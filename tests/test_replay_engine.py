"""回放引擎测试（v0.2 统计口径修正后）。"""

import json
import tempfile
from pathlib import Path

import pytest
from scripts.replay_engine import run_evaluation, _compute_config_hash, _mcnemar_test, _confidence_interval
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
    def test_all_same(self):
        events = [{"violations": []}] * 10
        result = _mcnemar_test(events, events)
        assert result["chi2"] == 0.0
        assert result["p_value"] == 1.0

    def test_blocking_effect(self):
        without = [{"violations": ["x"]}] * 5 + [{"violations": []}] * 5
        with_checks = [{"violations": []}] * 10
        result = _mcnemar_test(with_checks, without)
        assert result["contingency_table"]["only_without"] == 5
        assert result["significant_at_005"] is True


class TestConfidenceInterval:
    def test_zero_total(self):
        ci = _confidence_interval(0, 0)
        assert ci["point"] == 0.0

    def test_normal(self):
        ci = _confidence_interval(5, 100)
        assert 0 < ci["lower"] < ci["point"] < ci["upper"] < 1
