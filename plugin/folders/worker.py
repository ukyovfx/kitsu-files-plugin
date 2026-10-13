"""Durable worker handler for template-driven Task folder creation."""

from uuid import UUID

from sqlalchemy import select

from ..jobs.queue import JobResult
from ..models import FolderBinding, SharedTemplateConfiguration
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
            scope = f"production:{production_id}:task:{task_id}"
            with session_factory() as session:
                configuration = session.scalar(select(SharedTemplateConfiguration).where(
                    SharedTemplateConfiguration.id == configuration_id,
                    SharedTemplateConfiguration.production_id == production_id,
                ))
                selected_connection_id = configuration.storage_connection_id if configuration else None
                existing = session.scalar(select(FolderBinding.id).where(
                    FolderBinding.storage_connection_id == selected_connection_id,
                    FolderBinding.canonical_scope_key == scope,
                    FolderBinding.state == "ready",
                )) if selected_connection_id else None
            provision_task_folders(
                session_factory,
                adapter,
                production_id=production_id,
                task_id=task_id,
                configuration_id=configuration_id,
                configuration_version=version,
            )
            if existing is None and selected_connection_id is not None:
                with session_factory.begin() as session:
                    from ..integrations.event_producers import append_new_task_folder_event

                    append_new_task_folder_event(
                        session, production_id=production_id, task_id=task_id,
                        selected_connection_id=selected_connection_id,
                        expected_activation_epoch=payload.get("event_activation_epoch"),
                    )
            return JobResult("completed")
        except (KeyError, ValueError, TypeError):
            return JobResult("failed", error_code="invalid_provision_payload")
        except FolderProvisioningError as error:
            if "reconciliation" in str(error).lower() or "ambiguous" in str(error).lower():
                return JobResult("reconcile_required", error_code="folder_outcome_requires_reconciliation")
            return JobResult("failed", error_code="folder_scope_unavailable")

    return handle
