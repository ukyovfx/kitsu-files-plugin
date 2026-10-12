"""Durable worker handler for template-driven Task folder creation."""

from uuid import UUID

from ..jobs.queue import JobResult
from .provisioning import provision_task_folders
from .service import FolderProvisioningError


def make_task_folder_handler(session_factory, adapter):
    def handle(job):
        try:
            payload = job.payload
            production_id = UUID(str(payload["production_id"]))
            task_id = UUID(str(payload["task_id"]))
            configuration_id = UUID(str(payload["configuration_id"]))
            version = payload["configuration_version"]
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                return JobResult("failed", error_code="invalid_template_version")
            provision_task_folders(
                session_factory,
                adapter,
                production_id=production_id,
                task_id=task_id,
                configuration_id=configuration_id,
                configuration_version=version,
            )
            return JobResult("completed")
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_provision_payload")
        except FolderProvisioningError as error:
            if "reconciliation" in str(error).lower() or "ambiguous" in str(error).lower():
                return JobResult("reconcile_required", error_code="folder_outcome_requires_reconciliation")
            return JobResult("failed", error_code="folder_scope_unavailable")

    return handle
