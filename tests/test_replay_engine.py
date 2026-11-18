"""回放引擎测试（v0.2 统计口径修正后）。"""

import json
import math
import tempfile
from pathlib import Path

import pytest
from scripts.replay_engine import (
    run_evaluation,
    _compute_config_hash,
    _mcnemar_test,
    _mcnemar_p_values,
    _MCNEMAR_EXACT_MAX_DISCORDANT,
    _confidence_interval,
)
from scripts.injector import (
    InjectionCase,
    InjectionType,
    generate_duplicate_cases,
)


class TestConfigHash:
    def test_deterministic(self):
        config = {"price_deviation_threshold": 0.05, "max_position_pct": 0.3}
        h1 = _compute_config_hash(config, block_on_violation=True)
        h2 = _compute_config_hash(config, block_on_violation=True)
        assert h1 == h2

    def test_different_block_setting(self):
        config = {"price_deviation_threshold": 0.05}
        h1 = _compute_config_hash(config, block_on_violation=True)
        h2 = _compute_config_hash(config, block_on_violation=False)
        assert h1 != h2


class TestRunEvaluation:
    def test_basic_run(self):
        cases = [
            InjectionCase(
                case_id="test_001",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 1},
                position_ref="pos_0",
            )
        ]
        positions = {
            "pos_0": {
                "code": "600519.SH", "pnl_pct": 0,
                "sellable_qty": 1000, "locked_qty": 0,
                "price": 10.0, "available_cash": 100000,
                "time": "10:30", "market_id": "CN_A_STOCK",
            }
        }
        report = run_evaluation(cases=cases, positions=positions)
        assert "run_id" in report
        assert report["total_events"] >= 1
        assert "summary" in report
        assert "effect_size" in report
        assert "mcnemar_test" in report
        assert "confidence_intervals" in report

    def test_with_output_dir(self):
        cases = [
            InjectionCase(
                case_id="test_002",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 1},
                position_ref="pos_0",
            )
        ]
        positions = {
            "pos_0": {
                "code": "600519.SH", "pnl_pct": 0,
                "sellable_qty": 1000, "locked_qty": 0,
                "price": 10.0, "available_cash": 100000,
                "time": "10:30", "market_id": "CN_A_STOCK",
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            report = run_evaluation(
                cases=cases, positions=positions, output_dir=tmpdir,
            )
            assert Path(tmpdir, "events_with_checks.jsonl").exists()
            assert Path(tmpdir, "events_without_checks.jsonl").exists()
            assert Path(tmpdir, "pairwise_comparison.jsonl").exists()
            assert Path(tmpdir, "report.json").exists()

            with open(Path(tmpdir, "events_with_checks.jsonl")) as f:
                for line in f:
                    event = json.loads(line)
                    assert "event_id" in event
                    assert "constraint_checks" in event
                    assert "was_blocked" in event

    def test_blocking_reduces_violations(self):
        """有校验层应阻断硬约束违反，基线应记录违反。"""
        cases = [
            InjectionCase(
                case_id="block_test",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 1},
                position_ref="pos_over_limit",
            )
        ]
        positions = {
            "pos_over_limit": {
                "code": "600036", "pnl_pct": 0.015,
                "sellable_qty": 800, "locked_qty": 0,
                "price": 10.0, "available_cash": 100000,
                "time": "10:30", "market_id": "CN_A_STOCK",
                "current_position_pct": 0.28,
                "position_delta_pct": 0.05,
            }
        }

        def buy_trader(inp, cfg):
            return {
                "code": inp.get("code", ""), "pnl_pct": inp.get("pnl_pct", 0),
                "current_qty": 800, "time": inp.get("time", "10:30"),
                "action": "buy", "qty_change": 400, "target_qty": 1200,
                "reason": "test buy", "price": inp.get("price", 10.0),
            }

        report = run_evaluation(cases=cases, positions=positions, trader_fn=buy_trader)

        # 基线应有 position_limit 违反
        baseline_violations = report.get("hard_violations_by_constraint_baseline", {})
        assert baseline_violations.get("position_limit", 0) > 0

        # 有校验层应阻断，违反为 0
        blocked_violations = report.get("hard_violations_by_constraint_with_checks", {})
        assert blocked_violations.get("position_limit", 0) == 0

        # 阻断计数 > 0
        assert report["summary"]["blocked_count"] > 0

        # 效应量应为正
        assert report["effect_size"]["arr"] > 0

    def test_empty_positions_uses_default(self):
        cases = [
            InjectionCase(
                case_id="test_003",
                injection_type=InjectionType.DUPLICATE,
                params={},
                position_ref="nonexistent",
            )
        ]
        report = run_evaluation(cases=cases, positions={})
        assert report["total_events"] >= 1

    def test_duplicate_injection_repeats(self):
        cases = [
            InjectionCase(
                case_id="dup_test",
                injection_type=InjectionType.DUPLICATE,
                params={"repeat_n": 3},
                position_ref="pos_0",
            )
        ]
        positions = {
            "pos_0": {
                "code": "600519.SH", "pnl_pct": 0,
                "sellable_qty": 1000, "locked_qty": 0,
                "price": 10.0, "available_cash": 100000,
                "time": "10:30", "market_id": "CN_A_STOCK",
            }
        }
        report = run_evaluation(cases=cases, positions=positions)
        assert report["total_events"] == 3


class TestMcNemarTest:
    """McNemar 的 p 值口径。

    这里锁的是曾经真实存在的一处结论级缺陷：实现把 df=1 卡方的生存函数又除了
    一次 2（`erfc(sqrt(chi2/2)) / 2`），p 被系统性低估一半，`b=5, c=0` 从
    「不显著」被报成「显著」。改回来的同时补上精确二项检验，两条都要有锚点。
    """

    @staticmethod
    def _ev(hard: list[str]):
        return {"hard_violations": hard, "violations": hard}

    def test_all_same(self):
        events = [self._ev([])] * 10
        result = _mcnemar_test(events, events)
        assert result["chi2"] == 0.0
        assert result["p_value"] == 1.0
        assert result["discordant_pairs"] == 0
        assert result["significant_at_005"] is False

    def test_five_blocked_zero_introduced_is_not_significant(self):
        """回归锚点：b=5,c=0 曾经被误判显著（p=0.0368）。"""
        without = [self._ev(["limit_up"])] * 5 + [self._ev([])] * 5
        with_checks = [self._ev([])] * 10
        result = _mcnemar_test(with_checks, without)
        assert result["contingency_table"]["only_without"] == 5
        assert result["contingency_table"]["only_with"] == 0
        # 精确：2·P(X=5|n=5,p=.5) = 2/32；渐进：erfc(sqrt(3.2/2))
        assert result["p_value_exact"] == pytest.approx(0.0625, abs=1e-6)
        assert result["p_value_asymptotic"] == pytest.approx(0.073638, abs=1e-5)
        assert result["test_used"] == "exact_binomial"
        assert result["p_value"] == result["p_value_exact"]
        assert result["significant_at_005"] is False

    def test_asymptotic_p_is_the_chi_square_survival_function(self):
        """p 不得再被除 2：直接和 erfc(sqrt(chi2/2)) 对账。"""
        without = [self._ev(["x"])] * 8 + [self._ev([])] * 2
        with_checks = [self._ev([])] * 10
        result = _mcnemar_test(with_checks, without)
        chi2 = (abs(8 - 0) - 1) ** 2 / 8
        assert result["p_value_asymptotic"] == pytest.approx(
            math.erfc(math.sqrt(chi2 / 2)), rel=1e-3
        )
        # 直接对账函数本身：渐进值必须等于 χ²₁ 生存函数，而不是它的一半
        _, p_asym, _ = _mcnemar_p_values(8, 0)
        assert p_asym == pytest.approx(0.013328, abs=1e-5)

    def test_exact_p_hand_computed_cases(self):
        # b=10, c=0 → 2·(1/2)^10
        without = [self._ev(["x"])] * 10 + [self._ev([])] * 10
        with_checks = [self._ev([])] * 20
        result = _mcnemar_test(with_checks, without)
        assert result["p_value_exact"] == pytest.approx(2 * 0.5 ** 10, abs=1e-6)
        assert result["significant_at_005"] is True

    def test_pairing_basis_is_hard_violations_not_soft(self):
        """只有软约束不同时，不得计入不一致对子——与 _verdict 同口径。"""
        without = [
            {"hard_violations": [], "violations": ["soft_hint"]} for _ in range(5)
        ]
        with_checks = [
            {"hard_violations": [], "violations": []} for _ in range(5)
        ]
        result = _mcnemar_test(with_checks, without)
        assert result["discordant_pairs"] == 0
        assert result["p_value"] == 1.0


    def test_tiny_p_is_not_reported_as_exact_zero(self):
        """b=127,c=0 时精确 p≈1e-38；必须显示成非零有效数字，不能压成 0.0。"""
        n = 127
        without = [self._ev(["x"])] * n + [self._ev([])] * n
        with_checks = [self._ev([])] * (2 * n)
        result = _mcnemar_test(with_checks, without)
        assert result["p_value"] > 0, "极小 p 被压成 0.0，读者会误读为「恰好为 0」"
        assert result["p_value"] < 1e-20
        # 127 > _MCNEMAR_EXACT_MAX_DISCORDANT，走渐进检验：p 量级 ~1e-29，
        # 若断言成精确检验的 ~1e-38 就把判定规则写死了，这里只锁「非零且极小」

    def test_large_discordant_count_uses_asymptotic(self):
        n = _MCNEMAR_EXACT_MAX_DISCORDANT + 5
        without = [self._ev(["x"])] * n + [self._ev([])] * n
        with_checks = [self._ev([])] * (2 * n)
        result = _mcnemar_test(with_checks, without)
        assert result["discordant_pairs"] == n
        assert result["test_used"] == "chi2_continuity_correction"
        assert result["p_value"] == result["p_value_asymptotic"]


class TestConfidenceInterval:
    def test_zero_total(self):
        ci = _confidence_interval(0, 0)
        assert ci["point"] == 0.0

    def test_normal(self):
        ci = _confidence_interval(5, 100)
        assert 0 < ci["lower"] < ci["point"] < ci["upper"] < 1

