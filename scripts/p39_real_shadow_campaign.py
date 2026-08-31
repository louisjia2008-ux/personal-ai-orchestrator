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
    run_shadow_campaign,
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
    cases: list[DeclarativeShadowCase] = [
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
            case_id="bug_fix_discount",
            task_family="BUG_FIX",
            difficulty_class="small_deterministic",
            required_capabilities={"implementation": 0.8},
            fixture_files=(
                CampaignFile(
                    path="src/pricing.py",
                    content=(
                        "def discounted_cents(cents: int, percent_off: int) -> int:\n"
                        "    return cents - (cents * percent_off // 1000)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_pricing.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from pricing import discounted_cents\n\n\n"
                        "class PricingTests(unittest.TestCase):\n"
                        "    def test_applies_percent_discount(self) -> None:\n"
                        "        self.assertEqual(discounted_cents(2000, 25), 1500)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/pricing.py",),
            verifier_profile=VerifierProfile(
                name="p39-bug-discount-unittest",
                allowed_paths=("src/pricing.py",),
                commands=(_unittest_command(),),
            ),
            worker_prompt=(
                "Fix discounted_cents in src/pricing.py only. percent_off is a normal "
                "percentage, so 25 percent off 2000 cents returns 1500. Do not edit tests. "
                "Run python3 -B -m unittest discover -s tests."
            ),
            task_intent="repair deterministic discount behavior",
        ),
        DeclarativeShadowCase(
            case_id="bug_fix_parse_bool",
            task_family="BUG_FIX",
            difficulty_class="small_deterministic",
            required_capabilities={"implementation": 0.78},
            fixture_files=(
                CampaignFile(
                    path="src/flags.py",
                    content=(
                        "def parse_enabled(value: str) -> bool:\n"
                        "    return value.strip().lower() in {'true', '1'}\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_flags.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from flags import parse_enabled\n\n\n"
                        "class FlagTests(unittest.TestCase):\n"
                        "    def test_accepts_yes(self) -> None:\n"
                        "        self.assertTrue(parse_enabled(' yes '))\n\n"
                        "    def test_rejects_no(self) -> None:\n"
                        "        self.assertFalse(parse_enabled('no'))\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/flags.py",),
            verifier_profile=VerifierProfile(
                name="p39-bug-parse-bool-unittest",
                allowed_paths=("src/flags.py",),
                commands=(_unittest_command(),),
            ),
            worker_prompt=(
                "Fix parse_enabled in src/flags.py only so yes/y/true/1/on are true and "
                "no/false/0/off are false. Keep the function deterministic and do not edit tests."
            ),
            task_intent="repair boolean parsing behavior",
        ),
        DeclarativeShadowCase(
            case_id="bug_fix_median",
            task_family="BUG_FIX",
            difficulty_class="small_deterministic",
            required_capabilities={"implementation": 0.82},
            fixture_files=(
                CampaignFile(
                    path="src/stats.py",
                    content=(
                        "def median(values: list[int]) -> float:\n"
                        "    ordered = values\n"
                        "    mid = len(ordered) // 2\n"
                        "    if len(ordered) % 2:\n"
                        "        return float(ordered[mid])\n"
                        "    return (ordered[mid - 1] + ordered[mid]) / 2\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_stats.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from stats import median\n\n\n"
                        "class StatsTests(unittest.TestCase):\n"
                        "    def test_sorts_before_median(self) -> None:\n"
                        "        self.assertEqual(median([9, 1, 3]), 3.0)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/stats.py",),
            verifier_profile=VerifierProfile(
                name="p39-bug-median-unittest",
                allowed_paths=("src/stats.py",),
                commands=(_unittest_command(),),
            ),
            worker_prompt=(
                "Fix median in src/stats.py only so it sorts a copy of the input before "
                "computing the median. Do not edit tests."
            ),
            task_intent="repair median sorting behavior",
        ),
        DeclarativeShadowCase(
            case_id="bug_fix_date_label",
            task_family="BUG_FIX",
            difficulty_class="small_deterministic",
            required_capabilities={"implementation": 0.76},
            fixture_files=(
                CampaignFile(
                    path="src/dates.py",
                    content=(
                        "def date_label(year: int, month: int, day: int) -> str:\n"
                        "    return f'{year}-{month}-{day}'\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_dates.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from dates import date_label\n\n\n"
                        "class DateTests(unittest.TestCase):\n"
                        "    def test_zero_pads_month_and_day(self) -> None:\n"
                        "        self.assertEqual(date_label(2026, 8, 3), '2026-08-03')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/dates.py",),
            verifier_profile=VerifierProfile(
                name="p39-bug-date-label-unittest",
                allowed_paths=("src/dates.py",),
                commands=(_unittest_command(),),
            ),
            worker_prompt=(
                "Fix date_label in src/dates.py only so month and day are two-digit "
                "zero-padded fields. Do not edit tests."
            ),
            task_intent="repair date label formatting behavior",
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
            case_id="test_add_slug_whitespace",
            task_family="TEST_ADD",
            difficulty_class="small_regression_test",
            required_capabilities={"testing": 0.75},
            fixture_files=(
                CampaignFile(
                    path="src/slugs.py",
                    content=(
                        "def slugify(value: str) -> str:\n"
                        "    return '-'.join(value.strip().lower().split())\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_slugs.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from slugs import slugify\n\n\n"
                        "class SlugTests(unittest.TestCase):\n"
                        "    def test_lowercases_words(self) -> None:\n"
                        "        self.assertEqual(slugify('Hello World'), 'hello-world')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("tests/test_slugs.py",),
            verifier_profile=VerifierProfile(
                name="p39-test-slug-whitespace-unittest",
                allowed_paths=("tests/test_slugs.py",),
                commands=(
                    _python_check(
                        "whitespace-regression-test-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('tests/test_slugs.py').read_text()\n"
                            "assert 'test_collapses_repeated_whitespace' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "The implementation is already correct. Add only a unittest named "
                "test_collapses_repeated_whitespace proving slugify('  A   B  ') == 'a-b'. "
                "Do not edit src/slugs.py."
            ),
            task_intent="add missing slug whitespace regression test",
        ),
        DeclarativeShadowCase(
            case_id="test_add_percent_bounds",
            task_family="TEST_ADD",
            difficulty_class="small_regression_test",
            required_capabilities={"testing": 0.75},
            fixture_files=(
                CampaignFile(
                    path="src/percent.py",
                    content=(
                        "def bounded_percent(value: int) -> int:\n"
                        "    return max(0, min(value, 100))\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_percent.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from percent import bounded_percent\n\n\n"
                        "class PercentTests(unittest.TestCase):\n"
                        "    def test_clamps_negative(self) -> None:\n"
                        "        self.assertEqual(bounded_percent(-3), 0)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("tests/test_percent.py",),
            verifier_profile=VerifierProfile(
                name="p39-test-percent-bounds-unittest",
                allowed_paths=("tests/test_percent.py",),
                commands=(
                    _python_check(
                        "upper-bound-percent-test-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('tests/test_percent.py').read_text()\n"
                            "assert 'test_clamps_above_one_hundred' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "The implementation is already correct. Add only a unittest named "
                "test_clamps_above_one_hundred proving bounded_percent(104) == 100. "
                "Do not edit src/percent.py."
            ),
            task_intent="add missing percent upper-bound regression test",
        ),
        DeclarativeShadowCase(
            case_id="test_add_default_none",
            task_family="TEST_ADD",
            difficulty_class="small_regression_test",
            required_capabilities={"testing": 0.75},
            fixture_files=(
                CampaignFile(
                    path="src/defaults.py",
                    content=(
                        "def default_if_none(value: str | None, fallback: str) -> str:\n"
                        "    return fallback if value is None else value\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_defaults.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from defaults import default_if_none\n\n\n"
                        "class DefaultTests(unittest.TestCase):\n"
                        "    def test_keeps_non_none_value(self) -> None:\n"
                        "        self.assertEqual(default_if_none('x', 'fallback'), 'x')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("tests/test_defaults.py",),
            verifier_profile=VerifierProfile(
                name="p39-test-default-none-unittest",
                allowed_paths=("tests/test_defaults.py",),
                commands=(
                    _python_check(
                        "none-regression-test-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('tests/test_defaults.py').read_text()\n"
                            "assert 'test_uses_fallback_for_none' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "The implementation is already correct. Add only a unittest named "
                "test_uses_fallback_for_none proving default_if_none(None, 'fallback') "
                "returns 'fallback'. Do not edit src/defaults.py."
            ),
            task_intent="add missing default-none regression test",
        ),
        DeclarativeShadowCase(
            case_id="test_add_dedupe_order",
            task_family="TEST_ADD",
            difficulty_class="small_regression_test",
            required_capabilities={"testing": 0.75},
            fixture_files=(
                CampaignFile(
                    path="src/dedupe.py",
                    content=(
                        "def dedupe(values: list[str]) -> list[str]:\n"
                        "    return list(dict.fromkeys(values))\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_dedupe.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from dedupe import dedupe\n\n\n"
                        "class DedupeTests(unittest.TestCase):\n"
                        "    def test_removes_duplicates(self) -> None:\n"
                        "        self.assertEqual(dedupe(['a', 'a']), ['a'])\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("tests/test_dedupe.py",),
            verifier_profile=VerifierProfile(
                name="p39-test-dedupe-order-unittest",
                allowed_paths=("tests/test_dedupe.py",),
                commands=(
                    _python_check(
                        "order-regression-test-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('tests/test_dedupe.py').read_text()\n"
                            "assert 'test_preserves_first_seen_order' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "The implementation is already correct. Add only a unittest named "
                "test_preserves_first_seen_order proving dedupe(['b', 'a', 'b']) "
                "returns ['b', 'a']. Do not edit src/dedupe.py."
            ),
            task_intent="add missing dedupe order regression test",
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
            case_id="refactor_average",
            task_family="REFACTOR",
            difficulty_class="behavior_preserving_structure",
            required_capabilities={"implementation": 0.78, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/average.py",
                    content=(
                        "def average(values: list[int]) -> float:\n"
                        "    cleaned = [value for value in values if value is not None]\n"
                        "    return sum(cleaned) / len(cleaned)\n\n\n"
                        "def count_values(values: list[int]) -> int:\n"
                        "    cleaned = [value for value in values if value is not None]\n"
                        "    return len(cleaned)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_average.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from average import average, count_values\n\n\n"
                        "class AverageTests(unittest.TestCase):\n"
                        "    def test_average_ignores_none(self) -> None:\n"
                        "        self.assertEqual(average([2, None, 4]), 3)\n\n"
                        "    def test_count_ignores_none(self) -> None:\n"
                        "        self.assertEqual(count_values([2, None, 4]), 2)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/average.py",),
            verifier_profile=VerifierProfile(
                name="p39-refactor-average-unittest",
                allowed_paths=("src/average.py",),
                commands=(
                    _python_check(
                        "average-helper-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('src/average.py').read_text()\n"
                            "assert 'def _non_empty_values' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Refactor src/average.py only so filtering non-None values is shared "
                "through a helper named _non_empty_values. Preserve behavior and do not edit tests."
            ),
            task_intent="perform bounded average refactor",
        ),
        DeclarativeShadowCase(
            case_id="refactor_names",
            task_family="REFACTOR",
            difficulty_class="behavior_preserving_structure",
            required_capabilities={"implementation": 0.78, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/names.py",
                    content=(
                        "def display_name(first: str, last: str) -> str:\n"
                        "    return f'{first.strip()} {last.strip()}'\n\n\n"
                        "def initials(first: str, last: str) -> str:\n"
                        "    return f'{first.strip()[0]}{last.strip()[0]}'.upper()\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_names.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from names import display_name, initials\n\n\n"
                        "class NameTests(unittest.TestCase):\n"
                        "    def test_display_name_strips_parts(self) -> None:\n"
                        "        self.assertEqual(\n"
                        "            display_name(' Ada ', ' Lovelace '),\n"
                        "            'Ada Lovelace',\n"
                        "        )\n\n"
                        "    def test_initials_uppercase(self) -> None:\n"
                        "        self.assertEqual(initials(' ada ', ' lovelace '), 'AL')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/names.py",),
            verifier_profile=VerifierProfile(
                name="p39-refactor-names-unittest",
                allowed_paths=("src/names.py",),
                commands=(
                    _python_check(
                        "name-helper-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('src/names.py').read_text()\n"
                            "assert 'def _clean_name' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Refactor src/names.py only so stripping names is shared through a helper "
                "named _clean_name. Preserve behavior and do not edit tests."
            ),
            task_intent="perform bounded names refactor",
        ),
        DeclarativeShadowCase(
            case_id="refactor_query",
            task_family="REFACTOR",
            difficulty_class="behavior_preserving_structure",
            required_capabilities={"implementation": 0.78, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/query.py",
                    content=(
                        "from urllib.parse import urlencode\n\n\n"
                        "def search_url(term: str, page: int) -> str:\n"
                        "    return '/search?' + urlencode({'q': term, 'page': page})\n\n\n"
                        "def export_url(term: str, page: int) -> str:\n"
                        "    return '/export?' + urlencode({'q': term, 'page': page})\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_query.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from query import export_url, search_url\n\n\n"
                        "class QueryTests(unittest.TestCase):\n"
                        "    def test_search_url(self) -> None:\n"
                        "        self.assertEqual(search_url('a b', 2), '/search?q=a+b&page=2')\n\n"
                        "    def test_export_url(self) -> None:\n"
                        "        self.assertEqual(\n"
                        "            export_url('a b', 2),\n"
                        "            '/export?q=a+b&page=2',\n"
                        "        )\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/query.py",),
            verifier_profile=VerifierProfile(
                name="p39-refactor-query-unittest",
                allowed_paths=("src/query.py",),
                commands=(
                    _python_check(
                        "query-helper-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('src/query.py').read_text()\n"
                            "assert 'def _encoded_pairs' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Refactor src/query.py only so query string encoding is shared through a "
                "helper named _encoded_pairs. Preserve behavior and do not edit tests."
            ),
            task_intent="perform bounded query refactor",
        ),
        DeclarativeShadowCase(
            case_id="refactor_inventory",
            task_family="REFACTOR",
            difficulty_class="behavior_preserving_structure",
            required_capabilities={"implementation": 0.78, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/inventory.py",
                    content=(
                        "def order_total(lines: list[dict[str, int]]) -> int:\n"
                        "    return sum(line['qty'] * line['price'] for line in lines)\n\n\n"
                        "def order_tax(lines: list[dict[str, int]]) -> int:\n"
                        "    return sum(line['qty'] * line['price'] for line in lines) // 10\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_inventory.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from inventory import order_tax, order_total\n\n\n"
                        "class InventoryTests(unittest.TestCase):\n"
                        "    def test_total(self) -> None:\n"
                        "        self.assertEqual(order_total([{'qty': 2, 'price': 300}]), 600)\n\n"
                        "    def test_tax(self) -> None:\n"
                        "        self.assertEqual(order_tax([{'qty': 2, 'price': 300}]), 60)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/inventory.py",),
            verifier_profile=VerifierProfile(
                name="p39-refactor-inventory-unittest",
                allowed_paths=("src/inventory.py",),
                commands=(
                    _python_check(
                        "line-total-helper-present",
                        (
                            "from pathlib import Path\n"
                            "text = Path('src/inventory.py').read_text()\n"
                            "assert 'def _line_total' in text\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Refactor src/inventory.py only so line total calculation is shared through "
                "a helper named _line_total. Preserve behavior and do not edit tests."
            ),
            task_intent="perform bounded inventory refactor",
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
        DeclarativeShadowCase(
            case_id="multi_file_checkout_tax",
            task_family="MULTI_FILE_CHANGE",
            difficulty_class="small_two_file_feature",
            required_capabilities={"implementation": 0.82, "testing": 0.65},
            fixture_files=(
                CampaignFile(path="src/tax.py", content="TAX_RATE = 0.1\n"),
                CampaignFile(
                    path="src/checkout.py",
                    content=(
                        "from tax import TAX_RATE\n\n\n"
                        "def total_with_tax(cents: int) -> int:\n"
                        "    return cents + int(cents * TAX_RATE)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_checkout.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from checkout import total_with_tax\n\n\n"
                        "class CheckoutTests(unittest.TestCase):\n"
                        "    def test_total_uses_new_tax_rate(self) -> None:\n"
                        "        self.assertEqual(total_with_tax(1000), 1120)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/tax.py", "src/checkout.py"),
            verifier_profile=VerifierProfile(
                name="p39-multi-checkout-tax-unittest",
                allowed_paths=("src/tax.py", "src/checkout.py"),
                commands=(
                    _python_check(
                        "tax-and-checkout-changed",
                        (
                            "import subprocess\n"
                            "paths = set(\n"
                            "    subprocess.check_output(\n"
                            "        ['git', 'diff', '--name-only', 'HEAD']\n"
                            "    ).decode().split()\n"
                            ")\n"
                            "assert {'src/tax.py', 'src/checkout.py'} <= paths\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Make total_with_tax use a 12 percent tax rate as a bounded two-file "
                "change: update the reusable rate in src/tax.py and adjust src/checkout.py "
                "so the behavior is explicit. Do not edit tests."
            ),
            task_intent="make bounded multi-file checkout tax change",
        ),
        DeclarativeShadowCase(
            case_id="multi_file_config_title",
            task_family="MULTI_FILE_CHANGE",
            difficulty_class="small_two_file_feature",
            required_capabilities={"implementation": 0.82, "testing": 0.65},
            fixture_files=(
                CampaignFile(path="src/config.py", content="DEFAULT_TITLE = 'Untitled'\n"),
                CampaignFile(
                    path="src/render.py",
                    content=(
                        "from config import DEFAULT_TITLE\n\n\n"
                        "def page_title(value: str | None) -> str:\n"
                        "    return value or DEFAULT_TITLE\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_render.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from render import page_title\n\n\n"
                        "class RenderTests(unittest.TestCase):\n"
                        "    def test_default_title_has_prefix(self) -> None:\n"
                        "        self.assertEqual(page_title(None), 'PAO: Untitled')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/config.py", "src/render.py"),
            verifier_profile=VerifierProfile(
                name="p39-multi-config-title-unittest",
                allowed_paths=("src/config.py", "src/render.py"),
                commands=(
                    _python_check(
                        "config-and-render-changed",
                        (
                            "import subprocess\n"
                            "paths = set(\n"
                            "    subprocess.check_output(\n"
                            "        ['git', 'diff', '--name-only', 'HEAD']\n"
                            "    ).decode().split()\n"
                            ")\n"
                            "assert {'src/config.py', 'src/render.py'} <= paths\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Make the default title include a PAO: prefix as a bounded two-file change: "
                "put the reusable prefix/default data in src/config.py and consume it from "
                "src/render.py. Do not edit tests."
            ),
            task_intent="make bounded multi-file default title change",
        ),
        DeclarativeShadowCase(
            case_id="multi_file_routes",
            task_family="MULTI_FILE_CHANGE",
            difficulty_class="small_two_file_feature",
            required_capabilities={"implementation": 0.82, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/slugger.py",
                    content=(
                        "def slug(value: str) -> str:\n"
                        "    return value.strip().lower().replace(' ', '-')\n"
                    ),
                ),
                CampaignFile(
                    path="src/routes.py",
                    content=(
                        "from slugger import slug\n\n\n"
                        "def article_path(title: str) -> str:\n"
                        "    return '/' + slug(title)\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_routes.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from routes import article_path\n\n\n"
                        "class RouteTests(unittest.TestCase):\n"
                        "    def test_article_path_has_articles_prefix(self) -> None:\n"
                        "        self.assertEqual(\n"
                        "            article_path('Hello World'),\n"
                        "            '/articles/hello-world',\n"
                        "        )\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/slugger.py", "src/routes.py"),
            verifier_profile=VerifierProfile(
                name="p39-multi-routes-unittest",
                allowed_paths=("src/slugger.py", "src/routes.py"),
                commands=(
                    _python_check(
                        "slugger-and-routes-changed",
                        (
                            "import subprocess\n"
                            "paths = set(\n"
                            "    subprocess.check_output(\n"
                            "        ['git', 'diff', '--name-only', 'HEAD']\n"
                            "    ).decode().split()\n"
                            ")\n"
                            "assert {'src/slugger.py', 'src/routes.py'} <= paths\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Make article_path return /articles/<slug> as a bounded two-file change: "
                "add reusable path-segment behavior in src/slugger.py and use it in "
                "src/routes.py. Do not edit tests."
            ),
            task_intent="make bounded multi-file article route change",
        ),
        DeclarativeShadowCase(
            case_id="multi_file_metrics_report",
            task_family="MULTI_FILE_CHANGE",
            difficulty_class="small_two_file_feature",
            required_capabilities={"implementation": 0.82, "testing": 0.65},
            fixture_files=(
                CampaignFile(
                    path="src/metrics.py",
                    content=(
                        "def rate(successes: int, attempts: int) -> float:\n"
                        "    return successes / attempts\n"
                    ),
                ),
                CampaignFile(
                    path="src/summary.py",
                    content=(
                        "from metrics import rate\n\n\n"
                        "def pass_rate_label(successes: int, attempts: int) -> str:\n"
                        "    return f'{rate(successes, attempts):.2f}'\n"
                    ),
                ),
                CampaignFile(
                    path="tests/test_summary.py",
                    content=(
                        common_import
                        + "import unittest\n\n"
                        "from summary import pass_rate_label\n\n\n"
                        "class SummaryTests(unittest.TestCase):\n"
                        "    def test_pass_rate_label_is_percent(self) -> None:\n"
                        "        self.assertEqual(pass_rate_label(3, 4), '75%')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                ),
            ),
            expected_changed_paths=("src/metrics.py", "src/summary.py"),
            verifier_profile=VerifierProfile(
                name="p39-multi-metrics-report-unittest",
                allowed_paths=("src/metrics.py", "src/summary.py"),
                commands=(
                    _python_check(
                        "metrics-and-summary-changed",
                        (
                            "import subprocess\n"
                            "paths = set(\n"
                            "    subprocess.check_output(\n"
                            "        ['git', 'diff', '--name-only', 'HEAD']\n"
                            "    ).decode().split()\n"
                            ")\n"
                            "assert {'src/metrics.py', 'src/summary.py'} <= paths\n"
                        ),
                    ),
                    _unittest_command(),
                ),
            ),
            worker_prompt=(
                "Make pass_rate_label return a whole-number percent like 75% as a bounded "
                "two-file change: put reusable percent conversion in src/metrics.py and use "
                "it from src/summary.py. Do not edit tests."
            ),
            task_intent="make bounded multi-file metrics report change",
        ),
    ]
    return tuple(cases)


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
        timed_out = False
        try:
            stdout, stderr = self.process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.process.kill()
            stdout, stderr = self.process.communicate()
        finished_at = datetime.now(UTC)
        exit_code = self.process.returncode
        stderr_tail = stderr[-4000:]
        if timed_out:
            stderr_tail = (
                f"WORKER_TIMEOUT after {self.timeout_seconds} seconds\n" + stderr_tail
            )[-4000:]
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
            timed_out=timed_out,
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


def probe_workers(repo_root: Path) -> dict[str, object]:
    probes: dict[str, object] = {}
    codex_path = shutil.which("codex")
    probes["codex"] = {
        "cli_available": codex_path is not None,
        "version": None
        if codex_path is None
        else _probe_command([codex_path, "--version"], cwd=repo_root),
        "existing_auth_usable": "deferred_to_real_campaign_runs",
    }
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
    parser.add_argument("--max-observations", type=int, default=20)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--resume-deferred", action="store_true")
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
    probes = probe_workers(repo_root)
    campaign = run_shadow_campaign(
        campaign_root=args.campaign_root,
        campaign_id="p39-real-shadow-quality-campaign",
        cases=selected,
        worker_profile=CODEX_PROFILE,
        worker_launcher=_launch_codex_worker,
        repo_head=repo_head,
        resume_deferred=args.resume_deferred,
    )
    results = list(campaign.results)
    for result in results:
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
    for case_id in campaign.deferred_case_ids:
        print(f"{case_id}\tDEFERRED_QUOTA\tNO_MODEL_EXECUTION\tNone\tNone")

    report_path = write_campaign_report(
        campaign_root=args.campaign_root,
        report_name="p39-real-shadow-quality-campaign-report.json",
        results=results,
        provider_probe_results=probes,
    )
    print(report_path)
    return 0 if results or campaign.deferred_case_ids else 2


if __name__ == "__main__":
    raise SystemExit(main())
