from datasette.app import Datasette
import base64
import json
import pathlib
import pytest


@pytest.fixture
def non_mocked_hosts():
    return ["localhost"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ("logged_out", "logged_in_permission", "logged_in_no_permission")
)
async def test_database_permissions(httpx_mock, scenario):
    ds = Datasette()
    ds.add_memory_database("ff0150c6-b634-472a-81b2-ef2e0c01d224")
    if scenario == "logged_out":
        ds_cookies = {}
    else:
        actor = {"id": "1", "token": "abc", "display": "one"}
        ds_cookies = {"ds_actor": ds.sign({"a": actor}, "actor")}
        permission_response = {"node": None}
        if scenario == "logged_in_permission":
            permission_response = {"node": {"id": "...", "name": "Project"}}
        httpx_mock.add_response(
            url="https://api.biglocalnews.org/graphql",
            json={"data": permission_response},
        )

    response = await ds.client.get(
        "/ff0150c6-b634-472a-81b2-ef2e0c01d224",
        cookies=ds_cookies,
    )
    if scenario == "logged_out":
        assert response.status_code == 302
    else:
        if scenario == "logged_in_permission":
            assert response.status_code == 200
        elif scenario == "logged_in_no_permission":
            assert response.status_code == 302
        # Either way, should have been a GraphQL call
        request = httpx_mock.get_request()

        assert request.url == "https://api.biglocalnews.org/graphql"
        assert request.method == "POST"
        assert json.loads(request.read())["variables"]["id"] == base64.b64encode(
            "Project:ff0150c6-b634-472a-81b2-ef2e0c01d224".encode("utf-8")
        ).decode("utf-8")


@pytest.mark.asyncio
async def test_open_file(httpx_mock, ds, tmpdir):
    expected_db_path = pathlib.Path(tmpdir) / "ff0150c6-b634-472a-81b2-ef2e0c01d224.db"
    assert not expected_db_path.exists()
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

    # And should have created that file
    assert expected_db_path.exists()


@pytest.fixture
def ds(tmpdir):
    return Datasette(
        metadata={
            "plugins": {
                "datasette-big-local": {
                    "root_dir": str(tmpdir),
                }
            }
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    (
        {
            "project_id": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
            "remember_token": "",
        },
        {},
        {
            "project_id": "",
            "remember_token": "123",
        },
    ),
)
async def test_big_local_project_bad_parameters(ds, data):
    response = await ds.client.post(
        "/-/big-local-project",
        data=data,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_big_local_project_no_permission(ds, httpx_mock):
    # Mock both GraphQL calls
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={
            "data": {
                "user": {
                    "id": "1",
                    "displayName": "one",
                    "username": "one",
                    "email": "one@example.com",
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={"data": {"node": None}},
    )
    # Try the action
    response = await ds.client.post(
        "/-/big-local-project",
        data={
            "project_id": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
            "remember_token": "123",
        },
    )
    assert response.status_code == 403
    assert "Cannot access project" in response.text


@pytest.mark.asyncio
async def test_big_local_project(ds, httpx_mock, tmpdir):
    # This one works, so lots of things to mock
    assert len(tmpdir.listdir()) == 0
    assert ds.databases.keys() == {"_internal", "_memory"}
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={
            "data": {
                "user": {
                    "id": "1",
                    "displayName": "one",
                    "username": "one",
                    "email": "one@example.com",
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.biglocalnews.org/graphql",
        json={
            "data": {
                "node": {
                    "files": {
                        "edges": [
                            {
                                "node": {
                                    "name": "universities_final.csv",
                                    "size": 180437.0,
                                }
                            }
                        ]
                    },
                    "id": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
                    "name": "universities-ppp",
                }
            }
        },
    )
    response = await ds.client.post(
        "/-/big-local-project",
        data={
            "project_id": "UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=",
            "remember_token": "123",
        },
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/ff0150c6-b634-472a-81b2-ef2e0c01d224"
    # Should also have set a cookie
    ds_actor = response.headers["set-cookie"].split("=")[1].split("; ")[0]
    assert ds.unsign(ds_actor, "actor") == {
        "a": {
            "id": "1",
            "displayName": "one",
            "username": "one",
            "email": "one@example.com",
            "token": "123",
        }
    }
    assert len(tmpdir.listdir()) == 1
    assert tmpdir.listdir()[0].basename == "ff0150c6-b634-472a-81b2-ef2e0c01d224.db"

    # Requesting that page should see a button to import that file
    response = await ds.client.get(
        "/ff0150c6-b634-472a-81b2-ef2e0c01d224",
        cookies={
            "ds_actor": ds_actor,
        },
    )
    assert response.status_code == 200
    assert "universities_final.csv" in response.text
