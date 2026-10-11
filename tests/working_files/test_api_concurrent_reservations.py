from concurrent.futures import ThreadPoolExecutor
import hashlib
from uuid import uuid4

import pytest

pytest.importorskip("zou")


def test_concurrent_http_reservations_allocate_distinct_working_versions(
    authenticated_plugin_client, synthetic_upload_task
):
    app = authenticated_plugin_client.vfx_app
    _production_id, _entity_type, _task_type, _entity, task_id, _connection, _actor_id = synthetic_upload_task
    token = authenticated_plugin_client.vfx_files_token
    cookie_name = app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")
    route = f"/plugins/vfx_files/tasks/{task_id}/working-revisions"

    def reserve(filename, data):
        client = app.test_client()
        client.set_cookie(cookie_name, token)
        csrf = client.get("/plugins/vfx_files/csrf-token")
        assert csrf.status_code == 200
        response = client.post(
            route,
            json={"base_revision_id": None, "files": [{
                "name": filename,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }]},
            headers={
                "Origin": "https://kitsu.test",
                "X-VFX-Files-CSRF": csrf.json["csrf_token"],
                "Idempotency-Key": f"working:{uuid4()}",
            },
        )
        return response

    requests = [("parallel-a.nk", b"first"), ("parallel-b.nk", b"other")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda values: reserve(*values), requests))

    assert [response.status_code for response in responses] == [201, 201]
    assert sorted(response.json["version"] for response in responses) == [1, 2]
    assert len({response.json["id"] for response in responses}) == 2
    assert {response.json["version_label"] for response in responses} == {"w001", "w002"}
