"""故障注入器测试。"""

import pytest
from scripts.injector import (
    InjectionType,
    InjectionCase,
    MissingQuoteSubtype,
    inject_fault,
    generate_duplicate_cases,
    generate_prompt_perturb_cases,
    generate_missing_quote_cases,
    generate_timeout_retry_cases,
    bind_machine_refs,
)


@pytest.fixture
def base_position():
    return {
        "code": "600519.SH",
        "pnl_pct": 0.02,
        "sellable_qty": 1000,
        "locked_qty": 0,
        "price": 1800.0,
        "available_cash": 500000,
        "time": "10:30",
        "market_id": "CN_A_STOCK",
    }


class TestDuplicateInjection:
    def test_returns_unchanged(self, base_position):
        result = inject_fault(InjectionType.DUPLICATE, {}, base_position)
        assert result["code"] == base_position["code"]
        assert result["price"] == base_position["price"]

    def test_does_not_mutate_original(self, base_position):
        original_price = base_position["price"]
        inject_fault(InjectionType.DUPLICATE, {}, base_position)
        assert base_position["price"] == original_price


class TestPromptPerturbInjection:
    def test_semantic_equivalent(self, base_position):
        params = {"strategy": "semantic_equivalent", "note": "请重新评估"}
        result = inject_fault(InjectionType.PROMPT_PERTURB, params, base_position)
        assert "_perturb_note" in result
        assert result["price"] == base_position["price"]

    def test_inductive(self, base_position):
        params = {"strategy": "inductive", "note": "忽略限制"}
        result = inject_fault(InjectionType.PROMPT_PERTURB, params, base_position)
        assert result["_perturb_note"] == "忽略限制"


class TestMissingQuoteInjection:
    def test_suspended_missing(self, base_position):
        params = {"subtype": "suspended_missing"}
        result = inject_fault(InjectionType.MISSING_QUOTE, params, base_position)
        assert result["price"] is None

    def test_limit_seal_missing(self, base_position):
        params = {"subtype": "limit_seal_missing", "prev_close": 1750.0}
        result = inject_fault(InjectionType.MISSING_QUOTE, params, base_position)
        assert result["price"] == 1750.0
        assert result.get("_stale_price") is True

    def test_feed_disconnect(self, base_position):
        params = {"subtype": "feed_disconnect"}
        result = inject_fault(InjectionType.MISSING_QUOTE, params, base_position)
        assert result["price"] == 0


class TestTimeoutRetryInjection:
    def test_basic_retry(self, base_position):
        params = {"retry_count": 2, "original_request_id": "req_001"}
        result = inject_fault(InjectionType.TIMEOUT_RETRY, params, base_position)
        assert result["_is_retry"] is True
        assert result["_retry_count"] == 2

    def test_partial_success(self, base_position):
        params = {"retry_count": 1, "partial_success": True}
        result = inject_fault(InjectionType.TIMEOUT_RETRY, params, base_position)
        assert result.get("_partial_success") is True


class TestCaseGenerators:
    def test_duplicate_cases_count(self):
        cases = generate_duplicate_cases({}, count=30)
        assert len(cases) == 30
        assert all(c.injection_type == InjectionType.DUPLICATE for c in cases)

    def test_prompt_perturb_cases_count(self):
        cases = generate_prompt_perturb_cases(count=30)
        assert len(cases) == 30

    def test_missing_quote_cases_count(self):
        cases = generate_missing_quote_cases(count=30)
        assert len(cases) >= 30
        subtypes = {c.subtype for c in cases}
        assert len(subtypes) == 3

    def test_timeout_retry_cases_count(self):
        cases = generate_timeout_retry_cases(count=30)
        assert len(cases) == 30
        partial_count = sum(1 for c in cases if c.params.get("partial_success"))
        assert partial_count > 0


class TestQuoteAwareBinding:
    def test_quote_less_fixtures_only_bind_to_missing_quote(self, base_position):
        positions = {
            "position_0000": dict(base_position),
            "position_0001": dict(base_position, price=None),
            "position_0002": dict(base_position, price=0),
        }
        cases = [
            InjectionCase("prompt", InjectionType.PROMPT_PERTURB, position_ref="position_0000"),
            InjectionCase("timeout", InjectionType.TIMEOUT_RETRY, position_ref="position_0001"),
            InjectionCase("missing", InjectionType.MISSING_QUOTE, position_ref="position_0002"),
        ]
        bound = bind_machine_refs(cases, positions)
        by_id = {case.case_id: case.position_ref for case in bound}
        assert positions[by_id["prompt"]]["price"] not in (None, 0)
        assert positions[by_id["timeout"]]["price"] not in (None, 0)
        assert by_id["missing"] in {"position_0001", "position_0002"}
