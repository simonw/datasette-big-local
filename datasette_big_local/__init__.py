from cachetools import TTLCache
from datasette import hookimpl
from datasette.database import Database
from datasette.utils.asgi import Response
import base64
import html
import httpx
import pathlib
import csv
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
            await get_project(project_id, remember_token)
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


async def get_project(project_id, remember_token, files=False):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.biglocalnews.org/graphql",
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
    import json

    print(json.dumps(project, indent=4))
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


async def open_project_file(project_id, filename, remember_token):
    graphql_endpoint = "https://api.biglocalnews.org/graphql"
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
            project_id, filename, remember_token
        )
    except OpenError as e:
        return Response.html(
            "Could not open file: {}".format(html.escape(str(e))), status=400
        )

    # uri is valid, do we have the table already?
    table_name = alnum_encode(filename)

    if not await db.table_exists(table_name):
        # Fetch the CSV
        async with httpx.AsyncClient() as client:
            r = await client.get(uri)
            if r.status_code != 200:
                return Response.html("Error fetching CSV")

        def import_csv(raw_conn):
            conn = sqlite_utils.Database(raw_conn)
            tracker = TypeTracker()
            table = conn[table_name]
            rows = list(csv.DictReader(r.iter_lines()))
            table.insert_all(tracker.wrap(rows))
            types = tracker.types
            if not all(v == "text" for v in types.values()):
                # Transform!
                table.transform(types=types)

        # Import that CSV
        await db.execute_write_fn(import_csv, block=True)

    response = Response.redirect("/{}/{}".format(project_uuid, table_name))

    # Set a cookie so that the user can access this database in future
    # They might be signed in already
    if request.actor and request.actor["token"] == remember_token:
        pass
    else:
        # Look up user and set cookie
        actor = await get_big_local_user(remember_token)
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


async def get_big_local_user(remember_token):
    graphql_endpoint = "https://api.biglocalnews.org/graphql"
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
        actor = await get_big_local_user(remember_token)
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
        actor = await get_big_local_user(remember_token)
        if not actor:
            return Response.html("<h1>Invalid token</h1>", status=403)
        actor["token"] = remember_token
        should_set_cookie = True

    # Can the actor access the project?
    try:
        project = await get_project(project_id, actor["token"], True)
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
