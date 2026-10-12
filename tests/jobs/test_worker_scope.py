from plugin.worker.scope import publish_job_scope_matches, upload_job_scope_matches


def test_upload_job_requires_same_task_artist_and_production():
    payload = {"task_id": "task-a", "actor_id": "artist-a"}
    assert upload_job_scope_matches(
        upload_task_id="task-a", upload_actor_id="artist-a", task_project_id="production-a",
        connection_production_id="production-a", payload=payload,
    )
    assert not upload_job_scope_matches(
        upload_task_id="task-a", upload_actor_id="artist-a", task_project_id="production-a",
        connection_production_id="production-b", payload=payload,
    )
    assert not upload_job_scope_matches(
        upload_task_id="task-a", upload_actor_id="artist-a", task_project_id="production-a",
        connection_production_id="production-a", payload={"task_id": "task-b", "actor_id": "artist-a"},
    )
    assert not upload_job_scope_matches(
        upload_task_id="task-a", upload_actor_id="artist-a", task_project_id="production-a",
        connection_production_id="production-a", payload={"task_id": "task-a", "actor_id": "artist-b"},
    )


def test_publish_job_requires_the_stored_task_and_creator():
    payload = {"task_id": "task-a", "actor_id": "supervisor-a"}
    assert publish_job_scope_matches(task_id="task-a", created_by="supervisor-a", payload=payload)
    assert not publish_job_scope_matches(task_id="task-b", created_by="supervisor-a", payload=payload)
    assert not publish_job_scope_matches(task_id="task-a", created_by="artist-b", payload=payload)
