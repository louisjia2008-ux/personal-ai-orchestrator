"""Which provider quota scopes are relevant to the workload we actually schedule.

Why this layer exists
---------------------
MiniMax's quota response describes at least two scopes on this account:
``general`` and ``video``. Their remaining figures differ, and the collector
read that disagreement as "no plan figure is derivable" — collapsing MiniMax
coding quota to UNKNOWN because a *video* balance disagreed with a *text*
balance.

That is a category error. The two figures do not disagree about one quantity;
they describe two different resources. Personal AI Orchestrator schedules
coding, text, and agentic software-engineering work. It does not schedule
MiniMax video generation, so the video balance is not evidence about anything
this product does, and it can neither confirm nor deny coding availability.

The fix is a typed relevance layer rather than a special case. Scopes are
classified into workloads; the product declares which workloads it schedules;
the coding projection reads only the scopes belonging to a scheduled workload.
When this product later schedules video, it declares that workload instead of
deleting an ``if scope == "video"`` branch.

Two rules hold throughout:

1. Relevance is not truth. A scope excluded here is still a real provider
   observation and is preserved verbatim beside the projection (§21). It is
   *out of scope*, never *untrue*, and never deleted.
2. Exclusion is never a limiter. An excluded scope cannot lower, cap, average
   with, or invalidate a scheduled workload's figure. It simply is not read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum


class QuotaWorkloadScope(StrEnum):
    """The kind of work a provider quota scope meters.

    ``UNKNOWN`` is not a synonym for "irrelevant". A provider may name a scope
    we have no classification for; refusing to guess is what keeps this layer
    from inventing membership, and unclassified scopes are handled explicitly
    by :func:`scopes_for_workload` rather than silently dropped.
    """

    CODING_TEXT = "CODING_TEXT"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"
    AUDIO = "AUDIO"
    UNKNOWN = "UNKNOWN"


#: Workloads this build of the orchestrator actually schedules. Everything else
#: is observed and preserved, but never consulted for routing, binding-window
#: selection, temporal scarcity, QUOTA_SAVER, or equivalent capacity.
ACTIVE_SCHEDULING_WORKLOADS: frozenset[QuotaWorkloadScope] = frozenset(
    {QuotaWorkloadScope.CODING_TEXT}
)

#: The workload the coding dashboard and the scheduler ask for today.
ACTIVE_CODING_WORKLOAD = QuotaWorkloadScope.CODING_TEXT

#: Provider scope names whose workload is documented or directly observed.
#: Keys are lowercased provider scope ids; the classification is evidence, so a
#: name absent from this table stays UNKNOWN rather than being guessed at.
PROVIDER_SCOPE_WORKLOADS: Mapping[str, Mapping[str, QuotaWorkloadScope]] = {
    "minimax": {
        # OBSERVED on this account: the coding/text scope the orchestrator's
        # MiniMax targets draw on.
        "general": QuotaWorkloadScope.CODING_TEXT,
        "text": QuotaWorkloadScope.CODING_TEXT,
        # OBSERVED: video generation, which this product does not schedule.
        "video": QuotaWorkloadScope.VIDEO_GENERATION,
        "image": QuotaWorkloadScope.IMAGE_GENERATION,
        "audio": QuotaWorkloadScope.AUDIO,
        "speech": QuotaWorkloadScope.AUDIO,
        "music": QuotaWorkloadScope.AUDIO,
    },
}


def provider_scope_family(provider_id: str) -> str:
    """Collapse a provider *surface* id onto the family that owns the scope table.

    Discovery exposes ``minimax``, ``minimax-cn``, and ``minimax-cn-coding-plan``
    as separate surfaces of one provider. They share MiniMax's quota vocabulary,
    so they must share its scope classification; keying the table by surface
    would leave ``general`` unclassified on every surface but one.
    """

    key = provider_id.strip().lower()
    for family in PROVIDER_SCOPE_WORKLOADS:
        if key == family or key.startswith(f"{family}-"):
            return family
    return key


def is_scheduled_workload(workload: QuotaWorkloadScope) -> bool:
    """Whether this build schedules work against the given workload."""

    return workload in ACTIVE_SCHEDULING_WORKLOADS


def classify_provider_scope(provider_id: str, scope_id: str) -> QuotaWorkloadScope:
    """Classify one provider quota scope name into a workload.

    Only names we hold evidence for are classified. ``MiniMax-M2.7`` is a model
    id, not a workload label, and returns UNKNOWN here — deciding what a model
    is *for* is the catalog's job, not this table's.
    """

    table = PROVIDER_SCOPE_WORKLOADS.get(provider_scope_family(provider_id), {})
    return table.get(scope_id.strip().lower(), QuotaWorkloadScope.UNKNOWN)


def select_for_workload[T](
    classified: Sequence[tuple[T, QuotaWorkloadScope]],
    workload: QuotaWorkloadScope,
) -> tuple[T, ...]:
    """The items a projection for ``workload`` may read.

    Items classified as *this* workload win outright. Only when none exists do
    unclassified items stand in — a provider that reports a single unnamed bar,
    or names its entries after models, still yields a figure exactly as before.

    An item classified as some *other* workload never stands in. That is the
    whole invariant: a video balance can neither supply nor suppress a coding
    figure, so ``general 95% / video 60%`` reads as ``95%``, and ``video`` alone
    reads as UNKNOWN rather than as ``60%``.
    """

    exact = tuple(item for item, kind in classified if kind is workload)
    if exact:
        return exact
    return tuple(item for item, kind in classified if kind is QuotaWorkloadScope.UNKNOWN)


def scopes_for_workload(
    classified: Sequence[tuple[str, QuotaWorkloadScope]],
    workload: QuotaWorkloadScope,
) -> tuple[str, ...]:
    """Scope *ids* a projection for ``workload`` may read."""

    return select_for_workload(classified, workload)


def excluded_workloads(
    classified: Iterable[tuple[object, QuotaWorkloadScope]],
    workload: QuotaWorkloadScope,
) -> tuple[QuotaWorkloadScope, ...]:
    """Workloads observed on this provider that ``workload`` does not read.

    Returned so the owner can be told *why* a real provider figure is absent
    from the coding dashboard, instead of the evidence simply vanishing.
    """

    seen: list[QuotaWorkloadScope] = []
    for _, kind in classified:
        if kind is workload or kind is QuotaWorkloadScope.UNKNOWN:
            continue
        if kind not in seen:
            seen.append(kind)
    return tuple(seen)


__all__ = [
    "ACTIVE_CODING_WORKLOAD",
    "ACTIVE_SCHEDULING_WORKLOADS",
    "PROVIDER_SCOPE_WORKLOADS",
    "QuotaWorkloadScope",
    "classify_provider_scope",
    "excluded_workloads",
    "is_scheduled_workload",
    "provider_scope_family",
    "scopes_for_workload",
    "select_for_workload",
]
