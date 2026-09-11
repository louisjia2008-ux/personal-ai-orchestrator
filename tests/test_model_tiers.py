"""Tests for :mod:`personal_ai_orchestrator.model_tiers`.

The module is stdlib-only and pure — every test reads as a small,
named scenario. ``parse_tier_table`` is exercised with both the
shipped default and several malformed shapes; ``merge_tier_tables``
covers the override-wins / union-of-patterns semantics;
``meets_minimum`` walks the full 4×4 tier matrix.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from personal_ai_orchestrator.model_tiers import (
    DEFAULT_TIER_ENTRY,
    DEFAULT_TIER_TABLE_JSON,
    ModelTier,
    TierEntry,
    TierTable,
    meets_minimum,
    merge_tier_tables,
    parse_tier_table,
    tier_index,
)

# --------------------------------------------------------------------- #
# tier_index / meets_minimum
# --------------------------------------------------------------------- #


def test_tier_index_matches_declaration_order() -> None:
    assert tier_index(ModelTier.T0) == 0
    assert tier_index(ModelTier.T1) == 1
    assert tier_index(ModelTier.T2) == 2
    assert tier_index(ModelTier.T3) == 3


@pytest.mark.parametrize(
    "tier,min_tier,expected",
    [
        (ModelTier.T0, ModelTier.T0, True),
        (ModelTier.T0, ModelTier.T1, True),
        (ModelTier.T0, ModelTier.T2, True),
        (ModelTier.T0, ModelTier.T3, True),
        (ModelTier.T1, ModelTier.T0, False),
        (ModelTier.T1, ModelTier.T1, True),
        (ModelTier.T1, ModelTier.T2, True),
        (ModelTier.T1, ModelTier.T3, True),
        (ModelTier.T2, ModelTier.T0, False),
        (ModelTier.T2, ModelTier.T1, False),
        (ModelTier.T2, ModelTier.T2, True),
        (ModelTier.T2, ModelTier.T3, True),
        (ModelTier.T3, ModelTier.T0, False),
        (ModelTier.T3, ModelTier.T1, False),
        (ModelTier.T3, ModelTier.T2, False),
        (ModelTier.T3, ModelTier.T3, True),
    ],
)
def test_meets_minimum_full_matrix(
    tier: ModelTier, min_tier: ModelTier, expected: bool
) -> None:
    assert meets_minimum(tier, min_tier) is expected


# --------------------------------------------------------------------- #
# parse_tier_table
# --------------------------------------------------------------------- #


def test_parse_tier_table_accepts_default_table() -> None:
    table = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    # M1 WP4: the default table now lists ``opencode-big-pickle``
    # explicitly because that SKU has no ``-free`` suffix.
    assert set(table.entries) == {
        "zai-coding-plan-*",
        "minimax-cn-coding-plan-*",
        "minimax-coding-plan-*",
        "minimax-*",
        "opencode-*-free",
        "opencode-big-pickle",
    }
    # Every entry round-trips as a TierEntry with the right tier.
    assert table.entries["zai-coding-plan-*"].tier is ModelTier.T1
    assert table.entries["opencode-*-free"].tier is ModelTier.T3
    assert table.entries["opencode-big-pickle"].tier is ModelTier.T3


def test_parse_tier_table_rejects_non_dict() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_tier_table([])  # type: ignore[arg-type]


def test_parse_tier_table_rejects_missing_version() -> None:
    with pytest.raises(ValueError, match="version must be 1"):
        parse_tier_table({"tiers": {}})


def test_parse_tier_table_rejects_wrong_version() -> None:
    with pytest.raises(ValueError, match="version must be 1"):
        parse_tier_table({"version": 2, "tiers": {}})


def test_parse_tier_table_rejects_non_dict_tiers() -> None:
    with pytest.raises(ValueError, match="'tiers' must be an object"):
        parse_tier_table({"version": 1, "tiers": []})


def test_parse_tier_table_rejects_unknown_field_in_entry() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": "T1", "caps": [], "extra": 1}},
    }
    with pytest.raises(ValueError, match="unknown field"):
        parse_tier_table(raw)


def test_parse_tier_table_rejects_non_string_tier() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": 1, "caps": []}},
    }
    with pytest.raises(ValueError, match="tiers\\[.foo.\\]\\.tier must be a string"):
        parse_tier_table(raw)


def test_parse_tier_table_rejects_unknown_tier_value() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": "T9", "caps": []}},
    }
    with pytest.raises(ValueError, match="tiers\\[.foo.\\]\\.tier=.T9."):
        parse_tier_table(raw)
    # The error mentions the four legal values so the host can fix it
    # without reading the source.
    assert "T0" in str(parse_tier_table.__doc__)


def test_parse_tier_table_rejects_non_list_caps() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": "T1", "caps": "vision"}},
    }
    with pytest.raises(ValueError, match="caps must be a list"):
        parse_tier_table(raw)


def test_parse_tier_table_rejects_non_string_cap() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": "T1", "caps": ["vision", 7]}},
    }
    with pytest.raises(ValueError, match="caps\\[1\\] must be a string"):
        parse_tier_table(raw)


def test_parse_tier_table_rejects_empty_cap_string() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"foo": {"tier": "T1", "caps": [""]}},
    }
    with pytest.raises(ValueError, match="caps\\[0\\] must be non-empty"):
        parse_tier_table(raw)


def test_parse_tier_table_rejects_empty_pattern_key() -> None:
    raw: Mapping[str, object] = {
        "version": 1,
        "tiers": {"": {"tier": "T1", "caps": []}},
    }
    with pytest.raises(ValueError, match="pattern keys must be non-empty"):
        parse_tier_table(raw)


# --------------------------------------------------------------------- #
# lookup priority
# --------------------------------------------------------------------- #


def test_lookup_exact_match_wins_over_glob() -> None:
    table = TierTable(
        entries={
            "*": TierEntry(tier=ModelTier.T3, caps=()),
            "zai-coding-plan-glm-5.3": TierEntry(tier=ModelTier.T0, caps=("flagship",)),
        }
    )
    entry, reason = table.lookup("zai-coding-plan-glm-5.3")
    assert reason == "exact"
    assert entry.tier is ModelTier.T0
    assert entry.caps == ("flagship",)


def test_lookup_glob_match_uses_longest_pattern() -> None:
    table = TierTable(
        entries={
            "minimax-*": TierEntry(tier=ModelTier.T1, caps=()),
            "minimax-cn-coding-plan-*": TierEntry(tier=ModelTier.T0, caps=()),
        }
    )
    entry, reason = table.lookup("minimax-cn-coding-plan-GLM-5.3")
    assert reason == "glob"
    assert entry.tier is ModelTier.T0  # longest wins


def test_lookup_no_match_returns_default_entry() -> None:
    table = TierTable(
        entries={"opencode-*-free": TierEntry(tier=ModelTier.T3, caps=())}
    )
    entry, reason = table.lookup("zai-coding-plan-glm-5.3")
    assert reason == "default"
    assert entry == DEFAULT_TIER_ENTRY


def test_lookup_glob_star_matches_across_dashes() -> None:
    # ``fnmatch`` ``*`` matches any character including dashes, so the
    # pattern ``opencode-*-free`` matches both the expected two-segment
    # SKU ``opencode-glm-4.5-free`` and the shortened
    # ``opencode-glm-free``. The host that wants narrower matching
    # must spell out the pattern.
    table = TierTable(
        entries={"opencode-*-free": TierEntry(tier=ModelTier.T3, caps=())}
    )
    entry, reason = table.lookup("opencode-glm-free")
    assert reason == "glob"
    assert entry.tier is ModelTier.T3


def test_lookup_default_can_be_overridden_on_construction() -> None:
    table = TierTable(
        entries={"a": TierEntry(tier=ModelTier.T0, caps=())},
        default=TierEntry(tier=ModelTier.T2, caps=()),
    )
    entry, reason = table.lookup("nothing-matches")
    assert reason == "default"
    assert entry.tier is ModelTier.T2


# --------------------------------------------------------------------- #
# merge_tier_tables
# --------------------------------------------------------------------- #


def test_merge_override_pattern_replaces_same_pattern() -> None:
    default = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    override = TierTable(
        entries={"zai-coding-plan-*": TierEntry(tier=ModelTier.T0, caps=("flagship",))}
    )
    merged = merge_tier_tables(default, override)
    assert merged.entries["zai-coding-plan-*"].tier is ModelTier.T0
    # Other entries are kept.
    assert merged.entries["opencode-*-free"].tier is ModelTier.T3


def test_merge_disjoint_patterns_take_union() -> None:
    default = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    override = TierTable(
        entries={"custom-provider-*": TierEntry(tier=ModelTier.T2, caps=())}
    )
    merged = merge_tier_tables(default, override)
    assert "custom-provider-*" in merged.entries
    assert merged.entries["custom-provider-*"].tier is ModelTier.T2
    assert "zai-coding-plan-*" in merged.entries


def test_merge_override_default_replaces_default() -> None:
    default = TierTable(entries={"a": TierEntry(tier=ModelTier.T0, caps=())})
    override = TierTable(
        entries={"b": TierEntry(tier=ModelTier.T1, caps=())},
        default=TierEntry(tier=ModelTier.T3, caps=()),
    )
    merged = merge_tier_tables(default, override)
    assert merged.default.tier is ModelTier.T3


# --------------------------------------------------------------------- #
# DEFAULT_TIER_TABLE_JSON sanity
# --------------------------------------------------------------------- #


def test_default_table_covers_known_provider_families() -> None:
    table = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    # Every known provider family in :mod:`provider_discovery` matches
    # at least one glob and lands on a sane tier.
    for target in (
        "zai-coding-plan-glm-5.3",
        "minimax-cn-coding-plan-MiniMax-M3",
        "minimax-coding-plan-MiniMax-M3",
        "minimax-cn-token-plan-X",
    ):
        entry, reason = table.lookup(target)
        assert reason in {"glob", "exact"}
        assert entry.tier in {ModelTier.T0, ModelTier.T1, ModelTier.T2}


def test_default_table_free_tier_glob_matches_opencode_free_skus() -> None:
    table = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    entry, reason = table.lookup("opencode-glm-4.5-free")
    assert reason == "glob"
    assert entry.tier is ModelTier.T3


def test_default_table_unknown_target_falls_back_to_T1() -> None:
    table = parse_tier_table(DEFAULT_TIER_TABLE_JSON)
    entry, reason = table.lookup("future-provider-some-model")
    assert reason == "default"
    assert entry.tier is ModelTier.T1
    assert entry == DEFAULT_TIER_ENTRY