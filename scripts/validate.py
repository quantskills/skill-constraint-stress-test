"""输出契约校验器 — 校验事件日志 JSONL 的 schema 完整性。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_EVENT_FIELDS = [
    "event_id",
    "run_id",
    "timestamp",
    "config_hash",
    "injection_type",
    "input_fingerprint",
    "decision",
    "constraint_checks",
    "violations",
    "is_duplicate_action",
    "is_refusal",
]

# 新增的分类字段：存在时必须是 bool，缺失则按 False 处理
# （保持对 v0.1 事件日志的向后兼容）
OPTIONAL_BOOL_FIELDS = [
    "should_refuse",
    "false_pass",
    "over_conservative",
    "is_correct_refusal",
    "decision_changed",
    "baseline_is_order",
    "blocked_on_violation",
    "blocked_without_violation",
    "cascade_unblocked",
]

VALID_INJECTION_TYPES = {
    "duplicate",
    "prompt_perturb",
    "missing_quote",
    "timeout_retry",
}

VALID_SEVERITIES = {"hard", "soft"}


def validate_event(event: dict[str, Any], line_num: int) -> list[str]:
    """校验单条事件记录，返回错误列表（空 = 通过）。"""
    errors: list[str] = []

    # 必填字段
    for field_name in REQUIRED_EVENT_FIELDS:
        if field_name not in event:
            errors.append(f"line {line_num}: missing required field '{field_name}'")

    # injection_type 枚举
    inj_type = event.get("injection_type", "")
    if inj_type and inj_type not in VALID_INJECTION_TYPES:
        errors.append(
            f"line {line_num}: invalid injection_type '{inj_type}', "
            f"must be one of {VALID_INJECTION_TYPES}"
        )

    # constraint_checks 结构
    checks = event.get("constraint_checks", [])
    if not isinstance(checks, list):
        errors.append(f"line {line_num}: constraint_checks must be a list")
    else:
        for i, check in enumerate(checks):
            if not isinstance(check, dict):
                errors.append(
                    f"line {line_num}: constraint_checks[{i}] must be a dict"
                )
                continue
            if "constraint_name" not in check:
                errors.append(
                    f"line {line_num}: constraint_checks[{i}] missing 'constraint_name'"
                )
            if "passed" not in check:
                errors.append(
                    f"line {line_num}: constraint_checks[{i}] missing 'passed'"
                )
            severity = check.get("severity", "hard")
            if severity not in VALID_SEVERITIES:
                errors.append(
                    f"line {line_num}: constraint_checks[{i}] invalid severity '{severity}'"
                )

    # violations 必须是列表
    violations = event.get("violations")
    if violations is not None and not isinstance(violations, list):
        errors.append(f"line {line_num}: violations must be a list")

    # is_duplicate_action / is_refusal 必须是 bool
    for bool_field in ("is_duplicate_action", "is_refusal") + tuple(
        OPTIONAL_BOOL_FIELDS
    ):
        val = event.get(bool_field)
        if val is not None and not isinstance(val, bool):
            errors.append(f"line {line_num}: {bool_field} must be boolean")

    # 分类字段的自洽性：false_pass / over_conservative 与 is_refusal 互斥
    if event.get("false_pass") and event.get("is_refusal"):
        errors.append(
            f"line {line_num}: false_pass and is_refusal cannot both be true"
        )
    if event.get("over_conservative") and not event.get("is_refusal"):
        errors.append(
            f"line {line_num}: over_conservative requires is_refusal=true"
        )

    return errors


def validate_jsonl(filepath: str | Path) -> dict[str, Any]:
    """校验整个 JSONL 文件，返回结构化报告。"""
    filepath = Path(filepath)
    report: dict[str, Any] = {
        "file": str(filepath),
        "ok": True,
        "total_lines": 0,
        "valid_lines": 0,
        "errors": [],
        "event_ids": set(),
        "config_hashes": set(),
    }

    if not filepath.exists():
        report["ok"] = False
        report["errors"].append(f"File not found: {filepath}")
        return _finalize_report(report)

    seen_ids: set[str] = set()

    with open(filepath, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            report["total_lines"] += 1

            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                report["errors"].append(f"line {line_num}: invalid JSON: {e}")
                report["ok"] = False
                continue

            # 事件 ID 唯一性
            event_id = event.get("event_id", "")
            if event_id in seen_ids:
                report["errors"].append(
                    f"line {line_num}: duplicate event_id '{event_id}'"
                )
                report["ok"] = False
            seen_ids.add(event_id)
            report["event_ids"].add(event_id)

            # config_hash 收集
            ch = event.get("config_hash", "")
            if ch:
                report["config_hashes"].add(ch)

            # 字段校验
            field_errors = validate_event(event, line_num)
            if field_errors:
                report["errors"].extend(field_errors)
                report["ok"] = False
            else:
                report["valid_lines"] += 1

    # 分母非零断言
    if report["total_lines"] == 0:
        report["errors"].append("Empty file: no events found")
        report["ok"] = False

    return _finalize_report(report)


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    """将 set 转为 list 以便 JSON 序列化。"""
    report["event_ids"] = sorted(report["event_ids"])
    report["config_hashes"] = sorted(report["config_hashes"])
    report["unique_event_count"] = len(report["event_ids"])
    report["unique_config_count"] = len(report["config_hashes"])
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="constraint-stress-test event log contract validator")
    parser.add_argument("file", help="Path to JSONL event log file")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    args = parser.parse_args()

    report = validate_jsonl(args.file)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "✅ PASS" if report["ok"] else "❌ FAIL"
        print(f"{status} — {report['valid_lines']}/{report['total_lines']} valid events")
        if report["errors"]:
            print(f"\n  {len(report['errors'])} error(s):")
            for err in report["errors"][:20]:
                print(f"    • {err}")
            if len(report["errors"]) > 20:
                print(f"    ... and {len(report['errors']) - 20} more")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
