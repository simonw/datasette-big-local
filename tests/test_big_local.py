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
