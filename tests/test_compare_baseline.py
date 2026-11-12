"""冻结基线工具的测试。"""

import json
import tempfile
from pathlib import Path

import pytest

from scripts.compare_baseline import (
    BaselineError,
    compare,
    freeze,
    list_baselines,
)


def _report(run_id="r1", model="m1", hash_="h1", vr_base=0.2, verdict="positive"):
    return {
        "run_id": run_id,
        "model_id": model,
        "config_hash": hash_,
        "total_events": 100,
        "comparability": {"model_id": model, "fixed_fields": ["model_id"]},
        "summary": {
            "violation_rate_baseline": vr_base,
            "violation_rate_with_checks": 0.0,
            "blocked_count": 20,
        },
        "effect_size": {"arr": vr_base, "rrr": 1.0, "nnt": 5.0},
        "value_assessment": {"verdict": {"verdict": verdict}},
        "applicability": {},
    }


def _write(tmpdir, name, data):
    p = Path(tmpdir) / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestFreeze:
    def test_freeze_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            rp = _write(td, "report.json", _report())
            out = freeze(rp, "my-baseline", baseline_dir=Path(td) / "bl")
            assert out["ok"] is True
            assert Path(out["baseline"]).exists()
            frozen = json.loads(Path(out["baseline"]).read_text())
            assert frozen["name"] == "my-baseline"
            assert frozen["config_hash"] == "h1"

    def test_missing_report_raises(self):
        with pytest.raises(BaselineError) as ei:
            freeze("/nonexistent/report.json")
        assert ei.value.code == "report_not_found"

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td, "bad.json")
            bad.write_text("not json", encoding="utf-8")
            with pytest.raises(BaselineError) as ei:
                freeze(bad)
            assert ei.value.code == "invalid_json"


class TestCompare:
    def test_same_hash_is_comparable(self):
        with tempfile.TemporaryDirectory() as td:
            cur = _write(td, "cur.json", _report(run_id="r2"))
            base = _write(td, "base.json", _report(run_id="r1"))
            res = compare(cur, base)
            assert res["comparable"] is True
            assert res["deltas"]["violation_rate_baseline"]["delta"] == 0.0

    def test_different_hash_not_comparable(self):
        with tempfile.TemporaryDirectory() as td:
            cur = _write(td, "cur.json", _report(hash_="h2"))
            base = _write(td, "base.json", _report(hash_="h1"))
            res = compare(cur, base)
            assert res["comparable"] is False
            assert "不得据此下结论" in res["note"]

    def test_delta_reported(self):
        with tempfile.TemporaryDirectory() as td:
            cur = _write(td, "cur.json", _report(vr_base=0.1))
            base = _write(td, "base.json", _report(vr_base=0.2))
            res = compare(cur, base)
            d = res["deltas"]["violation_rate_baseline"]
            assert d["baseline"] == 0.2
            assert d["current"] == 0.1
            assert d["delta"] == -0.1

    def test_verdict_change_reported(self):
        with tempfile.TemporaryDirectory() as td:
            cur = _write(td, "cur.json", _report(verdict="negative"))
            base = _write(td, "base.json", _report(verdict="positive"))
            res = compare(cur, base)
            assert res["verdict"]["changed"] is True
            assert res["verdict"]["current"] == "negative"

    def test_changed_fields_detected(self):
        with tempfile.TemporaryDirectory() as td:
            cur_data = _report(model="m2", hash_="h2")
            cur_data["comparability"] = {"model_id": "m2", "fixed_fields": ["model_id"]}
            base_data = _report(model="m1", hash_="h1")
            base_data["comparability"] = {"model_id": "m1", "fixed_fields": ["model_id"]}
            cur = _write(td, "cur.json", cur_data)
            base = _write(td, "base.json", base_data)
            res = compare(cur, base)
            assert "model_id" in res["changed_fields"]


class TestList:
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            res = list_baselines(Path(td) / "nope")
            assert res["count"] == 0

    def test_lists_frozen(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "bl"
            rp = _write(td, "report.json", _report())
            freeze(rp, "b1", baseline_dir=d)
            res = list_baselines(d)
            assert res["count"] == 1
            assert res["baselines"][0]["name"] == "b1"
            assert res["baselines"][0]["verdict"] == "positive"


class TestFieldDiff:
    def test_rules_in_prompt_reported_as_changed(self):
        """两轮只差 rules_in_prompt 时，必须明确指出该字段变化。"""
        with tempfile.TemporaryDirectory() as td:
            cur = _report(hash_="h2")
            cur["comparability"] = {
                "trader": "llm",
                "rules_in_prompt": True,
                "fixed_fields": ["trader", "rules_in_prompt", "model_id"],
            }
            base = _report(hash_="h1")
            base["comparability"] = {
                "trader": "llm",
                "rules_in_prompt": False,
                "fixed_fields": ["trader", "rules_in_prompt", "model_id"],
            }
            cp = _write(td, "cur.json", cur)
            bp = _write(td, "base.json", base)
            res = compare(cp, bp)
            assert res["comparable"] is False
            assert "rules_in_prompt" in res["changed_fields"]
