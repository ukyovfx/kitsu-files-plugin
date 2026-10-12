import hashlib

from plugin.publishes.manifest import build_manifest, canonical_manifest_bytes, manifest_sha256
from plugin.publishes.qc import run_basic_qc


def test_manifest_hash_covers_exact_bytes():
    manifest = build_manifest(
        publish_id="publish-1",
        task_id="task-1",
        output_family_id="family-1",
        version=3,
        artifact_id="drive-object-1",
        byte_count=4,
        artifact_sha256=hashlib.sha256(b"data").hexdigest(),
    )
    exact = canonical_manifest_bytes(manifest)

    assert manifest_sha256(exact) == hashlib.sha256(exact).hexdigest()
    assert manifest_sha256(exact) != manifest_sha256(exact + b" ")


def test_qc_fails_when_external_readback_hash_differs():
    passed = run_basic_qc(
        policy_id="basic-1",
        policy_sha256="a" * 64,
        byte_count=4,
        expected_size=4,
        artifact_sha256=hashlib.sha256(b"data").hexdigest(),
        readback_sha256=hashlib.sha256(b"data").hexdigest(),
        artifact_id="drive-object-1",
        root_verified=True,
    )
    failed = run_basic_qc(
        policy_id="basic-1",
        policy_sha256="a" * 64,
        byte_count=4,
        expected_size=4,
        artifact_sha256=hashlib.sha256(b"data").hexdigest(),
        readback_sha256=hashlib.sha256(b"else").hexdigest(),
        artifact_id="drive-object-1",
        root_verified=True,
    )

    assert passed.passed
    assert not failed.passed
