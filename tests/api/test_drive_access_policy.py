"""M2: the Drive access status never claims safety it cannot verify."""

from uuid import uuid4

import pytest

pytest.importorskip("zou")

BASE = "/plugins/vfx_files"


def _client(app, person_id):
    from zou.app.models.person import Person
    from zou.app.services.persons_service import create_access_token_for_raw_person

    with app.app_context():
        token = create_access_token_for_raw_person(Person.query.filter_by(id=person_id).one())
    client = app.test_client()
    client.set_cookie(app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"), token)
    return client


def _states(payload):
    return {check["id"]: check["state"] for check in payload["checks"]}


def test_access_policy_reports_only_what_the_plugin_can_verify(authenticated_plugin_client, synthetic_upload_task, monkeypatch):
    app = authenticated_plugin_client.vfx_app
    production_id = synthetic_upload_task[0]
    client = authenticated_plugin_client

    monkeypatch.setitem(app.config, "VFX_FILES_STORAGE_MODE", "google_drive")
    real = client.get(f"{BASE}/productions/{production_id}/drive-access-policy")
    assert real.status_code == 200
    states = _states(real.json)
    assert real.json["viewing"] == "google_drive_acl" and real.json["account_mode"] == "shared_drive"
    assert states["plugin_creates_no_sharing"] == "verified" and states["no_byte_proxy"] == "verified"
    assert states["shared_drive_structure"] == "verified"
    # Google-side settings are never presented as verified or safe.
    for check_id in ("members_only_restriction", "external_accounts", "link_sharing_disabled",
                     "group_viewer_role", "credential_manager_role"):
        assert states[check_id] == "not_verified"
    text = real.get_data(as_text=True).lower()
    assert "secure" not in text and '"safe"' not in text

    monkeypatch.setitem(app.config, "VFX_FILES_STORAGE_MODE", "fake")
    fake = client.get(f"{BASE}/productions/{production_id}/drive-access-policy").json
    assert fake["viewing"] == "fake_storage_test" and _states(fake)["no_byte_proxy"] == "not_applicable"


def test_access_policy_requires_production_membership(authenticated_plugin_client, synthetic_upload_task):
    from zou.app.models.person import Person

    app = authenticated_plugin_client.vfx_app
    production_id = synthetic_upload_task[0]
    with app.app_context():
        from zou.app import db

        person = Person(first_name=f"Out{uuid4().hex[:8]}", last_name="Sider", role="user", active=True)
        db.session.add(person)
        db.session.commit()
        outsider_id = person.id
    assert _client(app, outsider_id).get(f"{BASE}/productions/{production_id}/drive-access-policy").status_code == 403
    assert authenticated_plugin_client.get(f"{BASE}/productions/not-a-uuid/drive-access-policy").status_code in (403, 404)


def test_access_checks_flag_my_drive_as_a_different_structure():
    from plugin.resources import drive_access_checks

    my_drive = {check["id"]: check for check in drive_access_checks(real_drive=True, account_mode="my_drive")}
    assert my_drive["shared_drive_structure"]["state"] == "attention"
    none_selected = {check["id"]: check for check in drive_access_checks(real_drive=True, account_mode=None)}
    assert none_selected["shared_drive_structure"]["state"] == "not_verified"
