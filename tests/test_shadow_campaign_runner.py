import importlib.util
import json
from collections import Counter
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
from personal_ai_orchestrator.shadow_evidence import (
    ShadowCampaignStatus,
    ShadowEvidenceJournal,
    ShadowFailureClass,
    ShadowFailureStage,
    ShadowQualityOutcome,
)
from personal_ai_orchestrator.verifier import VerifierCommand, VerifierProfile

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def load_campaign_script() -> object:
    script = Path(__file__).resolve().parents[1] / "scripts" / "p39_real_shadow_campaign.py"
    spec = importlib.util.spec_from_file_location("p39_real_shadow_campaign", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker_profile() -> CampaignWorkerProfile:
    return CampaignWorkerProfile(
        worker_id="fixture-worker",
        provider_id="openai",
        provider_display_name="OpenAI",
        account_id="fixture-account",
        plan_id="fixture-plan",
        plan_name="Fixture subscription",
        model_sku_id="fixture-model",
        model_display_name="Fixture Model",
        execution_target_id="fixture-worker-target",
        runtime_id="fixture-runtime",
        quota_pool_id="fixture-quota-pool",
        catalog_snapshot_id="fixture-catalog",
        catalog_source="fixture",
        quota_snapshot_id="fixture-quota-unknown",
        quota_source_reference="fixture-local",
        quota_source_note="Fixture quota truth is intentionally unknown",
        quota_confidence=EvidenceConfidence.UNKNOWN,
    )


def passing_case(case_id: str = "bug_fix") -> DeclarativeShadowCase:
    return DeclarativeShadowCase(
        case_id=case_id,
        task_family="BUG_FIX",
        difficulty_class="small",
        required_capabilities={"implementation": 0.7},
        fixture_files=(
            CampaignFile(path="src/mathy.py", content="def add_one(value):\n    return value\n"),
            CampaignFile(
                path="tests/test_mathy.py",
                content=(
                    "import sys\n"
                    "import unittest\n"
                    "from pathlib import Path\n"
                    "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                    "from mathy import add_one\n\n"
                    "class MathyTests(unittest.TestCase):\n"
                    "    def test_add_one(self):\n"
                    "        self.assertEqual(add_one(1), 2)\n\n"
                    "if __name__ == '__main__':\n"
                    "    unittest.main()\n"
                ),
            ),
        ),
        expected_changed_paths=("src/mathy.py",),
        verifier_profile=VerifierProfile(
            name="fixture-pytest",
            allowed_paths=("src/mathy.py",),
            commands=(
                VerifierCommand(
                    name="unittest",
                    argv=("python3", "-B", "-m", "unittest", "discover", "-s", "tests"),
                    timeout_seconds=60,
                ),
            ),
        ),
        worker_prompt="Fix add_one by editing only src/mathy.py.",
        task_intent="fixture bug fix",
    )


class FixtureWorkerHandle:
    pid = None

    def __init__(
        self,
        repo: Path,
        *,
        exit_code: int = 0,
        should_fix: bool = True,
        stderr_tail: str = "",
    ) -> None:
        self.repo = repo
        self.exit_code = exit_code
        self.should_fix = should_fix
        self.stderr_tail = stderr_tail

    def wait(self) -> WorkerExecutionResult:
        if self.exit_code == 0 and self.should_fix:
            (self.repo / "src" / "mathy.py").write_text(
                "def add_one(value):\n    return value + 1\n",
                encoding="utf-8",
            )
        return WorkerExecutionResult(
            worker_id="fixture-worker",
            provider_id="openai",
            execution_target_id="fixture-worker-target",
            started_at=NOW,
            finished_at=NOW,
            exit_code=self.exit_code,
            stderr_tail=self.stderr_tail,
        )


def test_runner_collects_verified_observation_with_actual_target_separate_from_would_select(
    tmp_path: Path,
) -> None:
    result = run_shadow_case(
        campaign_root=tmp_path / "campaign",
        campaign_id="p39-fixture",
        case=passing_case(),
        worker_profile=worker_profile(),
        worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(repo),
        repo_head="abc123",
    )

    journal = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime")
    observations = journal.load_all()
    state = journal.load_campaign_state()

    assert result.verified is True
    assert result.would_select_target is None
    assert result.actual_retained_target == "fixture-worker-target"
    assert result.observation_id == observations[0].observation_id
    assert observations[0].scheduler_execution_target_id is None
    assert observations[0].manual_execution_target_id == "fixture-worker-target"
    assert observations[0].task_family == "BUG_FIX"
    assert observations[0].execution_success is True
    assert observations[0].verification_success is True
    assert observations[0].quality_outcome is ShadowQualityOutcome.VERIFIED
    assert observations[0].failure_class is ShadowFailureClass.NONE
    assert observations[0].failure_stage is ShadowFailureStage.NONE
    assert state is not None
    assert state.status is ShadowCampaignStatus.COLLECTING


def test_runner_appends_after_journal_restart(tmp_path: Path) -> None:
    campaign_root = tmp_path / "campaign"
    for case_id in ("first", "second"):
        run_shadow_case(
            campaign_root=campaign_root,
            campaign_id="p39-fixture",
            case=passing_case(case_id),
            worker_profile=worker_profile(),
            worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(repo),
            repo_head="abc123",
        )
        assert len(ShadowEvidenceJournal(campaign_root / "shadow-runtime").load_all()) >= 1

    restarted = ShadowEvidenceJournal(campaign_root / "shadow-runtime")
    assert len(restarted.load_all()) == 2
    assert restarted.summarize_campaign().quality_observations == 2


def test_campaign_report_includes_quality_and_failure_taxonomy(tmp_path: Path) -> None:
    campaign_root = tmp_path / "campaign"
    results = [
        run_shadow_case(
            campaign_root=campaign_root,
            campaign_id="p39-fixture",
            case=passing_case(),
            worker_profile=worker_profile(),
            worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(repo),
            repo_head="abc123",
        )
    ]

    report_path = write_campaign_report(
        campaign_root=campaign_root,
        report_name="fixture-report.json",
        results=results,
        provider_probe_results={"codex": {"existing_auth_usable": "fixture"}},
    )
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    assert payload["readiness"]["total_quality_eligible_observations"] == 1
    assert payload["readiness"]["verified_count"] == 1
    assert payload["readiness"]["operational_failures"] == 0
    assert payload["quality_metrics"]["failure_class_counts"] == {"NONE": 1}
    assert payload["quality_metrics"]["quality_outcome_counts"] == {"VERIFIED": 1}


def test_runner_preserves_verifier_failure_as_negative_shadow_observation(tmp_path: Path) -> None:
    result = run_shadow_case(
        campaign_root=tmp_path / "campaign",
        campaign_id="p39-fixture",
        case=passing_case(),
        worker_profile=worker_profile(),
        worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(repo, should_fix=False),
        repo_head="abc123",
    )

    observations = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").load_all()

    assert result.verified is False
    assert result.final_task_state == "BLOCKED"
    assert result.failure_reason == "verifier command failed: unittest"
    assert len(observations) == 1
    assert observations[0].verified is False
    assert observations[0].failure_class is ShadowFailureClass.MODEL_TASK_FAILURE
    assert observations[0].failure_stage is ShadowFailureStage.VERIFICATION
    assert observations[0].quality_outcome is ShadowQualityOutcome.MODEL_QUALITY_FAILED
    summary = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").summarize()
    assert summary.model_task_failures == 1
    assert summary.verifier_failures == 0


def test_runner_preserves_worker_process_failure_as_negative_shadow_observation(
    tmp_path: Path,
) -> None:
    result = run_shadow_case(
        campaign_root=tmp_path / "campaign",
        campaign_id="p39-fixture",
        case=passing_case(),
        worker_profile=worker_profile(),
        worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(repo, exit_code=2),
        repo_head="abc123",
    )

    observations = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").load_all()

    assert result.verified is False
    assert result.final_task_state == "BLOCKED"
    assert result.failure_reason == "worker exited with code 2"
    assert len(observations) == 1
    assert observations[0].verified is False
    assert observations[0].execution_success is False
    assert observations[0].failure_class is ShadowFailureClass.WORKER_PROCESS_FAILURE
    assert observations[0].failure_stage is ShadowFailureStage.EXECUTION
    assert observations[0].quality_outcome is ShadowQualityOutcome.OPERATIONAL_FAILED
    assert observations[0].attempts_to_green is None
    summary = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").summarize()
    assert summary.operational_failures == 1
    assert summary.model_task_failures == 0


def test_runner_classifies_usage_limit_as_policy_block(tmp_path: Path) -> None:
    result = run_shadow_case(
        campaign_root=tmp_path / "campaign",
        campaign_id="p39-fixture",
        case=passing_case(),
        worker_profile=worker_profile(),
        worker_launcher=lambda repo, _case, _profile: FixtureWorkerHandle(
            repo,
            exit_code=1,
            stderr_tail="ERROR: You've hit your usage limit. Try again later.",
        ),
        repo_head="abc123",
    )

    observations = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").load_all()

    assert result.verified is False
    assert result.failure_class is ShadowFailureClass.POLICY_BLOCK
    assert result.failure_stage is ShadowFailureStage.INVOCATION
    assert result.quality_outcome is ShadowQualityOutcome.POLICY_BLOCKED
    assert observations[0].failure_class is ShadowFailureClass.POLICY_BLOCK
    summary = ShadowEvidenceJournal(tmp_path / "campaign" / "shadow-runtime").summarize()
    assert summary.policy_blocks == 1
    assert summary.operational_failures == 0


def test_real_campaign_script_defines_twenty_codex_only_variants() -> None:
    module = load_campaign_script()
    cases = module.campaign_cases()
    families = Counter(case.task_family for case in cases)
    prompt_text = "\n".join(case.worker_prompt for case in cases).lower()

    assert module.CODEX_PROFILE.provider_id == "openai"
    assert module.CODEX_PROFILE.worker_id == "codex-cli"
    assert len(cases) == 20
    assert families == {
        "BUG_FIX": 5,
        "TEST_ADD": 5,
        "REFACTOR": 5,
        "MULTI_FILE_CHANGE": 5,
    }
    assert len({case.case_id for case in cases}) == 20
    assert "claude" not in prompt_text
