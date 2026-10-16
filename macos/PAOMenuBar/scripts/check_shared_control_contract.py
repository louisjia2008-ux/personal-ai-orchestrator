#!/usr/bin/env python3
"""Portable fixture/source checks; NOT a Swift compile or a visual acceptance test."""

import json
import re
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "Tests/PAOControlKitTests/Fixtures/control"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def check_reports():
    for name in ("detail_verified", "continuation_verified", "detail_recoverable"):
        envelope = fixture(name)
        payload = envelope["payload"]
        report = payload["result"]
        assert envelope["protocol_version"] == 2
        assert report["schema_version"] == 1
        assert report["kind"] == "HOST_VERIFICATION_REPORT"
        assert report["binding"]["task_id"] == payload["task"]["task_id"]
        assert report["binding"]["store_id"] == envelope["store_id"]
        content = {key: value for key, value in report.items() if key != "result_id"}
        digest = sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()
        assert report["result_id"] == f"result-{digest}"
        assert "HOST_REPORT_ONLY" in report["limitations"]
        assert "result_json" not in json.dumps(report)
    payload = fixture("continuation_verified")["payload"]
    assert payload["continuation"]["manual_only"] is True
    assert payload["continuation"]["binding"] == payload["result"]["binding"]
    assert payload["continuation"]["result_id"] == payload["result"]["result_id"]
    assert payload["continuation"]["pending"]


def check_recovery():
    detail = fixture("detail_recoverable")["payload"]
    candidate = detail["recovery_candidate"]
    request = fixture("recovery_request")
    response = fixture("recovery_receipt")["payload"]
    for key in ("task_id", "task_state_version", "prior_dispatch_id"):
        assert candidate[key] == request[key] == response["recovery"][key]
    assert request["task_state_version"] == detail["task"]["state_version"]
    assert request["operation"] == "RECOVER"
    assert response["launch_performed"] is False
    assert response["next_step"] == "REVIEW_CURRENT_TASK_BEFORE_EXPLICIT_START"
    assert "execution_target_id" not in request
    assert "authority" not in request


def check_policies():
    global_policy = fixture("global_policy")
    project = fixture("project_policy")
    for key in ("revision", "store_id", "process_epoch"):
        assert re.fullmatch(r"[a-f0-9]{32}", global_policy[key])
        assert re.fullmatch(r"[a-f0-9]{32}", project[key])
    assert project["global_revision"] == global_policy["revision"]
    assert project["store_id"] == global_policy["store_id"]
    assert project["scheduling_policy"] is None
    assert project["policy_source"] == "GLOBAL"


def check_surface_contracts():
    views = ROOT / "Sources/PAOMenuBar"
    dashboard = (views / "DashboardView.swift").read_text()
    overview = dashboard[dashboard.index("private struct OverviewDashboard:") :]
    assert overview.index("OverviewActionQueue(") < overview.index("OverviewPolicyResources(")
    assert overview.index("OverviewPolicyResources(") < overview.index("MetricTile(")
    detail = (views / "Tasks/TaskDetailSurface.swift").read_text()
    assert detail.index("TaskResultSection(") < detail.index("TaskExecutionSection(")
    result = (views / "Tasks/TaskResultSection.swift").read_text()
    assert "outcome.matches(current)" in result
    assert "recoveryConfirmation = store.prepareRecovery()" in result
    assert "store.recoverTask(request)" in result
    assert "store.dispatch(" not in result
    core = ROOT / "Sources/PAOControlKit"
    store = (core / "OrchestratorStore.swift").read_text()
    recovery = store[
        store.index("public func prepareRecovery()") : store.index(
            "/// Internal: fetch provider-discovery"
        )
    ]
    assert "request.statusRequest" in recovery
    assert "client.dispatch(" not in recovery
    assert "request.matches(envelope, task: task)" in recovery
    assert "schedulingSettings?.processEpoch == request.processEpoch" in recovery
    keys = set(re.findall(r'"(control\.[A-Za-z_.]+)"', (core / "L10n.swift").read_text()))
    for language in ("en", "zh-Hans"):
        catalog = (core / f"Resources/{language}.lproj/Localizable.strings").read_text()
        catalog_keys = re.findall(r'^"(control\.[^"]+)"\s*=', catalog, re.MULTILINE)
        assert len(catalog_keys) == len(set(catalog_keys)), language
        assert keys <= set(catalog_keys), (language, keys - set(catalog_keys))
    print(
        f"PASS: {len(keys)} bilingual control keys; host-generated report, recovery "
        "and policy fixtures; source ordering and safe action guards."
    )
    print("NOT RUN: Swift compilation, Swift unit/UI tests, native rendering or screenshots.")


if __name__ == "__main__":
    check_reports()
    check_recovery()
    check_policies()
    check_surface_contracts()
