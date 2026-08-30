from datetime import UTC, datetime
from pathlib import Path

from personal_ai_orchestrator.model_registry import EvidenceConfidence
from personal_ai_orchestrator.shadow_campaign_runner import (
    CampaignFile,
    CampaignWorkerProfile,
    DeclarativeShadowCase,
    WorkerExecutionResult,
    run_shadow_case,
)
from personal_ai_orchestrator.shadow_evidence import ShadowCampaignStatus, ShadowEvidenceJournal
from personal_ai_orchestrator.verifier import VerifierCommand, VerifierProfile

NOW = datetime(2026, 8, 30, tzinfo=UTC)


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

    def __init__(self, repo: Path, *, exit_code: int = 0, should_fix: bool = True) -> None:
        self.repo = repo
        self.exit_code = exit_code
        self.should_fix = should_fix

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
    assert observations[0].attempts_to_green is None
