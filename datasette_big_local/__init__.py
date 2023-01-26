from xml.etree.ElementTree import QName
from cachetools import TTLCache
from datasette import hookimpl
from datasette.database import Database
from datasette.utils.asgi import Response
import asyncio
import base64
import html
import httpx
import pathlib

import functools
import uuid
import csv as csv_std
import datetime
import threading

import sqlite_utils
from sqlite_utils.utils import TypeTracker
from urllib.parse import urlencode
import re

ALLOWED = "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789"
split_re = re.compile("(_[0-9a-f]+_)")


@hookimpl
def forbidden(request, message):
    return Response.redirect("/-/big-local-login?=" + urlencode({"message": message}))


def get_cache(datasette):
    cache = getattr(datasette, "big_local_cache", None)
    if cache is None:
        datasette.big_local_cache = cache = TTLCache(maxsize=100, ttl=60 * 5)
    return cache


@hookimpl
def permission_allowed(datasette, actor, action, resource):
    async def inner():
        if action not in ("view-database", "execute-sql"):
            # No opinion
            return
        if resource.startswith("_"):
            # _internal / _memory etc
            return
        if not actor:
            return False
        cache = get_cache(datasette)
        actor_id = actor["id"]
        database_name = resource
        # Check cache to see if actor is allowed to access this database
        key = (actor_id, database_name)
        result = cache.get(key)
        if result is not None:
            return result
        # Not cached - hit GraphQL API and cache the result
        remember_token = actor["token"]

        # Figure out project ID from UUID database name
        project_id = project_uuid_to_id(database_name)

        try:
            await get_project(datasette, project_id, remember_token)
            result = True
        except (ProjectPermissionError, ProjectNotFoundError):
            result = False

        # Store in cache
        cache[key] = result
        return result

    return inner


class ProjectPermissionError(Exception):
    pass


class ProjectNotFoundError(Exception):
    pass


FILES = """
                            files(first:100) {
                                edges {
                                    node {
                                        name
                                        size
                                    }
                                }
                            }
"""


async def get_project(datasette, project_id, remember_token, files=False):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            get_graphql_endpoint(datasette),
            json={
                "variables": {"id": project_id},
                "query": """
                query Node($id: ID!) {
                    node(id: $id) {
                        ... on Project {
                            id
                            name
                            FILES
                        }
                    }
                }
                """.replace(
                    "FILES", FILES if files else ""
                ),
            },
            cookies={"remember_token": remember_token},
            timeout=30,
        )
    if response.status_code != 200:
        raise ProjectPermissionError(response.text)
    else:
        data = response.json()["data"]
        if data["node"] is None:
            raise ProjectNotFoundError("Project not found")
    project = data["node"]
    # Clean up the files nested data
    if files:
        files_edges = project.pop("files")
        project["files"] = [edge["node"] for edge in files_edges["edges"]]
    return project


def alnum_encode(s):
    encoded = []
    for char in s:
        if char in ALLOWED:
            encoded.append(char)
        else:
            encoded.append("_" + hex(ord(char))[2:] + "_")
    return "".join(encoded)


split_re = re.compile("(_[0-9a-f]+_)")


def alnum_decode(s):
    decoded = []
    for bit in split_re.split(s):
        if bit.startswith("_"):
            hexbit = bit[1:-1]
            decoded.append(chr(int(hexbit, 16)))
        else:
            decoded.append(bit)
    return "".join(decoded)


class OpenError(Exception):
    pass


def get_graphql_endpoint(datasette):
    plugin_config = datasette.plugin_config("datasette-big-local") or {}
    return plugin_config.get("graphql_url") or "https://api.biglocalnews.org/graphql"


async def open_project_file(datasette, project_id, filename, remember_token):
    graphql_endpoint = get_graphql_endpoint(datasette)
    body = {
        "operationName": "CreateFileDownloadURI",
        "variables": {
            "input": {
                "fileName": filename,
                "projectId": project_id,
            }
        },
        "query": """
        mutation CreateFileDownloadURI($input: FileURIInput!) {
            createFileDownloadUri(input: $input) {
                ok {
                    name
                    uri
                    __typename
                }
                err
                __typename
            }
        }
        """,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            graphql_endpoint,
            json=body,
            cookies={"remember_token": remember_token},
            timeout=30,
        )
        if response.status_code != 200:
            raise OpenError(response.text)
        data = response.json()["data"]
        if data["createFileDownloadUri"]["err"]:
            raise OpenError(data["createFileDownloadUri"]["err"])
        # We need to do a HEAD request because the GraphQL endpoint doesn't
        # check if the file exists, it just signs whatever filename we sent
        uri = data["createFileDownloadUri"]["ok"]["uri"]
        head_response = await client.head(uri)
        if head_response.status_code != 200:
            raise OpenError("File not found")
    return (
        uri,
        head_response.headers["etag"],
        int(head_response.headers["content-length"]),
    )


def project_id_to_uuid(project_id):
    return base64.b64decode(project_id).decode("utf-8").split("Project:")[-1]


def project_uuid_to_id(project_uuid):
    return base64.b64encode("Project:{}".format(project_uuid).encode("utf-8")).decode(
        "utf-8"
    )


def ensure_database(datasette, project_uuid):
    # Create a database of that name if one does not exist already
    try:
        db = datasette.get_database(project_uuid)
    except KeyError:
        plugin_config = datasette.plugin_config("datasette-big-local") or {}
        root_dir = pathlib.Path(plugin_config.get("root_dir") or ".")
        # Create empty file
        db_path = str(root_dir / "{}.db".format(project_uuid))
        sqlite_utils.Database(db_path).vacuum()
        db = datasette.add_database(Database(datasette, path=db_path, is_mutable=True))
    return db


async def big_local_open(request, datasette):
    if request.method == "GET":
        return Response.html(
            """
        <form action="/-/big-local-open" method="POST">
            <p><label>Project ID: <input name="project_id" value="UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ="></label></p>
            <p><label>Filename: <input name="filename" value="universities_final.csv"></label></p>
            <p><label>remember_token: <input name="remember_token" value=""></label></p>
            <p><input type="submit"></p>
        </form>
        """
        )
    post = await request.post_vars()
    bad_keys = [
        key for key in ("filename", "project_id", "remember_token") if not post.get(key)
    ]
    if bad_keys:
        return Response.html(
            "filename, project_id and remember_token POST variables are required",
            status=400,
        )

    filename = post["filename"]
    project_id = post["project_id"]
    remember_token = post["remember_token"]

    # Turn project ID into a UUID
    project_uuid = project_id_to_uuid(project_id)

    db = ensure_database(datasette, project_uuid)

    # Use GraphQL to check permissions and get the signed URL for this resource
    try:
        uri, etag, length = await open_project_file(
            datasette, project_id, filename, remember_token
        )
    except OpenError as e:
        return Response.html(
            "Could not open file: {}".format(html.escape(str(e))), status=400
        )

    # uri is valid, do we have the table already?
    table_name = alnum_encode(filename)

    if not await db.table_exists(table_name):
        await import_csv(db, uri, table_name)
        # Give it a moment to create the progress table and start running
        await asyncio.sleep(0.5)

    response = Response.redirect("/{}/{}".format(project_uuid, table_name))

    # Set a cookie so that the user can access this database in future
    # They might be signed in already
    if request.actor and request.actor["token"] == remember_token:
        pass
    else:
        # Look up user and set cookie
        actor = await get_big_local_user(datasette, remember_token)
        if not actor:
            return Response.redirect("/-/big-local-login?error=invalid_token")
        # Rename displayName to display
        actor["display"] = actor.pop("displayName")
        actor["token"] = remember_token
        response.set_cookie(
            "ds_actor",
            datasette.sign({"a": actor}, "actor"),
        )

    return response


async def get_big_local_user(datasette, remember_token):
    graphql_endpoint = get_graphql_endpoint(datasette)
    query = """
    query {
        user {
            id
            displayName
            username
            email
        }
    }
    """.strip()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            graphql_endpoint,
            json={"query": query},
            cookies={"remember_token": remember_token},
            timeout=30,
        )
    if response.status_code != 200:
        return None
    return response.json()["data"]["user"]


async def big_local_login(datasette, request):
    if request.method == "POST":
        post = await request.post_vars()
        remember_token = post.get("remember_token")
        if not remember_token:
            return Response.redirect("/-/big-local-login")
        # Check that the token is valid
        actor = await get_big_local_user(datasette, remember_token)
        if not actor:
            return Response.redirect("/-/big-local-login?error=invalid_token")
        # Rename displayName to display
        actor["display"] = actor.pop("displayName")
        actor["token"] = remember_token
        response = Response.redirect("/")
        response.set_cookie(
            "ds_actor",
            datasette.sign({"a": actor}, "actor"),
        )
        return response
    return Response.html(
        await datasette.render_template(
            "big_local_login.html",
            request=request,
        )
    )


async def big_local_project(datasette, request):
    if request.method == "GET":
        return Response.html(
            """
        <form action="/-/big-local-open" method="POST">
            <p><label>Project ID: <input name="project_id" value="UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ="></label></p>
            <p><label>Filename: <input name="filename" value="universities_final.csv"></label></p>
            <p><label>remember_token: <input name="remember_token" value=""></label></p>
            <p><input type="submit"></p>
        </form>
        """
        )
    post = await request.post_vars()
    bad_keys = [key for key in ("project_id", "remember_token") if not post.get(key)]
    if bad_keys:
        return Response.html(
            "project_id and remember_token POST variables are required",
            status=400,
        )

    project_id = post["project_id"]
    remember_token = post["remember_token"]

    actor = None
    should_set_cookie = False
    if request.actor and (request.actor["token"] == remember_token):
        # Does the token match the current actor's token?
        actor = request.actor
    else:
        # Check remember_token is for a valid actor
        actor = await get_big_local_user(datasette, remember_token)
        if not actor:
            return Response.html("<h1>Invalid token</h1>", status=403)
        actor["token"] = remember_token
        should_set_cookie = True

    # Can the actor access the project?
    try:
        project = await get_project(datasette, project_id, actor["token"], True)
    except (ProjectPermissionError, ProjectNotFoundError):
        return Response.html("<h1>Cannot access project</h1>", status=403)

    # Figure out UUID for project
    project_uuid = project_id_to_uuid(project_id)

    # Stash project files in the cache
    cache = get_cache(datasette)
    cache_key = "project-files-{}".format(project_id)
    cache[cache_key] = project["files"]

    # Ensure database for project exists
    ensure_database(datasette, project_uuid)

    # Redirect user
    response = Response.redirect("/{}".format(project_uuid))
    if should_set_cookie:
        response.set_cookie(
            "ds_actor",
            datasette.sign({"a": actor}, "actor"),
        )
    return response


@hookimpl
def extra_template_vars(datasette, view_name, database):
    if view_name == "database":
        cache = get_cache(datasette)
        cache_key = "project-files-{}".format(project_uuid_to_id(database))
        return {"available_files": cache.get(cache_key, [])}


@hookimpl
def register_routes():
    return [
        (r"^/-/big-local-open$", big_local_open),
        (r"^/-/big-local-project$", big_local_project),
        (r"^/-/big-local-login$", big_local_login),
    ]


@hookimpl
def skip_csrf(scope):
    return scope["path"] == "/-/big-local-open"


async def import_csv(db, url, table_name):
    task_id = str(uuid.uuid4())

    def insert_initial_record(conn):
        database = sqlite_utils.Database(conn)
        if "_import_progress_" not in database.table_names():
            database["_import_progress_"].create(
                {
                    "id": str,
                    "table": str,
                    "bytes_todo": int,
                    "bytes_done": int,
                    "rows_done": int,
                    "started": str,
                    "completed": str,
                },
                pk="id",
            )
        database["_import_progress_"].insert(
            {
                "id": task_id,
                "table": table_name,
                "bytes_todo": None,
                "bytes_done": 0,
                "rows_done": 0,
                "started": str(datetime.datetime.utcnow()),
                "completed": None,
            }
        )

    await db.execute_write_fn(insert_initial_record)

    # We run this in a thread to avoid blocking
    thread = threading.Thread(
        target=functools.partial(
            fetch_and_insert_csv_in_thread,
            task_id,
            url,
            db,
            table_name,
            asyncio.get_event_loop(),
        ),
        daemon=True,
    )
    thread.start()


BATCH_SIZE = 100


def fetch_and_insert_csv_in_thread(task_id, url, database, table_name, loop):
    bytes_todo = None
    bytes_done = 0
    tracker = TypeTracker()

    def stream_lines():
        nonlocal bytes_todo, bytes_done
        with httpx.stream("GET", url) as r:
            try:
                bytes_todo = int(r.headers["content-length"])
            except TypeError:
                bytes_todo = None
            for line in r.iter_lines():
                bytes_done += len(line)
                yield line

    reader = csv_std.reader(stream_lines())
    headers = next(reader)
    docs = (dict(zip(headers, row)) for row in reader)

    def update_progress(data):
        asyncio.ensure_future(
            database.execute_write_fn(
                lambda conn: sqlite_utils.Database(conn)["_import_progress_"].update(
                    task_id, data
                ),
                block=False,
            ),
            loop=loop,
        )

    def write_batch(docs):
        asyncio.ensure_future(
            database.execute_write_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].insert_all(
                    docs, alter=True
                ),
                block=False,
            ),
            loop=loop,
        )

    gathered = []
    i = 0
    for doc in tracker.wrap(docs):
        gathered.append(doc)
        i += 1
        if len(gathered) >= BATCH_SIZE:
            write_batch(gathered)
            gathered = []
            # Update progress table
            update_progress(
                {
                    "rows_done": i,
                    "bytes_todo": bytes_todo,
                    "bytes_done": bytes_done,
                }
            )

    if gathered:
        # Write any remaining rows
        write_batch(gathered)
        gathered = []

    # Mark as complete in the table
    update_progress(
        {
            "rows_done": i,
            "bytes_done": bytes_todo,
            "completed": str(datetime.datetime.utcnow()),
        }
    )

    # Update the table's schema types
    types = tracker.types
    if not all(v == "text" for v in types.values()):
        # Transform!
        asyncio.ensure_future(
            database.execute_write_fn(
                lambda conn: sqlite_utils.Database(conn)[table_name].transform(
                    types=types
                ),
                block=False,
            ),
            loop=loop,
        )


PROGRESS_BAR_JS = """
const PROGRESS_BAR_CSS = `
progress {
    -webkit-appearance: none;
    appearance: none;
    border: none;
    width: 100%;
    height: 2em;
    margin-top: 1em;
    margin-bottom: 1em;
}
progress::-webkit-progress-bar {
    background-color: #ddd;
}
progress::-webkit-progress-value {
    background-color: #124d77;
}
`;
(function() {
    // Add CSS
    const style = document.createElement("style");
    style.innerHTML = PROGRESS_BAR_CSS;
    document.head.appendChild(style);
    // Append progress bar
    const progress = document.createElement('progress');
    progress.setAttribute('value', 0);
    progress.innerHTML = 'Importing...';
    progress.style.display = 'none';
    const table = document.querySelector('table.rows-and-columns');
    table.parentNode.insertBefore(progress, table);
    console.log('progress', progress);

    // Figure out the polling URL
    let parts = location.href.split('/');
    let table_name = parts.pop();
    parts.push("_import_progress_.json");
    let pollUrl = parts.join('/') + (
        '?_col=bytes_todo&_col=bytes_done&table=' + table_name +
        '&_sort_desc=started&_shape=array&_size=1'
    );

    // Start polling
    let first = true;
    function pollNext() {
        fetch(pollUrl).then(r => r.json()).then(d => {
            let current = d[0].bytes_done;
            let total = d[0].bytes_todo;
            if (first) {
                progress.setAttribute('max', total);
                progress.style.display = 'block';
                first = false;
            }
            progress.setAttribute('value', current);
            if (current < total) {
                setTimeout(pollNext, 2000);
            } else {
                progress.parentNode.removeChild(progress);
            }
        });
    }
    pollNext();
})();
"""


@hookimpl
def extra_body_script(view_name):
    if view_name == "table":
        return PROGRESS_BAR_JS
