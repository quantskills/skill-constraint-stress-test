"""决策意图回放接入契约：精确绑定、稳定排序、unmatched 与双评估。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.injector import InjectionCase, InjectionType
from scripts.intent_replay import (
    PROTOCOL,
    IntentReplayError,
    intents_trader,
    run,
)

ROOT = Path(__file__).resolve().parents[1]


def _record(record_id="r1", case_id="case_a", **overrides):
    row = {
        "record_id": record_id,
        "case_id": case_id,
        "time": "10:30",
        "code": "600036",
        "action": "buy",
        "qty_change": 100,
        "target_qty": 900,
        "reason": "synthetic test",
    }
    row.update(overrides)
    return row


def _write(tmp_path, records, protocol=PROTOCOL):
    path = tmp_path / "intents.json"
    path.write_text(json.dumps({"protocol": protocol, "records": records}))
    return path


def _case(case_id="case_a", repeat_n=1):
    return InjectionCase(
        case_id=case_id,
        injection_type=InjectionType.DUPLICATE,
        params={"repeat_n": repeat_n},
        position_ref="position_0000",
    )


class TestIntentReplay:
    def test_example_runs_through_paired_engine(self, tmp_path):
        report = run(
            ROOT / "fixtures/intent_replay/example_intents.json",
            cases_dir=ROOT / "fixtures",
            output_dir=tmp_path,
        )
        assert report["total_events"] == 6
        assert report["intent_replay"]["matched_records"] == 6
        assert report["intent_replay"]["unmatched_count"] == 0
        assert report["summary"]["blocked_on_violation"] == 4
        assert report["summary"]["false_block_rate"] == 0.0
        for name in (
            "events_with_checks.jsonl",
            "events_without_checks.jsonl",
            "pairwise_comparison.jsonl",
        ):
            rows = [json.loads(line) for line in (tmp_path / name).read_text().splitlines()]
            assert len(rows) == 6

    def test_record_id_has_priority_over_case_id(self, tmp_path):
        cases = [_case("case_a"), _case("case_b")]
        path = _write(tmp_path, [_record("case_a", "case_b")])
        _, selected, stats = intents_trader(path, cases)
        assert [case.case_id for case in selected] == ["case_a"]
        assert stats["bound_by"] == {"record_id": 1, "case_id": 0}

    def test_case_id_fallback_and_stable_time_order(self, tmp_path):
        path = _write(tmp_path, [
            _record("late", "case_a", time="10:31"),
            _record("same-first", "case_a", time="10:30"),
            _record("same-second", "case_a", time="10:30"),
        ])
        trader, selected, stats = intents_trader(path, [_case(repeat_n=3)])
        assert len(selected) == 1
        assert stats["bound_by"]["case_id"] == 3
        times = [trader({"code": "600036"}, {})["time"] for _ in range(3)]
        assert times == ["10:30", "10:30", "10:31"]

    def test_unknown_record_is_unmatched_not_dropped(self, tmp_path):
        path = _write(tmp_path, [_record("ghost", "no_such_case")])
        _, selected, stats = intents_trader(path, [_case()])
        assert selected == []
        assert stats["unmatched_count"] == 1
        assert stats["unmatched"][0]["unmatched_reason"] == "case_not_found"

    def test_occurrence_overflow_is_unmatched(self, tmp_path):
        path = _write(tmp_path, [
            _record("r1"), _record("r2", time="10:31"),
        ])
        _, selected, stats = intents_trader(path, [_case(repeat_n=1)])
        assert len(selected) == 1
        assert stats["matched_records"] == 1
        assert stats["unmatched"][0]["unmatched_reason"] == "occurrence_overflow"

    def test_missing_repeat_intent_fails_instead_of_hold(self, tmp_path):
        path = _write(tmp_path, [_record("only-one")])
        with pytest.raises(IntentReplayError) as exc:
            intents_trader(path, [_case(repeat_n=2)])
        assert exc.value.code == "missing_intents"

    def test_protocol_mismatch_fails(self, tmp_path):
        path = _write(tmp_path, [_record()], protocol="other@1")
        with pytest.raises(IntentReplayError) as exc:
            intents_trader(path, [_case()])
        assert exc.value.code == "protocol_mismatch"

    def test_quantity_direction_is_validated(self, tmp_path):
        path = _write(tmp_path, [_record(action="sell", qty_change=100)])
        with pytest.raises(IntentReplayError) as exc:
            intents_trader(path, [_case()])
        assert exc.value.code == "invalid_record"

    def test_cli_unmatched_is_nonzero_and_report_is_written(self, tmp_path):
        intents = _write(tmp_path, [
            _record("ok", "timeout_0000", action="hold", qty_change=0),
            _record("ghost", "no_such_case", action="hold", qty_change=0),
        ])
        output = tmp_path / "out"
        proc = subprocess.run(
            [
                sys.executable,
                "scripts/intent_replay.py",
                "--intents",
                str(intents),
                "--cases-dir",
                "fixtures",
                "--output-dir",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "unmatched_intents"
        report = json.loads((output / "report.json").read_text())
        assert report["intent_replay"]["unmatched_count"] == 1
        assert report["value_assessment"]["verdict"]["reason"] == "unmatched_intent_records"
