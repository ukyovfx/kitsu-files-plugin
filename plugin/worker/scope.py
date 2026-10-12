"""Pure checks that bind durable jobs to their stored Kitsu scope."""


def upload_job_scope_matches(*, upload_task_id, upload_actor_id, task_project_id, connection_production_id, payload):
    return (
        task_project_id == connection_production_id
        and str(upload_task_id) == str(payload.get("task_id"))
        and str(upload_actor_id) == str(payload.get("actor_id"))
    )


def publish_job_scope_matches(*, task_id, created_by, payload):
    return (
        str(task_id) == str(payload.get("task_id"))
        and str(created_by) == str(payload.get("actor_id"))
    )
