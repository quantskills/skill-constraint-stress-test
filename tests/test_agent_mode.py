"""Agent 直连模式测试 —— 由阅读 skill 的 AI 自行填写决策，无需 LLM 端点。"""

import json
from pathlib import Path

import pytest

from scripts.agent_mode import (
    PROTOCOL,
    AgentModeError,
    _build_cases,
    decisions_trader,
    export_requests,
)


@pytest.fixture
def exported(tmp_path):
    out = tmp_path / "req.json"
    result = export_requests(
        cases_dir="fixtures", output_path=out, cases_per_type=1
    )
    return out, result


class TestExport:
    def test_exports_requests(self, exported):
        path, result = exported
        assert result["ok"] is True
        assert result["total_requests"] > 0
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["protocol"] == PROTOCOL
        assert len(data["requests"]) == result["total_requests"]

    def test_records_export_params(self, exported):
        """必须记录导出参数，回放时据此对齐案例集。"""
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["export_params"]["cases_per_type"] == 1

    def test_requests_have_null_decision(self, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        assert all(r["decision"] is None for r in data["requests"])

    def test_duplicate_occurrences_numbered(self, exported):
        """duplicate 注入的每次重复各自一条，不能被折叠。"""
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        dups = [r for r in data["requests"] if r["injection_type"] == "duplicate"]
        assert len(dups) == 3                     # repeat_n 默认 3
        assert [d["occurrence"] for d in dups] == [1, 2, 3]
        assert len({d["request_id"] for d in dups}) == 3

    def test_internal_flags_split_out(self, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data["requests"]:
            assert not any(k.startswith("_") for k in r["input"])

    def test_missing_quote_included(self, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        mq = [r for r in data["requests"] if r["injection_type"] == "missing_quote"]
        assert mq, "缺失行情案例必须出现在清单里"


class TestDecisionsTrader:
    def _write(self, tmp_path, requests):
        p = tmp_path / "dec.json"
        p.write_text(json.dumps({
            "protocol": PROTOCOL,
            "export_params": {"cases_per_type": 1},
            "requests": requests,
        }, ensure_ascii=False), encoding="utf-8")
        return p

    def test_protocol_mismatch_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text(json.dumps({"protocol": "other", "requests": []}))
        with pytest.raises(AgentModeError) as ei:
            decisions_trader(p)
        assert ei.value.code == "protocol_mismatch"

    def test_missing_file_raises(self):
        with pytest.raises(AgentModeError) as ei:
            decisions_trader("/nonexistent/dec.json")
        assert ei.value.code == "decisions_not_found"

    def test_answered_decision_used(self, tmp_path, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        first = data["requests"][0]
        first["decision"] = {
            "code": first["input"]["code"], "pnl_pct": 0,
            "current_qty": 0, "time": "10:00",
            "action": "buy", "qty_change": 100, "target_qty": 100,
            "reason": "test",
        }
        dec = self._write(tmp_path, data["requests"])
        trader = decisions_trader(dec)
        d = trader(first["input"], {})
        assert d["action"] == "buy"
        assert d["qty_change"] == 100

    def test_unanswered_falls_back_to_hold(self, tmp_path):
        dec = self._write(tmp_path, [{
            "request_id": "abc#1", "input": {"code": "X", "price": 1.0},
            "decision": None,
        }])
        trader = decisions_trader(dec)
        d = trader({"code": "X", "price": 1.0}, {})
        assert d["action"] == "hold"
        assert trader.__agent_stats__["unanswered"] == 1

    def test_invalid_action_falls_back_to_hold(self, tmp_path, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        data["requests"][0]["decision"] = {"action": "short"}
        dec = self._write(tmp_path, data["requests"])
        trader = decisions_trader(dec)
        d = trader(data["requests"][0]["input"], {})
        assert d["action"] == "hold"
        assert trader.__agent_stats__["invalid"] == 1

    def test_params_exposed_for_runtime_alignment(self, exported):
        path, _ = exported
        data = json.loads(path.read_text(encoding="utf-8"))
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            tmp = f.name
        trader = decisions_trader(tmp)
        assert trader.__agent_params__["cases_per_type"] == 1
        Path(tmp).unlink()


class TestEndToEnd:
    def test_full_loop_via_env(self, tmp_path, monkeypatch):
        """export → 填表 → replay，全流程不触碰任何 LLM 端点。"""
        from scripts.build import run

        out = tmp_path / "req.json"
        export_requests(cases_dir="fixtures", output_path=out, cases_per_type=1)
        data = json.loads(out.read_text(encoding="utf-8"))

        # 模拟 AI 作答：一律买入
        for r in data["requests"]:
            qty = int(r["input"].get("sellable_qty", 0)) + int(
                r["input"].get("locked_qty", 0)
            )
            r["decision"] = {
                "code": r["input"].get("code", ""), "pnl_pct": 0,
                "current_qty": qty, "time": "10:30",
                "action": "buy", "qty_change": 100, "target_qty": qty + 100,
                "reason": "always buy",
            }
        filled = tmp_path / "filled.json"
        filled.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setenv("CONSTRAINT_AGENT_DECISIONS", str(filled))
        monkeypatch.setenv("CONSTRAINT_TRADER", "agent")

        report = run({
            "cases_dir": "fixtures",
            "output_dir": str(tmp_path / "out"),
        })
        assert report["agent_stats"]["answered"] == len(data["requests"])
        assert report["agent_stats"]["unanswered"] == 0
        assert report["agent_stats"]["answer_rate"] == 1.0
        assert report["value_assessment"]["trader_meta"]["kind"] == "agent"
        # 一律买入应触发违规
        assert report["summary"]["violation_rate_baseline"] > 0

    def test_low_answer_rate_degrades_verdict(self, tmp_path, monkeypatch):
        """作答率过低必须降级，否则违反率会被系统性低估。"""
        from scripts.build import run

        out = tmp_path / "req.json"
        export_requests(cases_dir="fixtures", output_path=out, cases_per_type=1)
        data = json.loads(out.read_text(encoding="utf-8"))
        # 只答第一条
        first = data["requests"][0]
        qty = int(first["input"].get("sellable_qty", 0))
        first["decision"] = {
            "code": first["input"].get("code", ""), "pnl_pct": 0,
            "current_qty": qty, "time": "10:30",
            "action": "buy", "qty_change": 100, "target_qty": qty + 100,
            "reason": "x",
        }
        filled = tmp_path / "partial.json"
        filled.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setenv("CONSTRAINT_AGENT_DECISIONS", str(filled))
        monkeypatch.setenv("CONSTRAINT_TRADER", "agent")
        report = run({
            "cases_dir": "fixtures",
            "output_dir": str(tmp_path / "out2"),
        })
        assert report["agent_stats"]["answer_rate"] < 0.8
        v = report["value_assessment"]["verdict"]
        assert v["verdict"] == "inconclusive"
        assert v["reason"] == "low_answer_rate"