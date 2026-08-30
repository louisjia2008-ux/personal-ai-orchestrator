#!/usr/bin/env python3
"""Run a reusable P3.9 real Shadow quality campaign."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.shadow_campaign_runner import (
    CampaignFile,
    CampaignWorkerProfile,
    DeclarativeShadowCase,
    WorkerExecutionResult,
    run_shadow_case,
    write_campaign_report,
)
from personal_ai_orchestrator.verifier import VerifierCommand, VerifierProfile

CODEX_PROFILE = CampaignWorkerProfile(
    worker_id="codex-cli",
    provider_id="openai",
    provider_display_name="OpenAI",
    account_id="codex-cli-account",
    plan_id="codex-chatgpt-plan",
    plan_name="Codex CLI existing login",
    model_sku_id="gpt-5.5",
    model_display_name="GPT-5.5",
    execution_target_id="codex-cli-gpt-5.5",
    runtime_id="codex-cli",
    quota_pool_id="codex-chatgpt-plan",
    catalog_snapshot_id="catalog-p39-real-codex",
    catalog_source="p39-real-shadow-campaign",
    quota_snapshot_id="quota-p39-codex-unknown",
    quota_source_reference="codex-cli-existing-auth",
    quota_source_note=(
        "Codex CLI existing authentication is used without reading secrets; precise "
        "subscription quota/reset truth remains unavailable."
    ),
    quota_confidence=EvidenceConfidence.UNKNOWN,
)

CLAUDE_PROFILE = CampaignWorkerProfile(
    worker_id="claude-code",
    provider_id="anthropic",
    provider_display_name="Anthropic",
    account_id="claude-code-account",
    plan_id="claude-code-plan",
    plan_name="Claude Code existing login",
    model_sku_id="sonnet",
    model_display_name="Claude Sonnet",
    execution_target_id="claude-code-sonnet",
    runtime_id="claude-code",
    quota_pool_id="claude-code-plan",
    catalog_snapshot_id="catalog-p39-real-claude",
    catalog_source="p39-real-shadow-campaign",
    quota_snapshot_id="quota-p39-claude-unknown",
    quota_source_reference="claude-code-existing-auth",
    quota_source_note=(
        "Claude Code existing authentication is probed without reading secrets; precise "
        "subscription quota/reset truth remains unavailable."
    ),
    quota_confidence=EvidenceConfidence.UNKNOWN,
)


def _unittest_command() -> VerifierCommand:
    return VerifierCommand(
        name="unittest",
        argv=("python3", "-B", "-m", "unittest", "discover", "-s", "tests"),
        timeout_seconds=60,
    )


def _python_check(name: str, source: str) -> VerifierCommand:
    return VerifierCommand(
        name=name,
        argv=("python3", "-B", "-c", source),
        timeout_seconds=30,
    )


def campaign_cases() -> tuple[DeclarativeShadowCase, ...]:
    common_import = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n\n"
    )
    return (
        DeclarativeShadowCase(
            case_id="bug_fix_rounding",
            task_family="BUG_FIX",
            difficulty_class="small_deterministic",
            required_capabilities={"implementation": 0.8},
            fixture_files=(
                CampaignFile(
                    path="src/invoice.py",
                    content=(
                        "from decimal import Decimal, ROUND_HALF_UP\n\n\n"
                        "def cents_for_amount(amount: str) -> int:\n"
                        "    value = Decimal(amount)\n"
                        "    return int(value * 100)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_invoice.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from invoice import cents_for_amount\n\n\n"
                        "class InvoiceTests(unittest.TestCase):\n"
                        "    def test_rounds_half_up_to_cents(self) -> None:\n"
                        "        self.assertEqual(cents_for_amount('10.235'), 1024)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/invoice.py",),
            verifier_profile=VerifierProfile(
                name="p39-bug-fix-unittest",
                allowed_paths=("src/invoice.py",),
                commands=(_unittest_command(),),
            ),
            worker_prompt=(
                "Fix the failing deterministic unittest by editing only src/invoice.py. "
                "The function must convert a decimal money amount to cents using normal "
                "half-up cent rounding. Do not edit tests or use network access. Run "
                "python3 -B -m unittest discover -s tests before finishing."
            ),
            task_intent="repair deterministic rounding behavior",
        ),
        DeclarativeShadowCase(
            case_id="test_add_clamp",
            task_family="TEST_ADD",
            difficulty_class="small_regression_test",
            required_capabilities={"testing": 0.75},
            fixture_files=(
                CampaignFile(
                    path="src/ranges.py",
                    content=(
                        "def clamp(value: int, minimum: int, maximum: int) -> int:\n"
                        "    return max(minimum, min(value, maximum))\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_ranges.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from ranges import clamp\n\n\n"
                        "class RangeTests(unittest.TestCase):\n"
                        "    def test_clamps_below_minimum(self) -> None:\n"
                        "        self.assertEqual(clamp(-1, 0, 10), 0)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("tests/test_ranges.py",),
            verifier_profile=VerifierProfile(
                name="p39-test-add-unittest",
                allowed_paths=("tests/test_ranges.py",),
                commands=(
                    _python_check(
                        "upper-bound-regression-test-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('tests/test_ranges.py').read_text()\n"
                            "assert 'test_clamps_above_maximum' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "The implementation is already correct. Add a missing unittest regression "
                "named test_clamps_above_maximum in tests/test_ranges.py only, proving "
                "clamp(99, 0, 10) returns 10. Do not edit src/ranges.py. Run "
                "python3 -B -m unittest discover -s tests before finishing."
            ),
            task_intent="add missing clamp regression test",
        ),
        DeclarativeShadowCase(
            case_id="refactor_text_stats",
            task_family="REFACTOR",
            difficulty_class="behavior_preserving_structure",
            required_capabilities={"implementation": 0.8, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/text_stats.py",
                    content=(
                        "def word_count(text: str) -> int:\n"
                        "    words = [part for part in text.split(' ') if part.strip()]\n"
                        "    return len(words)\n\n\n"
                        "def unique_word_count(text: str) -> int:\n"
                        "    words = [part.lower() for part in text.split(' ') if part.strip()]\n"
                        "    return len(set(words))\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_text_stats.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from text_stats import unique_word_count, word_count\n\n\n"
                        "class TextStatsTests(unittest.TestCase):\n"
                        "    def test_counts_words(self) -> None:\n"
                        "        self.assertEqual(word_count('alpha  beta'), 2)\n\n"
                        "    def test_counts_unique_words_case_insensitively(self) -> None:\n"
                        "        self.assertEqual(unique_word_count('Alpha beta alpha'), 2)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/text_stats.py",),
            verifier_profile=VerifierProfile(
                name="p39-refactor-unittest",
                allowed_paths=("src/text_stats.py",),
                commands=(
                    _python_check(
                        "shared-helper-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('src/text_stats.py').read_text()\n"
                            "assert 'def _normalized_words' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Refactor src/text_stats.py only so word parsing is shared through a helper "
                "named _normalized_words while preserving existing behavior. Do not edit tests "
                "or add dependencies. Run python3 -B -m unittest discover -s tests."
            ),
            task_intent="perform bounded behavior-preserving refactor",
        ),
        DeclarativeShadowCase(
            case_id="multi_file_labels",
            task_family="MULTI_FILE_CHANGE",
            difficulty_class="small_two_file_feature",
            required_capabilities={"implementation": 0.82, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/labels.py",
                    content=(
                        "def normalize_label(value: str) -> str:\n"
                        "    return value.strip().lower().replace(' ', '-')\n"
                    ),
                ),
                CampaignFile(
                    path="src/report.py",
                    content=(
                        "from labels import normalize_label\n\n\n"
                        "def report_slug(title: str) -> str:\n"
                        "    return normalize_label(title)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_report.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from report import report_slug\n\n\n"
                        "class ReportTests(unittest.TestCase):\n"
                        "    def test_report_slug_has_prefix(self) -> None:\n"
                        "        self.assertEqual(\n"
                        "            report_slug('Quarterly Revenue'),\n"
                        "            'report-quarterly-revenue',\n"
                        "        )\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/labels.py", "src/report.py"),
            verifier_profile=VerifierProfile(
                name="p39-multi-file-unittest",
                allowed_paths=("src/labels.py", "src/report.py"),
                commands=(
                    _python_check(
                        "both-files-changed",
                        (
                            "import subprocess\n"
                            "paths = set(\n"
                            "    subprocess.check_output(\n"
                            "        ['git', 'diff', '--name-only', 'HEAD']\n"
                            "    ).decode().split()\n"
                            ")\n"
                            "assert {'src/labels.py', 'src/report.py'} <= paths\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Make the report slug include a report- prefix. This must be a bounded "
                "two-file change: add the reusable prefix behavior in src/labels.py and call "
                "it from src/report.py. Do not edit tests. Run python3 -B -m unittest "
                "discover -s tests."
            ),
            task_intent="make bounded multi-file report label change",
        ),
    )


class _SubprocessWorkerHandle:
    def __init__(
        self,
        *,
        process: subprocess.Popen[str],
        worker_profile: CampaignWorkerProfile,
        started_at: datetime,
        timeout_seconds: float,
    ) -> None:
        self.process = process
        self.pid = process.pid
        self.worker_profile = worker_profile
        self.started_at = started_at
        self.timeout_seconds = timeout_seconds

    def wait(self) -> WorkerExecutionResult:
        stdout, stderr = self.process.communicate(timeout=self.timeout_seconds)
        finished_at = datetime.now(UTC)
        exit_code = self.process.returncode
        stderr_tail = stderr[-4000:]
        if exit_code == 0 and len(stderr) > 2000:
            stderr_tail = "OMITTED_SUCCESSFUL_CODEX_CLI_DIAGNOSTICS"
        return WorkerExecutionResult(
            worker_id=self.worker_profile.worker_id,
            provider_id=self.worker_profile.provider_id,
            execution_target_id=self.worker_profile.execution_target_id,
            pid=self.process.pid,
            started_at=self.started_at,
            finished_at=finished_at,
            exit_code=exit_code,
            stdout_tail=stdout[-12000:],
            stderr_tail=stderr_tail,
        )


def _launch_codex_worker(
    repo: Path,
    case: DeclarativeShadowCase,
    profile: CampaignWorkerProfile,
) -> _SubprocessWorkerHandle:
    codex = shutil.which("codex")
    if codex is None:
        raise RuntimeError("codex CLI is not available")
    prompt = (
        "You are operating only inside this disposable repository. "
        f"CASE_ID: {case.case_id}. TASK_FAMILY: {case.task_family}. "
        f"Expected changed-file scope: {', '.join(case.expected_changed_paths)}. "
        f"{case.worker_prompt}"
    )
    argv = [
        codex,
        "exec",
        "--ephemeral",
        "--ignore-rules",
        "--ignore-user-config",
        "-m",
        profile.model_sku_id,
        "-C",
        str(repo),
        "-s",
        "workspace-write",
        "-c",
        "approval_policy='never'",
        prompt,
    ]
    started_at = datetime.now(UTC)
    process = subprocess.Popen(
        argv,
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return _SubprocessWorkerHandle(
        process=process,
        worker_profile=profile,
        started_at=started_at,
        timeout_seconds=420,
    )


def _launch_claude_worker(
    repo: Path,
    case: DeclarativeShadowCase,
    profile: CampaignWorkerProfile,
) -> _SubprocessWorkerHandle:
    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError("claude CLI is not available")
    prompt = (
        "You are operating only inside this disposable repository. "
        f"CASE_ID: {case.case_id}. TASK_FAMILY: {case.task_family}. "
        f"Expected changed-file scope: {', '.join(case.expected_changed_paths)}. "
        f"{case.worker_prompt}"
    )
    argv = [
        claude,
        "-p",
        "--model",
        profile.model_sku_id,
        "--permission-mode",
        "bypassPermissions",
        "--no-session-persistence",
        "--allowedTools=Edit,Bash(python3 *),Bash(git diff *),Bash(git status *)",
        prompt,
    ]
    started_at = datetime.now(UTC)
    process = subprocess.Popen(
        argv,
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return _SubprocessWorkerHandle(
        process=process,
        worker_profile=profile,
        started_at=started_at,
        timeout_seconds=600,
    )


def _probe_command(argv: list[str], *, cwd: Path, timeout: float = 45.0) -> dict[str, object]:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"available": False}
    except subprocess.TimeoutExpired:
        return {"available": True, "timed_out": True}
    text = f"{completed.stdout}\n{completed.stderr}".lower()
    return {
        "available": True,
        "exit_code": completed.returncode,
        "auth_blocked": any(marker in text for marker in ("login", "auth", "unauthorized", "401")),
        "provider_error_401": "401" in text,
        "succeeded": completed.returncode == 0,
    }


def probe_workers(repo_root: Path, *, attempt_claude: bool) -> dict[str, object]:
    probes: dict[str, object] = {}
    codex_path = shutil.which("codex")
    probes["codex"] = {
        "cli_available": codex_path is not None,
        "version": None
        if codex_path is None
        else _probe_command([codex_path, "--version"], cwd=repo_root),
        "existing_auth_usable": "deferred_to_real_campaign_runs",
    }
    claude_path = shutil.which("claude")
    claude: dict[str, object] = {
        "claude_cli_available": claude_path is not None,
        "claude_existing_login_usable": "NOT_EXECUTED",
        "claude_real_execution_safe": False,
        "claude_actual_execution_target_identifiable": False,
    }
    if claude_path is not None:
        claude["version"] = _probe_command([claude_path, "--version"], cwd=repo_root)
        if attempt_claude:
            probe = _probe_command(
                [claude_path, "--print", "Respond with PAO_CLAUDE_PROBE_OK only."],
                cwd=repo_root,
                timeout=90,
            )
            claude["auth_probe"] = probe
            claude["claude_existing_login_usable"] = bool(probe.get("succeeded"))
            claude["claude_real_execution_safe"] = bool(probe.get("succeeded"))
            claude["claude_actual_execution_target_identifiable"] = bool(probe.get("succeeded"))
        else:
            claude["claude_existing_login_usable"] = "NOT_EXECUTED_BY_FLAG"
    probes["claude"] = claude

    opencode_path = shutil.which("opencode")
    probes["minimax"] = {
        "opencode_cli_available": opencode_path is not None,
        "catalog_probe": None
        if opencode_path is None
        else _probe_command([opencode_path, "models", "minimax"], cwd=repo_root),
        "credential_repair_attempted": False,
    }
    return probes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--campaign-root",
        type=Path,
        default=Path(".personal-ai-orchestrator/p39-shadow"),
    )
    parser.add_argument("--max-observations", type=int, default=4)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--attempt-claude-probe", action="store_true")
    parser.add_argument("--worker", choices=("codex", "claude"), default="codex")
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()

    cases = campaign_cases()
    if args.list_cases:
        for case in cases:
            print(f"{case.case_id}\t{case.task_family}\t{case.difficulty_class}")
        return 0

    selected = [case for case in cases if not args.case_ids or case.case_id in args.case_ids]
    selected = selected[: args.max_observations]
    repo_root = Path.cwd()
    repo_head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    probes = probe_workers(repo_root, attempt_claude=args.attempt_claude_probe)
    worker_profile = CODEX_PROFILE if args.worker == "codex" else CLAUDE_PROFILE
    worker_launcher = _launch_codex_worker if args.worker == "codex" else _launch_claude_worker
    results = []
    for case in selected:
        result = run_shadow_case(
            campaign_root=args.campaign_root,
            campaign_id="p39-real-shadow-quality-campaign",
            case=case,
            worker_profile=worker_profile,
            worker_launcher=worker_launcher,
            repo_head=repo_head,
        )
        results.append(result)
        print(
            "\t".join(
                (
                    result.case_id,
                    result.task_family,
                    result.final_task_state,
                    str(result.observation_id),
                    str(result.quality_observations_after),
                )
            )
        )

    report_path = write_campaign_report(
        campaign_root=args.campaign_root,
        report_name="p39-real-shadow-quality-campaign-report.json",
        results=results,
        provider_probe_results=probes,
    )
    print(report_path)
    return 0 if results else 2


if __name__ == "__main__":
    raise SystemExit(main())
