"""决策意图回放接入：外部只提供决策 dict，引擎只做判定与阻断。

协议：``skill-constraint-stress-test/intent-replay@1``。
本模块不读取订单、成交、账户或委托号，也不会执行返回的决策。案例覆盖不足会
显式失败；无法绑定或超出案例出现次数的记录进入 ``unmatched``，CLI 以非零退出。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.injector import InjectionCase, InjectionType  # noqa: E402

PROTOCOL = "skill-constraint-stress-test/intent-replay@1"
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class IntentReplayError(ValueError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _load_payload(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.exists():
        raise IntentReplayError("intents_not_found", f"意图文件不存在：{source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntentReplayError("invalid_json", f"意图文件不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        actual = payload.get("protocol") if isinstance(payload, dict) else None
        raise IntentReplayError(
            "protocol_mismatch", f"协议不匹配：期望 {PROTOCOL}，实际 {actual!r}"
        )
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise IntentReplayError("no_records", "records 必须是非空数组")

    seen_ids: set[str] = set()
    for index, record in enumerate(records):
        _validate_record(record, index)
        record_id = record["record_id"]
        if record_id in seen_ids:
            raise IntentReplayError(
                "duplicate_record_id", f"record_id 重复：{record_id!r}"
            )
        seen_ids.add(record_id)
    return payload


def _validate_record(record: Any, index: int) -> None:
    where = f"records[{index}]"
    if not isinstance(record, dict):
        raise IntentReplayError("invalid_record", f"{where} 必须是对象")
    for key in ("record_id", "code"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise IntentReplayError(
                "invalid_record", f"{where}.{key} 必须是非空字符串"
            )
    if record.get("case_id") is not None and not isinstance(record.get("case_id"), str):
        raise IntentReplayError(
            "invalid_record", f"{where}.case_id 必须是字符串或 null"
        )
    if not isinstance(record.get("time"), str) or not _TIME.fullmatch(record["time"]):
        raise IntentReplayError(
            "invalid_record", f"{where}.time 必须是零填充的 HH:MM"
        )
    action = record.get("action")
    if action not in ("buy", "sell", "hold"):
        raise IntentReplayError(
            "invalid_record", f"{where}.action 必须是 buy/sell/hold"
        )
    qty = record.get("qty_change")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise IntentReplayError(
            "invalid_record", f"{where}.qty_change 必须是整数"
        )
    if (
        (action == "buy" and qty <= 0)
        or (action == "sell" and qty >= 0)
        or (action == "hold" and qty != 0)
    ):
        raise IntentReplayError(
            "invalid_record",
            f"{where} 数量方向不符：buy>0、sell<0、hold=0",
        )
    target = record.get("target_qty")
    if target is not None and (not isinstance(target, int) or isinstance(target, bool)):
        raise IntentReplayError(
            "invalid_record", f"{where}.target_qty 必须是整数或 null"
        )
    if record.get("reason") is not None and not isinstance(record.get("reason"), str):
        raise IntentReplayError(
            "invalid_record", f"{where}.reason 必须是字符串或 null"
        )


def _occurrences(case: InjectionCase) -> int:
    if case.injection_type == InjectionType.DUPLICATE:
        return max(1, int(case.params.get("repeat_n", 1)))
    return 1


def intents_trader(
    intents_path: str | Path,
    cases: list[InjectionCase],
) -> tuple[Callable[[dict, dict], dict], list[InjectionCase], dict[str, Any]]:
    """把外部意图绑定到案例，返回 ``(trader_fn, selected_cases, stats)``。

    record_id 命中案例 ID 时优先；否则使用 case_id 精确命中。每个案例的记录按
    time 稳定排序，同刻记录保持输入顺序。少于案例所需出现次数是硬错误；多出的
    记录和未知案例记录都进入 unmatched，不会被静默丢弃。
    """
    payload = _load_payload(intents_path)
    known = {case.case_id: case for case in cases}
    grouped: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []
    bound_by = {"record_id": 0, "case_id": 0}

    for record in payload["records"]:
        if record["record_id"] in known:
            case_id = record["record_id"]
            bound_by["record_id"] += 1
        elif record.get("case_id") in known:
            case_id = record["case_id"]
            bound_by["case_id"] += 1
        else:
            unmatched.append({**record, "unmatched_reason": "case_not_found"})
            continue
        grouped.setdefault(case_id, []).append(record)

    selected: list[InjectionCase] = []
    expected_records: list[dict[str, Any]] = []
    for case in cases:
        records = grouped.get(case.case_id)
        if records is None:
            continue
        # Python sort 稳定：同 time 保留外部文件中的先后顺序。
        ordered = sorted(records, key=lambda item: item["time"])
        expected = _occurrences(case)
        if len(ordered) < expected:
            raise IntentReplayError(
                "missing_intents",
                f"案例 {case.case_id} 需要 {expected} 条意图，实际只有 {len(ordered)} 条",
                hint="duplicate 案例的每次 repeat 都必须各有一条记录；不得用 hold 补齐",
            )
        for extra in ordered[expected:]:
            unmatched.append({**extra, "unmatched_reason": "occurrence_overflow"})
        selected.append(case)
        expected_records.extend(ordered[:expected])

    stats: dict[str, Any] = {
        "protocol": PROTOCOL,
        "source": str(intents_path),
        "total_records": len(payload["records"]),
        "matched_records": len(expected_records),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "selected_case_ids": [case.case_id for case in selected],
        "bound_by": bound_by,
        "consumed_records": 0,
        "ordering": "per case: stable sort by time, ties preserve input order",
    }
    cursor = 0

    def trader(perturbed: dict, config: dict) -> dict:
        nonlocal cursor
        if cursor >= len(expected_records):
            raise IntentReplayError(
                "unexpected_engine_call", "引擎调用次数超过已绑定意图数"
            )
        record = expected_records[cursor]
        cursor += 1
        stats["consumed_records"] = cursor
        current_qty = int(perturbed.get("sellable_qty", 0) or 0) + int(
            perturbed.get("locked_qty", 0) or 0
        )
        qty = record["qty_change"]
        target_qty = record.get("target_qty")
        decision = {
            "record_id": record["record_id"],
            "code": record["code"],
            "pnl_pct": perturbed.get("pnl_pct", 0),
            "current_qty": current_qty,
            "time": record["time"],
            "action": record["action"],
            "qty_change": qty,
            "target_qty": current_qty + qty if target_qty is None else target_qty,
            "reason": record.get("reason") or "",
        }
        price = perturbed.get("price")
        if isinstance(price, (int, float)) and price > 0:
            decision["price"] = float(price)
        return decision

    trader.__intent_stats__ = stats  # type: ignore[attr-defined]
    return trader, selected, stats


def run(
    intents_path: str | Path,
    *,
    cases_dir: str | Path = "fixtures",
    output_dir: str | Path = "output/intent_replay",
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """经 run_eval 的既有双评估路径回放意图，不写第二套统计逻辑。"""
    from scripts.run_eval import run as run_eval

    report = run_eval(
        cases_dir=str(cases_dir),
        config_path=str(config_path) if config_path is not None else None,
        output_dir=str(output_dir),
        intent_path=str(intents_path),
    )
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Replay external decision intents only")
    parser.add_argument("--intents", required=True, help="intent-replay@1 JSON file")
    parser.add_argument("--cases-dir", default="fixtures")
    parser.add_argument("--output-dir", default="output/intent_replay")
    parser.add_argument("--config")
    args = parser.parse_args()

    try:
        report = run(
            args.intents,
            cases_dir=args.cases_dir,
            output_dir=args.output_dir,
            config_path=args.config,
        )
    except IntentReplayError as exc:
        print(json.dumps({
            "ok": False,
            "error": {"code": exc.code, "message": exc.message, "hint": exc.hint},
        }, ensure_ascii=False))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "runtime_error")
        message = getattr(exc, "message", str(exc))
        hint = getattr(exc, "hint", "")
        print(json.dumps({
            "ok": False,
            "error": {"code": code, "message": message, "hint": hint},
        }, ensure_ascii=False))
        return 2 if code != "runtime_error" else 1

    meta = report["intent_replay"]
    ok = meta["unmatched_count"] == 0
    print(json.dumps({
        "ok": ok,
        "output_dir": args.output_dir,
        "summary": report["summary"],
        "intent_replay": meta,
        "error": None if ok else {
            "code": "unmatched_intents",
            "message": f"{meta['unmatched_count']} 条记录未绑定；详见报告 intent_replay.unmatched",
        },
    }, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
