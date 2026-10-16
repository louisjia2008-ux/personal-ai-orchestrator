"""Capture explicit current generation for pre-existing policy behavior tests.

Conflict/missing/stale-precondition tests construct their own captured payloads.
"""


def preconditions(view):
    return {
        "expected_revision": view.revision,
        "expected_store_id": view.store_id,
        "expected_process_epoch": view.process_epoch,
    }


def update_settings(service, payload):
    return service.update_scheduling_settings(
        {
            **preconditions(service.scheduling_settings_view()),
            **payload,
        }
    )


def update_project(service, project_id, payload):
    return service.set_project_scheduling_policy(
        project_id,
        {
            **preconditions(service.project_scheduling_policy_view(project_id)),
            **payload,
        },
    )
