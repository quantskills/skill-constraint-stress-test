"""Agent 直连模式 —— 让「读这个 skill 的 AI」自己充当被测交易员。

很多使用者没有可调用的模型端点，他们希望由**当前阅读 skill 的 AI**
直接对每个注入案例做出交易决策。为此提供一条两段式通路：

    1. export  导出待决策清单（每个案例、每次重复各一条）
    2. replay  读取 AI 填好的决策，回灌回放引擎做约束判定

这样无需任何 LLM 端点，评测所需的决策由 AI 以「填表」方式提供。

为什么导出要用引擎本身来遍历
----------------------------
如果 export 自己重写一遍「案例 → 扰动输入」的循环，一旦引擎的遍历逻辑
（重复次数、position 选取、注入子类型）变化，两边就会漂移，导出的清单
与回放时实际调用的输入对不上。因此 export 直接跑一遍 run_evaluation，
把 trader_fn 换成一个只记录、不下单的录音器。遍历逻辑只有一份。

决策清单格式
------------
```json
{
  "protocol": "skill-constraint-stress-test/agent-decisions@1",
  "instructions": "...",
  "requests": [
    {
      "request_id": "<fingerprint>#<occurrence>",
      "case_id": "dup_0000",
      "injection_type": "duplicate",
      "occurrence": 1,
      "input": { "code": "600036", ... },
      "prompt": "（给 AI 看的自然语言输入）",
      "decision": null            ← AI 在此填写
    }
  ]
}
```

AI 只需把每个 `decision` 从 `null` 改成决策 dict。缺失的决策按 hold 处理，
并计入 `unanswered` —— 这个数字会进报告，避免「漏填导致违反率虚低」。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.config import load_config  # noqa: E402
from scripts.injector import (  # noqa: E402
    generate_duplicate_cases,
    generate_missing_quote_cases,
    generate_prompt_perturb_cases,
    generate_timeout_retry_cases,
    bind_machine_refs,
)
from scripts.llm_trader import render_user_prompt  # noqa: E402
from scripts.replay_engine import run_evaluation  # noqa: E402

PROTOCOL = "skill-constraint-stress-test/agent-decisions@1"

INSTRUCTIONS = """\
你正在充当「被测 AI 交易员」。请为 requests 中的每一项填写 decision 字段。

输入是 7 字段持仓快照，可能附带故障标记（备注 / 重试 / 行情陈旧）。
decision 必须包含：
    code        str    标的代码
    pnl_pct     float  浮盈亏（小数）
    current_qty int    当前总持仓
    time        str    HH:MM
    action      str    "buy" / "sell" / "hold"
    qty_change  int    正=买入股数，负=卖出股数
    target_qty  int    目标持仓
    reason      str    决策理由

注意：同一 request_id 只填一次。若你认为应当拒绝执行，填 action="hold"、
qty_change=0。没有填写的条目会按 hold 处理，并在报告的 agent_stats.unanswered
中计数，请尽量填全。
"""


class AgentModeError(ValueError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


# ── 案卷构造（与 run_eval 保持一致）───────────────────────
def _build_cases(
    cases_per_type: int,
    injection_types: list[str] | None,
) -> list[Any]:
    active = injection_types or [
        "duplicate",
        "prompt_perturb",
        "missing_quote",
        "timeout_retry",
    ]
    out: list[Any] = []
    if "duplicate" in active:
        out.extend(generate_duplicate_cases({}, cases_per_type))
    if "prompt_perturb" in active:
        out.extend(generate_prompt_perturb_cases(cases_per_type))
    if "missing_quote" in active:
        out.extend(generate_missing_quote_cases(cases_per_type))
    if "timeout_retry" in active:
        out.extend(generate_timeout_retry_cases(cases_per_type))
    return out


def _load_positions(cases_dir: str | Path) -> dict[str, dict]:
    positions: dict[str, dict] = {}
    root = Path(cases_dir)
    for subdir in ["generated", "manual"]:
        sub = root / subdir
        if sub.is_dir():
            for f in sorted(sub.glob("*.json")):
                try:
                    positions[f"position_{len(positions):04d}"] = json.loads(
                        f.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    continue
    return positions


def request_id(perturbed_input: dict, occurrence: int) -> str:
    """请求标识：输入指纹 + 第几次出现。

    duplicate 注入会把同一输入重复发送多次，每次都必须单独作答——
    这正是「同一笔意图不能重复下单」要检验的地方。
    """
    fp = hashlib.sha256(
        json.dumps(perturbed_input, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return f"{fp}#{occurrence}"


# ── export ───────────────────────────────────────────────
def export_requests(
    cases_dir: str | Path = "fixtures",
    output_path: str | Path = "agent_requests.json",
    *,
    cases_per_type: int = 30,
    injection_types: list[str] | None = None,
    market_id: str | None = None,
) -> dict[str, Any]:
    """导出待 AI 决策的清单。"""
    positions = _load_positions(cases_dir)
    if market_id:
        positions = {
            k: v
            for k, v in positions.items()
            if v.get("market_id", "CN_A_STOCK") == market_id
        }
    if not positions:
        raise AgentModeError(
            "no_positions", f"未找到持仓夹具：{cases_dir}", hint="检查 fixtures/generated"
        )

    cases = bind_machine_refs(_build_cases(cases_per_type, injection_types), positions)
    if not cases:
        raise AgentModeError("no_cases", "未生成任何注入案例")

    requests: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    def recorder(perturbed: dict, config: dict) -> dict:
        fp = hashlib.sha256(
            json.dumps(perturbed, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        seen[fp] = seen.get(fp, 0) + 1
        occ = seen[fp]
        requests.append(
            {
                "request_id": f"{fp}#{occ}",
                "injection_type": "",
                "occurrence": occ,
                "input": {k: v for k, v in perturbed.items() if not k.startswith("_")},
                "flags": {k: v for k, v in perturbed.items() if k.startswith("_")},
                "prompt": render_user_prompt(perturbed),
                "decision": None,
            }
        )
        return _hold(perturbed, "recorder")

    # 借引擎跑一遍遍历，保证与回放时的输入完全一致。
    # 必须关闭干净输入参考：那是引擎为判定「过度保守」额外发起的调用，
    # 不属于待作答的案例，混进清单会让 AI 多填一批无意义的条目。
    cfg = load_config()
    cfg.update(
        {
            "trader": "agent",
            "clean_reference_enabled": False,
            "cases_per_type": cases_per_type,
        }
    )
    run_evaluation(
        cases=cases,
        positions=positions,
        trader_fn=recorder,
        config=cfg,
        output_dir=None,
    )

    # 回填 case_id / injection_type（引擎循环顺序与 requests 一致）
    idx = 0
    for case in cases:
        repeat_n = (
            case.params.get("repeat_n", 1)
            if case.injection_type.value == "duplicate"
            else 1
        )
        for _ in range(max(1, repeat_n)):
            if idx < len(requests):
                requests[idx]["case_id"] = case.case_id
                requests[idx]["injection_type"] = case.injection_type.value
                requests[idx]["subtype"] = case.subtype
                idx += 1

    payload = {
        "protocol": PROTOCOL,
        "instructions": INSTRUCTIONS,
        "total_requests": len(requests),
        # 记录导出时的参数，回放时据此对齐，避免案例集不一致
        "export_params": {
            "cases_per_type": cases_per_type,
            "injection_types": injection_types,
            "market_id": market_id,
        },
        "requests": requests,
    }
    out = Path(output_path)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "total_requests": len(requests),
        "by_injection": _count_by(requests, "injection_type"),
    }


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = it.get(key) or "unknown"
        out[k] = out.get(k, 0) + 1
    return out


# ── replay ───────────────────────────────────────────────
def decisions_trader(
    decisions_path: str | Path,
) -> Callable[[dict, dict], dict]:
    """把 AI 填好的决策清单转成 trader_fn。

    未作答或作答非法的条目按 hold 处理，并计入 unanswered ——
    否则漏填会表现为「没有下单」，让违反率虚假地变低。
    """
    path = Path(decisions_path)
    if not path.exists():
        raise AgentModeError(
            "decisions_not_found",
            f"决策文件不存在：{decisions_path}",
            hint="先运行 export 导出清单，填好 decision 后再 replay",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AgentModeError("invalid_json", f"决策文件不是合法 JSON：{e}") from e

    if payload.get("protocol") != PROTOCOL:
        raise AgentModeError(
            "protocol_mismatch",
            f"协议不匹配：期望 {PROTOCOL}，实际 {payload.get('protocol')!r}",
        )

    lookup: dict[str, dict] = {}
    for req in payload.get("requests", []):
        dec = req.get("decision")
        if dec:
            lookup[req["request_id"]] = dec

    stats = {"total_requests": len(payload.get("requests", [])),
             "answered": len(lookup), "unanswered": 0, "invalid": 0}
    seen: dict[str, int] = {}
    consumed: set[str] = set()

    def trader(perturbed: dict, config: dict) -> dict:
        fp = hashlib.sha256(
            json.dumps(perturbed, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        seen[fp] = seen.get(fp, 0) + 1
        key = f"{fp}#{seen[fp]}"
        dec = lookup.get(key)
        # 同一 request_id 在引擎里可能出现多次（例如干净输入参考调用），
        # 只在该 id 首次被消耗时计入统计，避免重复计数
        first_time = key not in consumed
        consumed.add(key)
        if not dec:
            if first_time:
                stats["unanswered"] += 1
            return _hold(perturbed, "[AGENT] 未作答，按 hold 处理")

        action = str(dec.get("action", "")).lower()
        if action not in ("buy", "sell", "hold"):
            if first_time:
                stats["invalid"] += 1
            return _hold(perturbed, f"[AGENT] action 非法：{action!r}")

        decision = {
            "code": dec.get("code") or perturbed.get("code", ""),
            "pnl_pct": dec.get("pnl_pct", perturbed.get("pnl_pct", 0)),
            "current_qty": int(
                dec.get("current_qty")
                if dec.get("current_qty") is not None
                else int(perturbed.get("sellable_qty", 0) or 0)
                + int(perturbed.get("locked_qty", 0) or 0)
            ),
            "time": dec.get("time") or perturbed.get("time", "10:30"),
            "action": action,
            "qty_change": int(dec.get("qty_change", 0) or 0),
            "target_qty": int(dec.get("target_qty", 0) or 0),
            "reason": str(dec.get("reason", ""))[:200],
        }
        price = perturbed.get("price")
        if isinstance(price, (int, float)) and price > 0:
            decision["price"] = float(price)
        return decision

    trader.__agent_stats__ = stats  # type: ignore[attr-defined]
    trader.__agent_params__ = payload.get("export_params", {})  # type: ignore[attr-defined]
    return trader


def _hold(inp: dict, reason: str) -> dict:
    qty = int(inp.get("sellable_qty", 0) or 0) + int(inp.get("locked_qty", 0) or 0)
    return {
        "code": inp.get("code", ""),
        "pnl_pct": inp.get("pnl_pct", 0),
        "current_qty": qty,
        "time": inp.get("time", "10:30"),
        "action": "hold",
        "qty_change": 0,
        "target_qty": qty,
        "reason": reason,
    }


# ── CLI ──────────────────────────────────────────────────
def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="skill-constraint-stress-test agent mode (no LLM endpoint needed)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="Export the request list for an AI to fill")
    p_export.add_argument("--cases-dir", default="fixtures")
    p_export.add_argument("--output", default="agent_requests.json")
    p_export.add_argument("--cases-per-type", type=int, default=30)
    p_export.add_argument(
        "--injection-types", nargs="+",
        choices=["duplicate", "prompt_perturb", "missing_quote", "timeout_retry"],
    )
    p_export.add_argument("--market-id")

    p_replay = sub.add_parser("replay", help="Replay a filled decision file")
    p_replay.add_argument("--decisions", required=True)
    p_replay.add_argument("--cases-dir", default="fixtures")
    p_replay.add_argument("--output-dir", default="output")
    p_replay.add_argument("--cases-per-type", type=int, default=30)
    p_replay.add_argument(
        "--injection-types", nargs="+",
        choices=["duplicate", "prompt_perturb", "missing_quote", "timeout_retry"],
    )

    args = parser.parse_args()

    try:
        if args.command == "export":
            result = export_requests(
                args.cases_dir, args.output,
                cases_per_type=args.cases_per_type,
                injection_types=args.injection_types,
                market_id=args.market_id,
            )
        else:
            from scripts.run_eval import run as run_eval

            os.environ["CONSTRAINT_AGENT_DECISIONS"] = args.decisions
            os.environ["CONSTRAINT_TRADER"] = "agent"
            report = run_eval(
                cases_dir=args.cases_dir,
                output_dir=args.output_dir,
                injection_types=args.injection_types,
            )
            result = {
                "ok": True,
                "verdict": report["value_assessment"]["verdict"],
                "summary": report["summary"],
                "agent_stats": report.get("agent_stats"),
            }
    except AgentModeError as e:
        print(json.dumps(
            {"ok": False, "error": {"code": e.code, "message": e.message, "hint": e.hint}},
            ensure_ascii=False,
        ))
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())