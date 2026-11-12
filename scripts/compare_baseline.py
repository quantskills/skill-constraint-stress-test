"""冻结基线 —— 持久化一次运行结果，供后续 run 做跨运行比对。

用法：
    # 冻结当前运行结果
    python scripts/compare_baseline.py freeze output/report.json

    # 与冻结基线比对（新 run vs 基线）
    python scripts/compare_baseline.py compare output/report.json --against baselines/<name>.json

    # 列出已冻结基线
    python scripts/compare_baseline.py list

冻结基线的意义：同一次 run 内的「有/无校验层」对照只证明「这层有没有用」，
跨 run 的冻结基线才能证明「改了代码/换了模型之后，结果变好还是变差」。

可比性由 config_hash 保证：哈希不一致时比对会明确标记 incompatible，
而不是给出一个看似合理但无意义的差值。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASELINE_DIR = REPO_ROOT / "baselines"

# 参与跨 run 比对的指标
COMPARED_METRICS = [
    "violation_rate_baseline",
    "violation_rate_with_checks",
    "duplicate_rate_baseline",
    "duplicate_rate_with_checks",
    "false_pass_rate_baseline",
    "false_pass_rate_with_checks",
    "over_conservative_rate_baseline",
    "over_conservative_rate_with_checks",
    "blocked_count",
    "correct_refusal_count",
]


class BaselineError(ValueError):
    """基线操作失败。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _load_report(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise BaselineError(
            "report_not_found", f"报告文件不存在：{path}", hint="先运行 scripts/build.py"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BaselineError("invalid_json", f"报告不是合法 JSON：{e}") from e


def _baseline_name(report: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        return explicit
    model = report.get("model_id", "unknown")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{model}_{ts}"


def freeze(
    report_path: str | Path,
    name: str | None = None,
    baseline_dir: Path | None = None,
) -> dict[str, Any]:
    """把一次运行结果冻结为基线文件。"""
    report = _load_report(report_path)
    out_dir = baseline_dir or BASELINE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    frozen = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "name": _baseline_name(report, name),
        "run_id": report.get("run_id"),
        "model_id": report.get("model_id"),
        "config_hash": report.get("config_hash"),
        "total_events": report.get("total_events"),
        "comparability": report.get("comparability", {}),
        "summary": report.get("summary", {}),
        "effect_size": report.get("effect_size", {}),
        "value_assessment": report.get("value_assessment", {}),
        "applicability": report.get("applicability", {}),
    }

    out_path = out_dir / f"{frozen['name']}.json"
    out_path.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "baseline": str(out_path), "name": frozen["name"]}


def compare(
    report_path: str | Path,
    baseline_path: str | Path,
) -> dict[str, Any]:
    """把当前 run 与冻结基线比对。

    可比性判定：
    - config_hash 相同 → comparable，给出逐项 delta
    - config_hash 不同 → incompatible，只列出差异字段，不给结论
    """
    current = _load_report(report_path)
    baseline = _load_report(baseline_path)

    cur_hash = current.get("config_hash")
    base_hash = baseline.get("config_hash")
    comparable = bool(cur_hash) and cur_hash == base_hash

    cur_summary = current.get("summary", {})
    base_summary = baseline.get("summary", {})

    deltas: dict[str, dict[str, Any]] = {}
    for metric in COMPARED_METRICS:
        if metric not in cur_summary and metric not in base_summary:
            continue
        cur_val = cur_summary.get(metric)
        base_val = base_summary.get(metric)
        if isinstance(cur_val, (int, float)) and isinstance(base_val, (int, float)):
            deltas[metric] = {
                "baseline": base_val,
                "current": cur_val,
                "delta": round(cur_val - base_val, 6),
            }
        else:
            deltas[metric] = {"baseline": base_val, "current": cur_val, "delta": None}

    # 可比性差异字段
    cur_cmp = current.get("comparability", {})
    base_cmp = baseline.get("comparability", {})
    changed_fields = [
        field
        for field in cur_cmp.get("fixed_fields", [])
        if cur_cmp.get(field) != base_cmp.get(field)
    ]

    # 价值判定变化
    cur_verdict = current.get("value_assessment", {}).get("verdict", {})
    base_verdict = baseline.get("value_assessment", {}).get("verdict", {})

    return {
        "ok": True,
        "comparable": comparable,
        "current": {
            "run_id": current.get("run_id"),
            "model_id": current.get("model_id"),
            "config_hash": cur_hash,
        },
        "baseline": {
            "run_id": baseline.get("run_id"),
            "model_id": baseline.get("model_id"),
            "config_hash": base_hash,
            "frozen_at": baseline.get("frozen_at"),
        },
        "changed_fields": changed_fields,
        "deltas": deltas,
        "verdict": {
            "baseline": base_verdict.get("verdict"),
            "current": cur_verdict.get("verdict"),
            "changed": base_verdict.get("verdict") != cur_verdict.get("verdict"),
        },
        "note": (
            "两组配置一致，delta 可直接解读"
            if comparable
            else "config_hash 不一致，delta 仅供参考，不得据此下结论"
        ),
    }


def list_baselines(baseline_dir: Path | None = None) -> dict[str, Any]:
    out_dir = baseline_dir or BASELINE_DIR
    if not out_dir.is_dir():
        return {"ok": True, "count": 0, "baselines": []}
    items = []
    for p in sorted(out_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items.append(
            {
                "name": data.get("name", p.stem),
                "path": str(p),
                "model_id": data.get("model_id"),
                "config_hash": data.get("config_hash"),
                "frozen_at": data.get("frozen_at"),
                "verdict": data.get("value_assessment", {})
                .get("verdict", {})
                .get("verdict"),
            }
        )
    return {"ok": True, "count": len(items), "baselines": items}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="constraint-stress-test frozen baseline tool"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_freeze = sub.add_parser("freeze", help="Freeze a report as a baseline")
    p_freeze.add_argument("report", help="Path to report.json")
    p_freeze.add_argument("--name", help="Baseline name (default: <model>_<timestamp>)")

    p_cmp = sub.add_parser("compare", help="Compare a report against a baseline")
    p_cmp.add_argument("report", help="Path to report.json")
    p_cmp.add_argument("--against", required=True, help="Path to baseline json")

    sub.add_parser("list", help="List frozen baselines")

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    try:
        if args.command == "freeze":
            result = freeze(args.report, args.name)
        elif args.command == "compare":
            result = compare(args.report, args.against)
        else:
            result = list_baselines()
    except BaselineError as e:
        print(
            json.dumps(
                {"ok": False, "error": {"code": e.code, "message": e.message,
                                        "hint": e.hint}},
                ensure_ascii=False,
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())