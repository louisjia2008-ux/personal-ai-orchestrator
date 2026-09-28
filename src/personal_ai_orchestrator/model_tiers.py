"""Model tier table: pure functions over a host-owned policy file.

The recommender's ``capability_fit`` needs to know whether a target's
model tier is at, above, or below the task's ``min_tier``. A
:class:`TierTable` is the answer to that question for every
``execution_target_id`` the daemon can ever observe: pick the target's
``ModelTier`` via a glob-overridable table, compare to the task's
``min_tier``, and surface the verdict up through the dispatch candidate
view so the UI can render a tier chip alongside the pressure chip.

This module is deliberately **stdlib-only**. It sits at the bottom of
the project dependency DAG, the same position as ``quota_burn`` — the
provider-aware lookup is here, the project-internal consumers
(``model_registry``, ``dispatch_recommender``, ``control_api``) are on
top of it. ``control_api`` reads the JSON file, builds a
:class:`TierTable`, and injects the merged result.

``target_key`` format
---------------------

A ``target_key`` is one ``execution_target_id``: a string of the shape
``"{provider_id}-{sku}"`` — **single dash, never a slash**. The dash
shape is the same one ``provider_discovery.py:1290`` synthesizes at
discovery time (``f"{record.provider_id}-{sku}"``). Patterns in the
table use ``fnmatch`` (Unix shell glob semantics). ``*`` matches any
character **including dashes**, so ``opencode-*-free`` matches both
``opencode-glm-4.5-free`` and ``opencode-glm-free`` — the host that
wants narrower matching must spell out each pattern explicitly.
Always scope a free-tier glob to a provider prefix so a future
``minimax-cn-coding-plan-GLM-4.5-free`` SKU does not silently inherit
a free-tier classification.

Patterns are matched in this order:

1. Exact key match (``pattern == target_key``).
2. Longest ``fnmatch`` glob match (the first one wins on ties, since
   Python dicts guarantee no two patterns have the same string).
3. The default :class:`TierEntry` (``ModelTier.T1``, no caps).

A pattern that matches nothing is silently kept in the table — the
parser does not validate that every pattern has a live match. The
host can write the table speculatively without coordinating with the
discovery cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch


class ModelTier(StrEnum):
    """Capability tier of one model.

    Lower index == higher capability. ``meets_minimum`` is
    ``tier_index(t) <= tier_index(min)`` so a flagship ``T0`` meets a
    ``T2`` minimum but a free ``T3`` does not meet a ``T1`` minimum.

    Order matters: ``parse_tier_table`` accepts every tier listed here
    and no others. Adding a tier is a backward-compatible change for
    tasks whose ``min_tier`` was already set in storage; existing tiers
    keep their semantics.
    """

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


def tier_index(tier: ModelTier) -> int:
    """Return the integer index of a tier (``T0=0`` … ``T3=3``).

    Defined as a free function so callers do not have to know about the
    declaration order of the enum. The index is the only thing
    ``capability_fit`` and ``meets_minimum`` need.
    """

    return _TIER_ORDER.index(tier)


def meets_minimum(tier: ModelTier, min_tier: ModelTier) -> bool:
    """``True`` iff ``tier`` is at or above ``min_tier`` in capability."""

    return tier_index(tier) <= tier_index(min_tier)


#: Strict order used by :func:`tier_index`. Defined as a tuple (not a
#: class attribute on the StrEnum) because StrEnum rejects class-level
#: mutable defaults, and ``list.index`` is what makes ``tier_index``
#: O(1) without spelling out four branches.
_TIER_ORDER: tuple[ModelTier, ...] = (
    ModelTier.T0,
    ModelTier.T1,
    ModelTier.T2,
    ModelTier.T3,
)


@dataclass(frozen=True)
class TierEntry:
    """One row of the table: a tier and the capability caps it satisfies.

    ``caps`` is the set of named capabilities this tier is certified
    for (``"vision"``, ``"tools"``, ``"long_context"``, …). Today the
    recommender only compares ``tier``; the field is here for WP3's
    per-capability score components to consume without a schema bump.
    """

    tier: ModelTier
    caps: tuple[str, ...] = ()


#: The default fallback the lookup returns when no pattern matches.
DEFAULT_TIER_ENTRY = TierEntry(tier=ModelTier.T1, caps=())


@dataclass(frozen=True)
class TierTable:
    """A resolved set of ``{target_key_pattern: TierEntry}``.

    Construction goes through :func:`parse_tier_table` or
    :func:`merge_tier_tables` — direct ``TierTable(entries={...})`` is
    allowed for tests but skips the "every entry is well-formed" check
    a host-owned file deserves.
    """

    entries: Mapping[str, TierEntry]
    default: TierEntry = DEFAULT_TIER_ENTRY

    def lookup(self, target_key: str) -> tuple[TierEntry, str]:
        """Resolve ``target_key`` to ``(entry, reason)``.

        ``reason`` is one of ``"exact"``, ``"glob"``, ``"default"``. The
        recommender surfaces the reason on the candidate view so the UI
        can show "(via pattern)" when an exact match was absent.

        ``target_key`` is matched against the pattern table with
        ``fnmatch``; the glob ``*`` matches any character (including
        dashes). Patterns are evaluated in arbitrary dict order; the
        **longest** matching pattern wins so a specific override beats
        a broad default.
        """

        if target_key in self.entries:
            return self.entries[target_key], "exact"

        best_pattern: str | None = None
        for pattern in self.entries:
            if fnmatch(target_key, pattern):
                if best_pattern is None or len(pattern) > len(best_pattern):
                    best_pattern = pattern

        if best_pattern is not None:
            return self.entries[best_pattern], "glob"

        return self.default, "default"


def _validate_entry(key: str, value: object) -> TierEntry:
    """Turn one parsed ``{pattern: {tier, caps}}`` row into a TierEntry.

    Raises ``ValueError`` with the pattern key and the offending field
    name so a host can find the bad row without reading a stack trace.
    """

    if not isinstance(value, dict):
        raise ValueError(
            f"tiers[{key!r}] must be an object with 'tier' and 'caps' keys, "
            f"got {type(value).__name__}"
        )
    if set(value.keys()) - {"tier", "caps"}:
        extra = sorted(set(value.keys()) - {"tier", "caps"})
        raise ValueError(f"tiers[{key!r}] has unknown field(s) {extra}; allowed: tier, caps")

    raw_tier = value.get("tier")
    if not isinstance(raw_tier, str):
        raise ValueError(f"tiers[{key!r}].tier must be a string, got {type(raw_tier).__name__}")
    try:
        tier = ModelTier(raw_tier)
    except ValueError:
        allowed = ", ".join(t.value for t in ModelTier)
        raise ValueError(f"tiers[{key!r}].tier={raw_tier!r} is not one of: {allowed}") from None

    raw_caps = value.get("caps", ())
    if not isinstance(raw_caps, list):
        raise ValueError(
            f"tiers[{key!r}].caps must be a list of strings, got {type(raw_caps).__name__}"
        )
    caps_list: list[str] = []
    for index, cap in enumerate(raw_caps):
        if not isinstance(cap, str):
            raise ValueError(
                f"tiers[{key!r}].caps[{index}] must be a string, got {type(cap).__name__}"
            )
        if not cap:
            raise ValueError(f"tiers[{key!r}].caps[{index}] must be non-empty")
        caps_list.append(cap)

    return TierEntry(tier=tier, caps=tuple(caps_list))


def parse_tier_table(raw: Mapping[str, object]) -> TierTable:
    """Parse a JSON-style dict into a :class:`TierTable`.

    The expected shape is::

        {
          "version": 1,
          "tiers": {
            "<pattern>": {"tier": "T0"|"T1"|"T2"|"T3", "caps": [<str>, ...]},
            ...
          }
        }

    A missing ``version`` key, a non-1 version, a non-dict ``tiers``,
    or any malformed entry raises :class:`ValueError` with the field
    name in the message. A pattern that matches no live target is
    silently kept — the table is allowed to be speculative.
    """

    if not isinstance(raw, Mapping):
        raise ValueError(f"tier table must be a JSON object, got {type(raw).__name__}")

    version = raw.get("version")
    if version != 1:
        raise ValueError(
            f"tier table version must be 1, got {version!r}; this daemon only knows version 1"
        )

    tiers_raw = raw.get("tiers")
    if not isinstance(tiers_raw, Mapping):
        raise ValueError(f"tier table 'tiers' must be an object, got {type(tiers_raw).__name__}")

    entries: dict[str, TierEntry] = {}
    for pattern, value in tiers_raw.items():
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"tier table pattern keys must be non-empty strings, got {pattern!r}")
        entries[pattern] = _validate_entry(pattern, value)

    return TierTable(entries=entries)


def merge_tier_tables(default: TierTable, override: TierTable) -> TierTable:
    """Merge two tables — ``override`` patterns replace ``default`` ones.

    The two tables may target the same ``execution_target_id`` via
    different patterns; the merged table's ``lookup`` resolves each
    target key with the union of patterns, so a host-installed glob
    and a config-installed exact match compose naturally. The default
    :class:`TierEntry` from ``override`` wins if both define one;
    otherwise the one from ``default`` survives.
    """

    entries: dict[str, TierEntry] = dict(default.entries)
    entries.update(override.entries)
    return TierTable(entries=entries, default=override.default)


#: The default tier table the host-owned ``model-tiers.json`` is seeded
#: from. Patterns use the ``execution_target_id`` format (single dash).
#: M1 WP4: ``opencode-*-free`` covers every free OpenCode Zen SKU with
#: the ``-free`` suffix. ``opencode-big-pickle`` is the one explicit
#: override — the SKU has no suffix but is in
#: ``provider_discovery.PROVIDER_FAMILIES``'s ``free_model_skus`` list
#: (deliberate act #6). ``parse_tier_table`` resolves the longest
#    matching pattern first, so an existing owner-installed pattern
#    that overrides the new entry wins without code change.
DEFAULT_TIER_TABLE_JSON: dict[str, object] = {
    "version": 1,
    "tiers": {
        "zai-coding-plan-*": {"tier": "T1", "caps": ["coding"]},
        "minimax-cn-coding-plan-*": {"tier": "T1", "caps": ["coding"]},
        "minimax-coding-plan-*": {"tier": "T1", "caps": ["coding"]},
        "minimax-*": {"tier": "T1", "caps": []},
        "opencode-*-free": {"tier": "T3", "caps": []},
        "opencode-big-pickle": {"tier": "T3", "caps": []},
    },
}


__all__ = [
    "DEFAULT_TIER_ENTRY",
    "DEFAULT_TIER_TABLE_JSON",
    "ModelTier",
    "TierEntry",
    "TierTable",
    "meets_minimum",
    "merge_tier_tables",
    "parse_tier_table",
    "tier_index",
]
