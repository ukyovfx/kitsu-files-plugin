"""Synthetic fault-injection tests for the Drive root-scope guard.

`GoogleDriveStorageAdapter.assert_parent_within_root` is the guard that keeps a
Production from writing outside its configured Drive root. It is invoked by
`create_folder`, `upload` and `download_to`, but it had no direct test coverage.
All provider IO here is a synthetic in-memory transport; no real Drive is used.
"""

import pytest

from plugin.storage.base import AmbiguousOutcome, PermissionFailure
from plugin.storage.google_drive import DriveConfig, FOLDER_MIME, GoogleDriveStorageAdapter


class Response:
    def __init__(self, status_code, data=None, content=b"", headers=None):
        self.status_code = status_code
        self._data = data
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._data

    def iter_content(self, chunk_size):
        yield self.content


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def synthetic_config():
    return DriveConfig(
        account_mode="shared_drive",
        allowed_root_id="root-1",
        drive_id="drive-1",
        owner_subject=None,
    )


def synthetic_drive(transport):
    return GoogleDriveStorageAdapter(synthetic_config(), lambda: "synthetic-token", transport)


def folder(object_id, parents, *, trashed=False, mime_type=FOLDER_MIME):
    return {"id": object_id, "mimeType": mime_type, "trashed": trashed, "parents": list(parents)}


def root_capability_response():
    return Response(
        200,
        {
            "id": "root-1",
            "mimeType": FOLDER_MIME,
            "trashed": False,
            "driveId": "drive-1",
            "parents": [],
            "capabilities": {"canAddChildren": True},
        },
    )


def verified_drive(transport):
    adapter = synthetic_drive(transport)
    adapter.capabilities("root-1")
    return adapter


def fetch_targets(transport):
    """Object ids requested by the ancestry walk, excluding the root check."""
    return [call[1].rsplit("/", 1)[-1] for call in transport.calls[1:]]


def test_root_scope_guard_grants_the_configured_root_without_provider_io():
    transport = FakeTransport([])

    synthetic_drive(transport).assert_parent_within_root("root-1")

    assert transport.calls == []


def test_root_scope_guard_grants_a_descendant_beneath_the_root():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, folder("grandchild-1", ["child-1"])),
            Response(200, folder("child-1", ["root-1"])),
        ]
    )
    adapter = verified_drive(transport)

    adapter.assert_parent_within_root("grandchild-1")

    assert fetch_targets(transport) == ["grandchild-1", "child-1"]


def test_root_scope_guard_denies_a_parent_that_never_reaches_the_root():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, folder("outside-2", ["outside-1"])),
            Response(200, folder("outside-1", [])),
        ]
    )
    adapter = verified_drive(transport)

    with pytest.raises(PermissionFailure, match="not proven beneath the allowed root"):
        adapter.assert_parent_within_root("outside-2")


def test_root_scope_guard_stops_on_a_cycle_instead_of_looping():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, folder("loop-a", ["loop-b"])),
            Response(200, folder("loop-b", ["loop-a"])),
        ]
    )
    adapter = verified_drive(transport)

    with pytest.raises(PermissionFailure, match="not proven beneath the allowed root"):
        adapter.assert_parent_within_root("loop-a")

    assert fetch_targets(transport) == ["loop-a", "loop-b"]


def test_root_scope_guard_stops_after_a_bounded_number_of_hops():
    class EndlessAncestors:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            object_id = url.rsplit("/", 1)[-1]
            if object_id == "root-1":
                return root_capability_response()
            return Response(200, folder(object_id, ["parent-of-" + object_id]))

    transport = EndlessAncestors()
    adapter = verified_drive(transport)

    with pytest.raises(PermissionFailure, match="not proven beneath the allowed root"):
        adapter.assert_parent_within_root("start-1")

    # A hostile or corrupt ancestry must not walk forever; the guard is bounded.
    assert len(transport.calls) - 1 == 64


@pytest.mark.parametrize(
    "ancestor",
    [
        folder("trashed-ancestor", ["root-1"], trashed=True),
        folder("document-ancestor", ["root-1"], mime_type="application/pdf"),
        folder("detached-ancestor", []),
    ],
    ids=["trashed", "not-a-folder", "detached"],
)
def test_root_scope_guard_denies_unusable_ancestors(ancestor):
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, folder("target-1", [ancestor["id"]])),
            Response(200, ancestor),
        ]
    )
    adapter = verified_drive(transport)

    with pytest.raises(PermissionFailure, match="not proven beneath the allowed root"):
        adapter.assert_parent_within_root("target-1")


def test_root_scope_guard_cannot_prove_ancestry_before_root_verification():
    transport = FakeTransport([])

    with pytest.raises(PermissionFailure):
        synthetic_drive(transport).assert_parent_within_root("child-1")

    assert transport.calls == []


def test_root_scope_guard_classifies_an_unreadable_ancestor_as_ambiguous():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(404, {"error": {"message": "synthetic-provider-detail"}}),
        ]
    )
    adapter = verified_drive(transport)

    # A provider 404 cannot prove absence, so the outcome stays ambiguous: the
    # durable job reconciles or retries instead of treating it as a denial.
    with pytest.raises(AmbiguousOutcome):
        adapter.assert_parent_within_root("unknown-1")


@pytest.mark.parametrize("operation", ["create_folder", "upload"])
def test_root_scope_guard_denies_out_of_root_writes_before_provider_write_io(operation):
    import hashlib
    import io

    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, folder("outside-1", [])),
        ]
    )
    adapter = verified_drive(transport)
    source = io.BytesIO(b"synthetic-artifact")

    with pytest.raises(PermissionFailure, match="not proven beneath the allowed root"):
        if operation == "create_folder":
            adapter.create_folder("outside-1", "reserved-folder", {"display_name": "Synthetic"})
        else:
            adapter.upload(
                source,
                "reserved-artifact",
                len(b"synthetic-artifact"),
                hashlib.sha256(b"synthetic-artifact").hexdigest(),
                "outside-1",
            )

    assert [call[0] for call in transport.calls] == ["GET", "GET"]


def test_capabilities_refuses_another_root_without_provider_io():
    transport = FakeTransport([])
    adapter = synthetic_drive(transport)

    # Production isolation: a connection may only ever verify its own root.
    with pytest.raises(PermissionFailure, match="differs from configured root"):
        adapter.capabilities("root-2")

    assert transport.calls == []


@pytest.mark.parametrize(
    "payload",
    [{"ids": []}, {"ids": ["a", "b"]}, {"kind": "drive#generatedIds"}],
    ids=["empty", "too-many", "missing-key"],
)
def test_reserve_id_requires_exactly_one_provider_id(payload):
    transport = FakeTransport([Response(200, payload)])

    with pytest.raises(AmbiguousOutcome, match="Could not reserve a Drive file ID"):
        synthetic_drive(transport).reserve_id("synthetic-scope")
