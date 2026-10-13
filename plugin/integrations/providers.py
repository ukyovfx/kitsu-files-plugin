"""Strict, secret-free response builders for KitsuSync provider endpoints."""

from uuid import UUID


class ProviderContractError(Exception):
    def __init__(self, status: int, code: str, field: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.field = field


_TEMPLATE_CATALOG = {
    "production": ("vfx-files.production.default", "Production folder", "{production}", ["production"]),
    "asset": ("vfx-files.asset.default", "Asset folder", "{asset}", ["asset"]),
    "sequence": ("vfx-files.sequence.default", "Sequence folder", "{sequence}", ["sequence"]),
    "shot": ("vfx-files.shot.default", "Shot folder", "{shot}", ["shot"]),
    "task": ("vfx-files.task.default", "Task folder", "{task}", ["task"]),
}


def templates_payload(scope: str) -> dict:
    """Return the immutable v1 default catalog item for one exact scope."""
    if scope not in _TEMPLATE_CATALOG:
        raise ProviderContractError(400, "invalid_event", "scope")
    template_id, label, pattern, tokens = _TEMPLATE_CATALOG[scope]
    return {
        "contract_version": "1.0",
        "templates": [{
            "id": template_id,
            "revision": 1,
            "scope": scope,
            "label": label,
            "pattern": pattern,
            "available_tokens": list(tokens),
        }],
    }


def connection_payload(connection) -> dict:
    """Serialize only contract-approved connection identity and state fields."""
    account_mode = connection.account_mode
    if account_mode not in ("my_drive", "shared_drive"):
        raise ProviderContractError(503, "connection_not_ready")
    configured_identity = (
        bool(connection.owner_subject) if account_mode == "my_drive"
        else bool(connection.drive_id)
    )
    state = "connected" if connection.enabled and configured_identity else (
        "error" if connection.enabled else "not_configured"
    )
    payload = {
        "contract_version": "1.0",
        "id": str(UUID(str(connection.id))),
        "account_mode": account_mode,
        "state": state,
    }
    if account_mode == "shared_drive":
        payload["drive_id"] = connection.drive_id
    return payload


def root_verification_payload(
    *, connection_id, account_mode: str, root_id: str, can_add_children: bool,
    owner_subject: str | None, drive_id: str | None = None,
) -> dict:
    """Build a verified result only when account identity evidence is present."""
    if account_mode == "my_drive":
        if not isinstance(owner_subject, str) or not owner_subject or len(owner_subject) > 255:
            raise ProviderContractError(403, "root_unverified")
        verified_drive_id = None
    elif account_mode == "shared_drive":
        if owner_subject is not None or not isinstance(drive_id, str) or not drive_id:
            raise ProviderContractError(403, "root_unverified")
        verified_drive_id = drive_id
    else:
        raise ProviderContractError(400, "invalid_event", "account_mode")
    if not isinstance(root_id, str) or not root_id or len(root_id) > 255:
        raise ProviderContractError(400, "invalid_event", "root_id")
    return {
        "contract_version": "1.0",
        "connection_id": str(UUID(str(connection_id))),
        "account_mode": account_mode,
        "root_id": root_id,
        "drive_id": verified_drive_id,
        "state": "verified",
        "capabilities": {"can_read": True, "can_add_children": bool(can_add_children)},
        "owner_subject": owner_subject,
    }
