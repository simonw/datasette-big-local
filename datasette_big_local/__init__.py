import graphlib
from http import cookies
from time import time
from datasette import hookimpl
from datasette.database import Database
from datasette.utils.asgi import Response
import base64
import html
import httpx
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


def open_project_file(project_id, filename, remember_token):
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
    response = httpx.post(
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
    head_response = httpx.head(uri)
    if head_response.status_code != 200:
        raise OpenError("File not found")
    return (
        uri,
        head_response.headers["etag"],
        int(head_response.headers["content-length"]),
    )


async def open_big_local(request, datasette):
    if request.method == "GET":
        return Response.html(
            """
        <form action="/-/open-big-local" method="POST">
            <p><label>Project ID: <input name="project_id" value="UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ="></label></p>
            <p><label>Filename: <input name="filename" value="universities_final.csv"></label></p>
            <p><label>remember_token: <input name="remember_token" value="***REMOVED***"></label></p>
            <p><input type="submit"></p>
        </form>
        """
        )
    post = await request.post_vars()
    print(post)
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

    # Use GraphQL to check permissions and get the signed URL for this resource
    try:
        uri, etag, length = open_project_file(project_id, filename, remember_token)
    except OpenError as e:
        return Response.html(
            "Could not open file: {}".format(html.escape(str(e))), status=400
        )

    print(uri, etag, length)

    # Turn project ID into a UUID
    project_uuid = base64.b64decode(project_id).decode("utf-8").split("Project:")[-1]

    # Create a database of that name if one does not exist already
    try:
        db = datasette.get_database(project_uuid)
    except KeyError:
        # Create empty file
        sqlite_utils.Database("{}.db".format(project_uuid)).vacuum()
        db = datasette.add_database(
            Database(datasette, path="{}.db".format(project_uuid), is_mutable=True)
        )
    # uri is valid, do we have the table already?
    table_name = alnum_encode(filename)

    if not await db.table_exists(table_name):
        # Fetch the CSV
        async with httpx.AsyncClient() as client:
            print("About to get", length)
            print(uri)
            r = await client.get(uri)
            print("got")
            if r.status_code != 200:
                return Response.html("Error fetching CSV")

        def import_csv(raw_conn):
            conn = sqlite_utils.Database(raw_conn)
            print("Here goes insert all")
            tracker = TypeTracker()
            table = conn[table_name]
            rows = list(csv.DictReader(r.iter_lines()))
            print("len rows = ", len(rows))
            table.insert_all(tracker.wrap(rows))
            print("Done insertall")
            types = tracker.types
            print("types = ", types)
            if not all(v == "text" for v in types.values()):
                # Transform!
                table.transform(types=types)

        # Import that CSV
        await db.execute_write_fn(import_csv, block=True)
    return Response.redirect("/{}/{}".format(project_uuid, table_name))


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


@hookimpl
def register_routes():
    return [
        (r"^/-/open-big-local$", open_big_local),
        (r"^/-/big-local-login$", big_local_login),
    ]


@hookimpl
def skip_csrf(scope):
    return scope["path"] == "/-/open-big-local"
