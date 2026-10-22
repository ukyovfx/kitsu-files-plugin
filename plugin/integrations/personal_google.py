"""Authorization boundary and read-only reader for a user's own Google grant.

M3 stops at the boundary: the personal grant can only be turned into a `PersonalDriveReader`, which has no write,
move, delete or share operation and issues HTTP GET only. It is intentionally not a StorageAdapter, so it cannot be
handed to Production storage code (`resolve_storage_adapter` also refuses it). The `drive.file` scope can include
write capability at the Google API level; this class is how the Plugin refuses to use it.
M4 (Picker import) builds on `open_personal_source`.
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import BinaryIO
from uuid import UUID

from .user_oauth import (
    POLICY_ALLOWED,
    _models,
    build_user_drive_token_provider,
    get_personal_google_policy,
)

API = "https://www.googleapis.com/drive/v3"
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_FIELDS = "id,name,mimeType,size,md5Checksum,trashed,parents,driveId"


class PersonalSourceDenied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PersonalDriveReader:
    """GET-only access to files visible to one user's grant. Never a Production storage adapter."""

    personal_read_only = True  # checked by storage.base.resolve_storage_adapter

    def __init__(self, token_provider, transport):
        self._token_provider = token_provider
        self._transport = transport

    def _get(self, path, params):
        from ..storage.base import PermissionFailure, RetryableStorageFailure

        for attempt in (0, 1):
            token = self._token_provider()
            response = self._transport.request(
                "GET", f"{API}{path}", params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt == 0:
                refresh = getattr(self._token_provider, "refresh_after_unauthorized", None)
                if refresh is None:
                    break
                refresh(token)
                continue
            if response.status_code in (401, 403, 404):
                raise PermissionFailure("Personal Drive object is not accessible")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                raise RetryableStorageFailure(30, "Drive requested a retry")
            return response
        raise PermissionFailure("Personal Drive grant was rejected")

    @staticmethod
    def _checked(file_id):
        if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
            raise PersonalSourceDenied("invalid_drive_object")
        return file_id

    def get_metadata(self, file_id):
        file_id = self._checked(file_id)
        response = self._get(f"/files/{file_id}", {"supportsAllDrives": "true", "fields": _FIELDS})
        if response.status_code != 200:
            raise PersonalSourceDenied("drive_object_unavailable")
        data = response.json()
        if data.get("id") != file_id or data.get("trashed"):
            raise PersonalSourceDenied("drive_object_unavailable")
        return data

    def download_to(self, file_id, destination: BinaryIO):
        file_id = self._checked(file_id)
        response = self._get(f"/files/{file_id}", {"alt": "media", "supportsAllDrives": "true"})
        if response.status_code != 200:
            raise PersonalSourceDenied("drive_object_unavailable")
        digest, count = sha256(), 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                destination.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        return count, digest.hexdigest()


def open_personal_source(session_factory, *, actor_id, production_id, settings, extensions, transport):
    """Return a read-only reader for this user in this Production, or raise `PersonalSourceDenied`.

    Re-checked on every call (never cached): the user must still be an active Production member, the Production must
    still allow personal Google use, and the user's own grant must be active.
    """
    from sqlalchemy import select
    from zou.app.models.person import Person
    from zou.app.models.project import ProjectPersonLink

    try:
        actor_id, production_id = UUID(str(actor_id)), UUID(str(production_id))
    except (TypeError, ValueError):
        raise PersonalSourceDenied("invalid_scope") from None
    with session_factory() as session:
        person = session.get(Person, actor_id)
        if person is None or not getattr(person, "active", True):
            raise PersonalSourceDenied("actor_unavailable")
        global_role = getattr(person.role, "code", person.role)
        membership = session.scalar(select(ProjectPersonLink).where(
            ProjectPersonLink.project_id == production_id, ProjectPersonLink.person_id == actor_id))
        if membership is None and global_role != "admin":
            raise PersonalSourceDenied("not_a_production_member")
        if get_personal_google_policy(session, production_id) != POLICY_ALLOWED:
            raise PersonalSourceDenied("personal_google_blocked")
        credential = session.get(_models().GoogleUserCredential, actor_id)
        if credential is None or credential.state != "active":
            raise PersonalSourceDenied("personal_google_not_linked")
    provider = build_user_drive_token_provider(session_factory, actor_id, settings, extensions, transport=transport)
    return PersonalDriveReader(provider, transport)
