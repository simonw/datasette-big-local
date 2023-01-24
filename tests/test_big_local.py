from datasette.app import Datasette
import base64
import json
import pytest


@pytest.fixture
def non_mocked_hosts():
    return ["localhost"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ("logged_out", "logged_in_permission", "logged_in_no_permission")
)
async def test_database_permissions(httpx_mock, case):
    ds = Datasette()
    ds.add_memory_database("ff0150c6-b634-472a-81b2-ef2e0c01d224")
    if case == "logged_out":
        ds_cookies = {}
    else:
        actor = {"id": "1", "token": "abc", "display": "one"}
        ds_cookies = {"ds_actor": ds.sign({"a": actor}, "actor")}
        permission_response = {"node": None}
        if case == "logged_in_permission":
            permission_response = {"node": {"id": "...", "name": "Project"}}
        httpx_mock.add_response(
            url="https://api.biglocalnews.org/graphql",
            json={"data": permission_response},
        )

    response = await ds.client.get(
        "/ff0150c6-b634-472a-81b2-ef2e0c01d224",
        cookies=ds_cookies,
    )
    if case == "logged_out":
        assert response.status_code == 302
    else:
        if case == "logged_in_permission":
            assert response.status_code == 200
        elif case == "logged_in_no_permission":
            assert response.status_code == 302
        # Either way, should have been a GraphQL call
        request = httpx_mock.get_request()

        assert request.url == "https://api.biglocalnews.org/graphql"
        assert request.method == "POST"
        assert json.loads(request.read())["variables"]["id"] == base64.b64encode(
            "Project:ff0150c6-b634-472a-81b2-ef2e0c01d224".encode("utf-8")
        ).decode("utf-8")


@pytest.mark.asyncio
async def test_open_file(httpx_mock, tmpdir):
    ds = Datasette(
        metadata={
            "plugins": {
                "datasette-big-local": {
                    "root_dir": str(tmpdir),
                }
            }
        }
    )
    assert ds.databases.keys() == {"_internal", "_memory"}
    # First one is GraphQL to create a link to the file
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={
            "data": {
                "createFileDownloadUri": {
                    "ok": {"uri": "https://storage.googleapis.com/table.csv"},
                    "err": None,
                }
            }
        },
    )
    # Second is a HEAD request
    httpx_mock.add_response(
        method="HEAD",
        url="https://storage.googleapis.com/table.csv",
        headers={"ETag": "abc", "content-length": "11"},
    )
    # Third one is to download the file
    httpx_mock.add_response(
        method="GET",
        url="https://storage.googleapis.com/table.csv",
        content=b"a,b,c\n1,2,3",
    )
    # Fourth is to verify user to set a cookie
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={
            "data": {
                "user": {
                    # id, displayName, username, email
                    "id": "1",
                    "displayName": "one",
                    "username": "one",
                    "email": "one@example.com",
                }
            }
        },
    )
    # Fifth is that permission check
    httpx_mock.add_response(
        url="https://api.biglocalnews.org/graphql",
        json={"data": {"node": {"id": "...", "name": "Project"}}},
    )

    # Now do the POST
    response = await ds.client.post(
        "/-/big-local-open",
        data={
            "project_id": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
            "filename": "universities_final.csv",
            "remember_token": "5f31b602-123",
        },
    )
    graphql_request = httpx_mock.get_requests()[0]
    assert json.loads(graphql_request.read())["variables"] == {
        "input": {
            "fileName": "universities_final.csv",
            "projectId": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
        }
    }
    assert graphql_request.headers["cookie"] == "remember_token=5f31b602-123"

    expected_path = "/ff0150c6-b634-472a-81b2-ef2e0c01d224/universities_5f_final_2e_csv"
    assert response.status_code == 302
    assert response.headers["Location"] == expected_path

    # It should also set a cookie
    response.headers["set-cookie"].startswith("ds_actor")
    ds_actor = response.headers["set-cookie"].split("=")[1].split("; ")[0]

    # Grab the JSON and check that it worked
    response = await ds.client.get(
        expected_path + ".json?_shape=array",
        cookies={
            "ds_actor": ds_actor,
        },
    )
    assert response.status_code == 200
    assert response.json() == [{"rowid": 1, "a": 1, "b": 2, "c": 3}]
