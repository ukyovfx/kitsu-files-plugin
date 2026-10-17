"""Synthetic fault-injection tests for Drive integrity, retry hints and duplicates.

Covers the untested high-risk paths of `plugin.storage.google_drive`:
 * upload pre-flight integrity rejection before any provider write IO
 * `verify()` negative outcomes (trash/size/short readback/digest)
 * `download_to` metadata byte-count mismatch
 * duplicate reserved-folder reconciliation (idempotent retry of a repeated job)
 * `Retry-After` clamping and HTTP-date parsing for 429/5xx retries

Everything is a synthetic in-memory transport; no real Drive or Production is used.
"""

import email.utils
import hashlib
import io
from datetime import datetime, timedelta, timezone

import pytest

from plugin.storage.base import AmbiguousOutcome, PermissionFailure, RetryableStorageFailure
from plugin.storage.google_drive import DriveConfig, FOLDER_MIME, GoogleDriveStorageAdapter

PAYLOAD = b"synthetic-artifact-bytes"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


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


class UnseekableSource:
    def read(self, _size=-1):
        return b""

    def seek(self, _position):
        raise OSError("synthetic non-seekable stream")

    def seekable(self):
        return False


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


def artifact(object_id, *, size, parents=("root-1",), trashed=False):
    metadata = {"id": object_id, "mimeType": "application/pdf", "trashed": trashed, "parents": list(parents)}
    if size is not None:
        metadata["size"] = str(size)
    return metadata


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


def write_calls(transport):
    return [call for call in transport.calls if call[0] != "GET"]


@pytest.mark.parametrize(
    "size,sha256",
    [(len(PAYLOAD) + 1, PAYLOAD_SHA256), (len(PAYLOAD), "0" * 64), (0, PAYLOAD_SHA256)],
    ids=["short-size", "wrong-digest", "empty-size"],
)
def test_upload_rejects_a_source_that_does_not_match_size_and_digest(size, sha256):
    transport = FakeTransport([root_capability_response()])
    adapter = verified_drive(transport)

    with pytest.raises(ValueError, match="does not match expected size and SHA-256"):
        adapter.upload(io.BytesIO(PAYLOAD), "reserved-artifact", size, sha256, "root-1")

    # The pre-flight runs before any provider write IO, so no partial upload exists.
    assert write_calls(transport) == []


def test_upload_rejects_a_non_seekable_source_before_provider_io():
    transport = FakeTransport([root_capability_response()])
    adapter = verified_drive(transport)

    with pytest.raises(ValueError, match="seekable durable staging stream"):
        adapter.upload(UnseekableSource(), "reserved-artifact", len(PAYLOAD), PAYLOAD_SHA256, "root-1")

    assert write_calls(transport) == []


@pytest.mark.parametrize("file_name", ["", "   ", "x" * 1025], ids=["blank", "whitespace", "too-long"])
def test_upload_rejects_an_invalid_file_name_before_provider_io(file_name):
    transport = FakeTransport([root_capability_response()])
    adapter = verified_drive(transport)

    with pytest.raises(ValueError):
        adapter.upload(io.BytesIO(PAYLOAD), "reserved-artifact", len(PAYLOAD), PAYLOAD_SHA256, "root-1", file_name=file_name)

    assert write_calls(transport) == []


def test_upload_preflight_value_error_bounds_are_intact_for_a_valid_source():
    """The same source that failed a bad digest must upload once the expectation matches."""

    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("reserved-artifact", size=len(PAYLOAD))),
        ]
    )
    adapter = verified_drive(transport)

    uploaded = adapter.upload(io.BytesIO(PAYLOAD), "reserved-artifact", len(PAYLOAD), PAYLOAD_SHA256, "root-1")

    assert uploaded.object_id == "reserved-artifact"


def test_verify_is_false_for_trashed_missing_size_and_wrong_digest():
    trashed = FakeTransport([root_capability_response(), Response(200, artifact("obj-1", size=len(PAYLOAD), trashed=True))])
    assert verified_drive(trashed).verify("obj-1", len(PAYLOAD), PAYLOAD_SHA256) is False

    # A metadata size mismatch is rejected without downloading the bytes at all.
    mismatched = FakeTransport([root_capability_response(), Response(200, artifact("obj-1", size=len(PAYLOAD)))])
    assert verified_drive(mismatched).verify("obj-1", len(PAYLOAD) + 1, PAYLOAD_SHA256) is False
    assert len(mismatched.calls) == 2

    short = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("obj-1", size=len(PAYLOAD))),
            Response(200, content=PAYLOAD[:3]),
        ]
    )
    assert verified_drive(short).verify("obj-1", len(PAYLOAD), PAYLOAD_SHA256) is False

    corrupt = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("obj-1", size=len(PAYLOAD))),
            Response(200, content=b"z" * len(PAYLOAD)),
        ]
    )
    assert verified_drive(corrupt).verify("obj-1", len(PAYLOAD), PAYLOAD_SHA256) is False


def test_verify_reports_revocation_on_the_media_read_as_permission_failure():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("obj-1", size=len(PAYLOAD))),
            Response(403, {"error": {"message": "synthetic-provider-detail"}}),
        ]
    )

    with pytest.raises(PermissionFailure):
        verified_drive(transport).verify("obj-1", len(PAYLOAD), PAYLOAD_SHA256)


def test_verify_accepts_no_content_body_only_when_the_digest_matches():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("empty-1", size=0)),
            Response(200, content=b""),
        ]
    )

    assert verified_drive(transport).verify("empty-1", 0, hashlib.sha256(b"").hexdigest()) is True


def test_download_rejects_a_byte_count_that_differs_from_metadata():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("obj-1", size=len(PAYLOAD))),
            Response(200, content=PAYLOAD[:2]),
        ]
    )
    adapter = verified_drive(transport)
    destination = io.BytesIO()

    with pytest.raises(AmbiguousOutcome, match="byte count differs from metadata"):
        adapter.download_to("obj-1", destination)


@pytest.mark.parametrize("metadata", [{"trashed": True}, {"parents": []}], ids=["trashed", "detached"])
def test_download_refuses_an_unavailable_object(metadata):
    payload = {"id": "obj-1", "mimeType": "application/pdf", "size": str(len(PAYLOAD))}
    payload.update(metadata)
    transport = FakeTransport([root_capability_response(), Response(200, payload)])

    with pytest.raises(PermissionFailure, match="Drive object is unavailable"):
        verified_drive(transport).download_to("obj-1", io.BytesIO())


def test_download_tolerates_unknown_provider_size_and_returns_the_digest():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(200, artifact("obj-1", size=None)),
            Response(200, content=PAYLOAD),
        ]
    )
    adapter = verified_drive(transport)

    count, digest = adapter.download_to("obj-1", io.BytesIO())

    assert count == len(PAYLOAD)
    assert digest == PAYLOAD_SHA256


def test_duplicate_reserved_folder_is_reconciled_to_the_existing_object():
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(409, {"error": {"message": "synthetic-provider-detail"}}),
            Response(200, folder("reserved-folder", ["root-1"])),
        ]
    )
    adapter = verified_drive(transport)

    reconciled = adapter.create_folder("root-1", "reserved-folder", {"display_name": "Synthetic"})

    assert reconciled.object_id == "reserved-folder"
    assert reconciled.parent_id == "root-1"
    posted = write_calls(transport)[0][2]["json"]
    assert posted["id"] == "reserved-folder"
    assert posted["parents"] == ["root-1"]


@pytest.mark.parametrize(
    "existing",
    [folder("reserved-folder", ["other-parent"]), folder("reserved-folder", ["root-1"], mime_type="application/pdf")],
    ids=["conflicting-parent", "conflicting-mime"],
)
def test_duplicate_reserved_folder_with_conflicting_metadata_stays_ambiguous(existing):
    transport = FakeTransport(
        [
            root_capability_response(),
            Response(409, {"error": {"message": "synthetic-provider-detail"}}),
            Response(200, existing),
        ]
    )
    adapter = verified_drive(transport)

    with pytest.raises(AmbiguousOutcome, match="conflicting metadata"):
        adapter.create_folder("root-1", "reserved-folder", {"display_name": "Synthetic"})


@pytest.mark.parametrize(
    "header,expected",
    [
        ("0", 1),
        ("-5", 1),
        ("99999", 3600),
        ("11.4", 11),
        (None, 30),
        ("", 30),
        ("NaN", 30),
        ("Infinity", 30),
        ("not-a-date", 30),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 1),
    ],
    ids=["zero", "negative", "huge", "fractional", "absent", "blank", "nan", "infinity", "garbage", "http-date-past"],
)
def test_retry_delay_is_clamped_for_429_and_5xx(header, expected):
    headers = {} if header is None else {"Retry-After": header}
    transport = FakeTransport([Response(503, headers=headers)])

    with pytest.raises(RetryableStorageFailure) as failure:
        synthetic_drive(transport).reserve_id("synthetic-scope")

    assert failure.value.retry_after_seconds == expected


def test_retry_delay_parses_a_future_http_date_and_clamps_to_the_ceiling():
    retry_at = datetime.now(timezone.utc) + timedelta(hours=2)
    transport = FakeTransport([Response(429, headers={"Retry-After": email.utils.format_datetime(retry_at)})])

    with pytest.raises(RetryableStorageFailure) as failure:
        synthetic_drive(transport).reserve_id("synthetic-scope")

    assert failure.value.retry_after_seconds == 3600


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_every_transient_provider_status_becomes_a_durable_retry(status_code):
    transport = FakeTransport([Response(status_code, headers={})])

    with pytest.raises(RetryableStorageFailure) as failure:
        synthetic_drive(transport).reserve_id("synthetic-scope")

    assert failure.value.retry_after_seconds == 30


@pytest.mark.parametrize("status_code", [400, 404, 410])
def test_non_transient_provider_status_is_not_a_retry(status_code):
    transport = FakeTransport([Response(status_code, {"error": {"message": "synthetic-provider-detail"}})])

    with pytest.raises(AmbiguousOutcome):
        synthetic_drive(transport).reserve_id("synthetic-scope")
