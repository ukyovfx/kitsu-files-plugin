"""Google Drive v3 adapter around an injected transport and ephemeral token source."""

from dataclasses import dataclass
from hashlib import sha256
import json
from itertools import chain
from typing import BinaryIO, Mapping
from uuid import uuid4

from .base import AmbiguousOutcome, Listing, PermissionFailure, StorageCapabilities, StorageObject


API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveConfig:
    account_mode: str
    allowed_root_id: str
    drive_id: str | None
    owner_subject: str | None


class GoogleDriveStorageAdapter:
    def __init__(self, config: DriveConfig, token_provider, transport):
        if config.account_mode not in ("my_drive", "shared_drive"):
            raise ValueError("Unsupported Drive account mode")
        if config.account_mode == "shared_drive" and not config.drive_id:
            raise ValueError("Shared Drive configuration requires a drive ID")
        if config.account_mode == "my_drive" and not config.owner_subject:
            raise ValueError("My Drive configuration requires its owner subject")
        self.config = config
        self.token_provider = token_provider
        self.transport = transport
        self._root_verified = False

    def _request(self, method, url, *, params=None, json=None, data=None, headers=None):
        token = self.token_provider()
        if not token:
            raise PermissionFailure("Drive credential is unavailable")
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {token}"
        try:
            return self.transport.request(method, url, params=params, json=json, data=data, headers=request_headers)
        except Exception as exc:
            raise AmbiguousOutcome("Drive transport outcome is unknown") from exc

    def reserve_id(self, scope_key):
        response = self._request("GET", f"{API}/files/generateIds", params={"count": 1, "space": "drive", "type": "files"})
        if response.status_code != 200 or len(response.json().get("ids", [])) != 1:
            raise AmbiguousOutcome("Could not reserve a Drive file ID")
        return response.json()["ids"][0]

    def capabilities(self, root_id):
        if root_id != self.config.allowed_root_id:
            raise PermissionFailure("Requested root differs from configured root")
        if self.config.account_mode == "my_drive":
            account = self._request("GET", f"{API}/about", params={"fields": "user(permissionId,emailAddress)"})
            if account.status_code != 200:
                raise PermissionFailure("Drive account identity could not be verified")
            user = account.json().get("user", {})
            if self.config.owner_subject not in (user.get("permissionId"), user.get("emailAddress")):
                raise PermissionFailure("Drive token belongs to a different My Drive owner")
        response = self._request("GET", f"{API}/files/{root_id}", params={"supportsAllDrives": "true", "fields": "id,name,mimeType,driveId,parents,trashed,capabilities(canAddChildren)"})
        if response.status_code != 200:
            raise PermissionFailure("Configured Drive root is not readable")
        data = response.json()
        if data.get("id") != root_id or data.get("trashed") or data.get("mimeType") != FOLDER_MIME:
            raise PermissionFailure("Configured root is not an accessible folder")
        drive_id = data.get("driveId")
        if self.config.account_mode == "shared_drive" and drive_id != self.config.drive_id:
            raise PermissionFailure("Root belongs to a different Shared Drive")
        self._root_verified = True
        return StorageCapabilities(
            can_add_children=bool(data.get("capabilities", {}).get("canAddChildren")),
            root_id=root_id,
            drive_id=drive_id,
            shared_drive=self.config.account_mode == "shared_drive",
            root_visible=True,
            root_ancestors=tuple(data.get("parents", ())) + (root_id,),
        )

    def create_folder(self, parent_id: str, reserved_id: str, correlation: Mapping[str, str]):
        if not self._root_verified:
            raise PermissionFailure("Allowed root must be verified before create")
        self._assert_parent_within_root(parent_id)
        body = {
            "id": reserved_id,
            "name": correlation.get("display_name", "VFX Files"),
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
            "appProperties": {key: value for key, value in correlation.items() if key != "display_name"},
        }
        response = self._request("POST", f"{API}/files", params={"supportsAllDrives": "true", "fields": "id,name,mimeType,parents,driveId,trashed"}, json=body)
        if response.status_code == 409:
            existing = self.get(reserved_id)
            if existing and existing.parent_id == parent_id and existing.mime_type == FOLDER_MIME:
                return existing
            raise AmbiguousOutcome("Reserved Drive ID exists with conflicting metadata")
        if response.status_code not in (200, 201):
            raise AmbiguousOutcome("Drive folder create result is not confirmed")
        return self._object(response.json())

    def upload(self, source: BinaryIO, reserved_id: str, size: int, sha256_hex: str, parent_id: str):
        if not self._root_verified:
            raise PermissionFailure("Allowed root must be verified before upload")
        self._assert_parent_within_root(parent_id)
        if not source.seekable():
            raise ValueError("Drive upload source must be a seekable durable staging stream")
        position = source.tell()
        digest = sha256()
        counted = 0
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            counted += len(chunk)
            digest.update(chunk)
        source.seek(position)
        if counted != size or digest.hexdigest() != sha256_hex:
            raise ValueError("Upload source does not match expected size and SHA-256")
        boundary = f"vfx-files-{uuid4().hex}"
        metadata = json.dumps({"id": reserved_id, "parents": [parent_id]}).encode("utf-8")
        prefix = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
        )
        suffix = f"\r\n--{boundary}--\r\n".encode()

        def media_chunks():
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

        body = chain((prefix,), media_chunks(), (suffix,))
        response = self._request(
            "POST",
            f"{UPLOAD_API}/files",
            params={"uploadType": "multipart", "supportsAllDrives": "true", "fields": "id,name,mimeType,parents,driveId,size,md5Checksum,trashed"},
            data=body,
            headers={
                "Content-Type": f"multipart/related; boundary={boundary}",
                "Content-Length": str(len(prefix) + size + len(suffix)),
            },
        )
        if response.status_code == 409:
            existing = self.get(reserved_id)
            if existing and self.verify(reserved_id, size, sha256_hex):
                return existing
            raise AmbiguousOutcome("Reserved artifact ID exists with unverified bytes")
        if response.status_code not in (200, 201):
            raise AmbiguousOutcome("Drive upload result is not confirmed")
        return self._object(response.json())

    def get(self, object_id: str):
        if not self._root_verified:
            raise PermissionFailure("Allowed root must be verified before lookup")
        response = self._request("GET", f"{API}/files/{object_id}", params={"supportsAllDrives": "true", "fields": "id,name,mimeType,parents,driveId,size,md5Checksum,trashed"})
        if response.status_code == 404:
            raise AmbiguousOutcome("Drive 404 does not prove absence or distinguish inaccessible objects")
        if response.status_code in (401, 403):
            raise PermissionFailure("Drive object is inaccessible")
        if response.status_code != 200:
            raise AmbiguousOutcome("Drive lookup result is not confirmed")
        return self._object(response.json())

    def verify(self, object_id: str, size: int, sha256_hex: str):
        obj = self.get(object_id)
        if obj is None or obj.trashed or obj.size != size:
            return False
        # Drive exposes md5Checksum, not SHA-256. A byte readback is required for SHA-256 proof.
        response = self._request("GET", f"{API}/files/{object_id}", params={"alt": "media", "supportsAllDrives": "true"})
        if response.status_code != 200:
            return False
        digest = sha256()
        count = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                count += len(chunk)
                digest.update(chunk)
        return count == size and digest.hexdigest() == sha256_hex

    def download_to(self, object_id: str, destination: BinaryIO) -> tuple[int, str]:
        if not self._root_verified:
            raise PermissionFailure("Allowed root must be verified before download")
        obj = self.get(object_id)
        if obj is None or obj.trashed or obj.parent_id is None:
            raise PermissionFailure("Drive object is unavailable")
        self.assert_parent_within_root(obj.parent_id)
        response = self._request("GET", f"{API}/files/{object_id}", params={"alt": "media", "supportsAllDrives": "true"})
        if response.status_code in (401, 403):
            raise PermissionFailure("Drive object is not readable")
        if response.status_code != 200:
            raise AmbiguousOutcome("Drive download result is not confirmed")
        digest = sha256()
        count = 0
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                destination.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        if obj.size is not None and count != obj.size:
            raise AmbiguousOutcome("Drive download byte count differs from metadata")
        return count, digest.hexdigest()

    def list_children(self, parent_id: str, page_token: str | None = None):
        if not self._root_verified:
            raise PermissionFailure("Allowed root must be verified before listing")
        params = {
            "q": f"'{parent_id}' in parents and trashed = false",
            "pageSize": 1000,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,parents,driveId,size,trashed)",
        }
        if self.config.account_mode == "shared_drive":
            params.update({"corpora": "drive", "driveId": self.config.drive_id})
        if page_token:
            params["pageToken"] = page_token
        response = self._request("GET", f"{API}/files", params=params)
        if response.status_code != 200:
            raise AmbiguousOutcome("Drive list result is not confirmed")
        data = response.json()
        from .base import Listing

        objects = tuple(self._object(obj) for obj in data.get("files", ()))
        # `complete` represents Drive's incompleteSearch flag only; a next page
        # token means more results remain and must be followed by the caller.
        return Listing(objects, complete=not data.get("incompleteSearch", False), next_page_token=data.get("nextPageToken"))

    def assert_parent_within_root(self, parent_id: str):
        current = parent_id
        visited = set()
        for _ in range(64):
            if current == self.config.allowed_root_id:
                return
            if current in visited:
                break
            visited.add(current)
            item = self.get(current)
            if item is None or item.trashed or item.mime_type != FOLDER_MIME:
                break
            current = item.parent_id
            if current is None:
                break
        raise PermissionFailure("Target parent is not proven beneath the allowed root")

    _assert_parent_within_root = assert_parent_within_root

    @staticmethod
    def _object(data):
        return StorageObject(
            object_id=data["id"],
            parent_id=(data.get("parents") or [None])[0],
            size=int(data["size"]) if data.get("size") is not None else 0,
            sha256=None,
            drive_id=data.get("driveId"),
            mime_type=data.get("mimeType"),
            trashed=bool(data.get("trashed", False)),
            name=data.get("name"),
        )
