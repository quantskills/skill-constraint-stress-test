"""契约校验器测试。"""

import json
import tempfile
from pathlib import Path

import pytest
from scripts.validate import validate_event, validate_jsonl


def _make_valid_event(**overrides):
    event = {
        "event_id": "test-001",
        "run_id": "run-001",
        "timestamp": "2026-01-01T00:00:00Z",
        "config_hash": "abc123",
        "injection_type": "duplicate",
        "input_fingerprint": "fp001",
        "decision": {"action": "hold"},
        "constraint_checks": [],
        "violations": [],
        "is_duplicate_action": False,
        "is_refusal": False,
    }
    event.update(overrides)
    return event


class TestValidateEvent:
    def test_valid_event(self):
        errors = validate_event(_make_valid_event(), 1)
        assert len(errors) == 0

    def test_missing_field(self):
        event = _make_valid_event()
        del event["event_id"]
        errors = validate_event(event, 1)
        assert any("event_id" in e for e in errors)

    def test_invalid_injection_type(self):
        errors = validate_event(_make_valid_event(injection_type="invalid"), 1)
        assert any("injection_type" in e for e in errors)

    def test_invalid_severity(self):
        event = _make_valid_event(
            constraint_checks=[{"constraint_name": "x", "passed": True, "severity": "critical"}]
        )
        errors = validate_event(event, 1)
        assert any("severity" in e for e in errors)

    def test_non_bool_is_duplicate(self):
        errors = validate_event(_make_valid_event(is_duplicate_action="yes"), 1)
        assert any("is_duplicate_action" in e for e in errors)

    def test_block_evidence_fields_must_be_boolean(self):
        names = (
            "baseline_is_order", "blocked_on_violation",
            "blocked_without_violation", "cascade_unblocked",
        )
        for name in names:
            assert validate_event(_make_valid_event(**{name: True}), 1) == []
            errors = validate_event(_make_valid_event(**{name: 1}), 1)
            assert any(name in error for error in errors)


class TestValidateJsonl:
    def test_valid_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_valid_event(event_id="e1")) + "\n")
            f.write(json.dumps(_make_valid_event(event_id="e2")) + "\n")
            f.flush()
            report = validate_jsonl(f.name)
        assert report["ok"] is True
        assert report["valid_lines"] == 2

    def test_duplicate_event_id(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(_make_valid_event(event_id="dup")) + "\n")
            f.write(json.dumps(_make_valid_event(event_id="dup")) + "\n")
            f.flush()
            report = validate_jsonl(f.name)
        assert report["ok"] is False
        assert any("duplicate event_id" in e for e in report["errors"])

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.flush()
            report = validate_jsonl(f.name)
        assert report["ok"] is False

    def test_nonexistent_file(self):
        report = validate_jsonl("/nonexistent/path.jsonl")
        assert report["ok"] is False

    def test_invalid_json_line(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not json\n")
            f.flush()
            report = validate_jsonl(f.name)
        assert report["ok"] is False
