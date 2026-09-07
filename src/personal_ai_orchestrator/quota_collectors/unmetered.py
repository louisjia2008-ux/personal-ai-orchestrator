"""M1 WP4 unmetered pool collector.

OpenCode Zen proxies free-model traffic through ``opencode.ai``; the
host needs **no** credential of its own and there is no quota
window to read. This collector is local — it never opens a network
socket — and returns a single ``UNMETERED`` window whose
``used_fraction`` is ``None`` so ``quota_burn.assess`` short-circuits
to ``BurnPressure.UNMETERED``.

The collector's contract with ``dispatch_executor._admit_quota`` is
identical to the windowed collectors': a successful
``collect()`` returns ``QuotaCollectionResult(status=SUCCESS)`` with a
synthetic ``QuotaSnapshot`` carrying one UNMETERED window. The result
becomes a ``QuotaAvailabilityEvidence`` with
``state=AVAILABLE_UNMETERED`` and ``consecutive_failures=0`` (so the
A2 ``UNCERTAIN_LOCKED`` projection cannot fire on this path).

The collector also produces a ``PlanQuotaProjection`` with a single
``UNMETERED`` ``QuotaWindowSnapshot``. ``burn()`` therefore returns
``UNMETERED`` automatically, and ``source_pressure_for`` reads the
same data shape the windowed path uses — the only branch the
scheduler needs is the new ``QuotaWindowKind.UNMETERED`` value.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from personal_ai_orchestrator.model_registry import (
    EvidenceConfidence,
    EvidenceSource,
    QuotaSnapshot,
    QuotaWindowKind,
    QuotaWindowSnapshot,
)
from personal_ai_orchestrator.quota_collectors.base import (
    QuotaCollectionResult,
    QuotaCollectionStatus,
    QuotaCollector,
)
from personal_ai_orchestrator.quota_plan import (
    BindingWindow,
    ConsumptionUnitKind,
    PlanQuota,
    PlanQuotaProjection,
    PlanQuotaSemantics,
    QuotaResourceKind,
    SharedQuotaPool,
)


# M1 WP4: the availability-side types live in ``quota_availability``
# which ``shadow_evidence`` transitively imports. Importing the
# availability types here at module load time would close a
# circular chain (``quota_collectors/__init__`` → ``unmetered`` →
# ``quota_availability`` → ``quota_policy_analysis`` →
# ``shadow_evidence`` ← ``quota_collectors/__init__``). Type hints
# only; the real values flow at call time via the ``evidence`` method
# below.
if TYPE_CHECKING:
    from personal_ai_orchestrator.quota_availability import (
        QuotaAvailabilityEvidence,
        QuotaAvailabilityState,
    )


class UnmeteredQuotaCollector(QuotaCollector):
    """Read-only collector that emits a single UNMETERED window.

    The collector takes a ``provider_id`` and ``pool_id`` so the
    synthetic evidence it produces can be addressed by the existing
    journal / projection / control-plane plumbing without
    inventing a new wire path. ``covered_model_ids`` lets the
    dispatch recommender pin the unmetered pool to the opencode
    free SKUs so a single snapshot is the source of truth for every
    ``execution_target`` that resolves to this pool.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        pool_id: str,
        plan_id: str | None = None,
        covered_model_ids: tuple[str, ...] = (),
        observed_at: datetime | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._pool_id = pool_id
        self._plan_id = plan_id or provider_id
        self._covered_model_ids = covered_model_ids
        self._observed_at = observed_at

    def collect(self) -> QuotaCollectionResult:
        """Return a single UNMETERED window with no upstream round-trip.

        ``observed_at`` is a fixed construction-time timestamp: the
        collector never opens a network socket, so there is no
        "when the upstream said it" — there is only "when the host
        asked". Pinning the instant makes the test path
        deterministic.
        """

        observed_at = self._observed_at
        window_started_at = self._observed_at
        source = EvidenceSource(
            source_type="LOCAL_OBSERVATION",
            observed_at=observed_at,
            reference="unmetered_collector",
            note=(
                "M1 WP4: synthetic UNMETERED window. No upstream quota "
                "endpoint to read; rate-limit detection is on the worker "
                "outcome classifier."
            ),
            confidence=EvidenceConfidence.ESTIMATED,
        )
        unmetered_window = QuotaWindowSnapshot(
            window_id=f"{self._provider_id}-unmetered",
            window_kind=QuotaWindowKind.UNMETERED,
            duration_seconds=None,
            remaining_fraction=None,
            used_fraction=None,
            remaining_units=None,
            used_units=None,
            total_units=None,
            unit=None,
            window_started_at=window_started_at,
            reset_at=None,
            state="UNKNOWN",
            confidence=EvidenceConfidence.UNKNOWN,
            source=source,
        )
        snapshot = QuotaSnapshot(
            id=f"{self._provider_id}-unmetered-{observed_at.isoformat()}",
            quota_pool_id=self._pool_id,
            provider_id=self._provider_id,
            observed_at=observed_at,
            windows=(unmetered_window,),
            source=source,
            confidence=EvidenceConfidence.ESTIMATED,
        )
        projection = PlanQuotaProjection(
            plan=PlanQuota(
                provider_id=self._provider_id,
                plan_id=self._plan_id,
                display_name=f"{self._provider_id} (unmetered)",
                quota_semantics=PlanQuotaSemantics.SHARED_POOL,
                plan_level=None,
                observed_at=observed_at,
            ),
            pool=SharedQuotaPool(
                pool_id=self._pool_id,
                provider_id=self._provider_id,
                plan_id=self._plan_id,
                resource_kind=QuotaResourceKind.CODING_PLAN_USAGE_POOL,
                shared_across_models=True,
                covered_model_ids=self._covered_model_ids,
                unit_kind=ConsumptionUnitKind.PLAN_CREDITS,
                provider_unit_label="UNMETERED",
            ),
            windows=(unmetered_window,),
            binding_window=BindingWindow(),
            source=source,
        )
        return QuotaCollectionResult(
            status=QuotaCollectionStatus.SUCCESS,
            snapshot=snapshot,
            last_known_good=snapshot,
            error_category=None,
            projection=projection,
        )

    def evidence(
        self,
        *,
        execution_target_id: str,
        quota_pool_id: str,
        observed_at: datetime | None = None,
    ) -> QuotaAvailabilityEvidence:
        """Build a fresh ``available_unmetered`` journal entry.

        The dispatch executor's ``_snapshot_to_availability`` helper
        currently expects a windowed snapshot. For an unmetered target
        we go straight to ``observe_success`` with the unmetered state
        — the helper cannot synthesise the unmetered semantics from a
        windowed snapshot without lying about the kind.

        The ``quota_availability`` import is deferred to call time so
        this module can be imported by ``quota_collectors/__init__``
        without closing a circular chain through ``shadow_evidence``.
        """

        # Local import (see TYPE_CHECKING note above).
        from personal_ai_orchestrator.quota_availability import (
            QuotaAvailabilityState,
            observe_success,
        )

        moment = observed_at or self._observed_at
        return observe_success(
            previous=None,
            execution_target_id=execution_target_id,
            provider_id=self._provider_id,
            quota_pool_id=quota_pool_id,
            observed_at=moment,
        ).model_copy(
            update={
                "state": QuotaAvailabilityState.AVAILABLE_UNMETERED,
                "sanitized_reason_code": "UNMETERED_POOL_AVAILABLE",
                "previous_state_baseline": (
                    QuotaAvailabilityState.AVAILABLE_UNMETERED
                ),
            }
        )


__all__ = ["UnmeteredQuotaCollector"]