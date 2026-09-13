"""PI-5B3F campaign-aware wrapper around the PI-5B3E child evidence path."""

from __future__ import annotations

from typing import Any

from personal_ai_orchestrator.delegation_campaign import DelegationCalibrationCampaignStore
from personal_ai_orchestrator.delegation_quota_calibration import (
    build_quota_baseline,
    compare_quota_after,
)
from personal_ai_orchestrator.pi5_child_execution import PAODelegationChildPort
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore


class CampaignAwareDelegationChildPort(PAODelegationChildPort):
    """Enable B3E calibration only for campaign-admitted observations."""

    def __init__(
        self,
        *,
        delegation_campaign: DelegationCalibrationCampaignStore,
        **kwargs: Any,
    ) -> None:
        kwargs["quota_calibration_enabled"] = False
        super().__init__(**kwargs)
        self.delegation_campaign = delegation_campaign

    def _project_for_shadow(self, shadow: Any) -> str | None:
        store = SafetyKernelStore(self.state_db)
        try:
            try:
                task = store.get_task(shadow.parent_task_id)
            except KeyError:
                return None
            return task.project_id
        finally:
            store.close()

    def _capture_quota_baseline(self, store: SafetyKernelStore, shadow: Any) -> None:
        if (
            self.quota_refresh_service is None
            or self.quota_calibration_journal is None
            or shadow.child_quota_pool_id is None
        ):
            return
        project_id = self._project_for_shadow(shadow)
        if project_id is None or not self.delegation_campaign.claim(
            observation_id=shadow.observation_id,
            project_id=project_id,
        ):
            return
        provider_id = self._target_provider_id(shadow.selected_child_execution_target_id)
        if provider_id is None:
            return
        try:
            snapshot = self.quota_refresh_service.snapshot_for_pool(
                shadow.child_quota_pool_id
            )
            baseline = build_quota_baseline(
                observation_id=shadow.observation_id,
                provider_id=provider_id,
                quota_pool_id=shadow.child_quota_pool_id,
                snapshot=snapshot,
            )
            if baseline is None:
                raise ValueError("baseline unavailable")
            self.quota_calibration_journal.append(baseline)
            store.record_system_event(
                "DELEGATION_QUOTA_BASELINE_RECORDED",
                {
                    "observation_id": shadow.observation_id,
                    "quota_pool_id": baseline.quota_pool_id,
                    "snapshot_id": baseline.snapshot_id,
                },
            )
        except Exception:
            try:
                store.record_system_event(
                    "DELEGATION_QUOTA_BASELINE_RECORD_FAILED",
                    {
                        "observation_id": shadow.observation_id,
                        "reason_code": "QUOTA_BASELINE_CAPTURE_FAILED",
                    },
                )
            except Exception:
                pass

    def _quota_pair_for_child(
        self,
        shadow: Any,
    ) -> tuple[str | None, str | None, str | None]:
        if self.quota_refresh_service is None or self.quota_calibration_journal is None:
            return None, None, None
        project_id = self._project_for_shadow(shadow)
        if project_id is None or not self.delegation_campaign.is_admitted(
            observation_id=shadow.observation_id,
            project_id=project_id,
        ):
            return None, None, None
        baseline = self.quota_calibration_journal.load(shadow.observation_id)
        if baseline is None:
            return None, None, "quota_calibration_baseline_missing"
        provider_id = self._target_provider_id(shadow.selected_child_execution_target_id)
        if provider_id is None or provider_id != baseline.provider_id:
            return None, None, "quota_calibration_provider_identity_mismatch"
        try:
            self.quota_refresh_service.refresh(provider_id)
            after = self.quota_refresh_service.snapshot_for_pool(baseline.quota_pool_id)
            comparison = compare_quota_after(baseline, after)
        except Exception:
            return None, None, "quota_calibration_refresh_failed"
        if not comparison.comparable:
            return (
                None,
                None,
                f"quota_calibration_{comparison.reason.value.lower()}",
            )
        return (
            comparison.quota_before_snapshot_id,
            comparison.quota_after_snapshot_id,
            None,
        )


__all__ = ["CampaignAwareDelegationChildPort"]
