from dataclasses import dataclass
import hashlib


@dataclass(frozen=True)
class QCResult:
    passed: bool
    policy_id: str
    policy_sha256: str
    checks: dict


def run_basic_qc(*, policy_id: str, policy_sha256: str, byte_count: int, expected_size: int, artifact_sha256: str, readback_sha256: str, artifact_id: str | None, root_verified: bool) -> QCResult:
    hashes_well_formed = (
        isinstance(artifact_sha256, str)
        and isinstance(readback_sha256, str)
        and len(artifact_sha256) == 64
        and len(readback_sha256) == 64
        and all(ch in "0123456789abcdef" for ch in artifact_sha256 + readback_sha256)
    )
    checks = {
        "size_matches": byte_count == expected_size and byte_count > 0,
        "sha256_matches": hashes_well_formed and artifact_sha256 == readback_sha256,
        "artifact_has_stable_id": bool(artifact_id),
        "destination_root_verified": root_verified,
    }
    return QCResult(all(checks.values()), policy_id, policy_sha256, checks)
