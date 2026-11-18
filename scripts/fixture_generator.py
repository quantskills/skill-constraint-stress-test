"""夹具生成器 — 把 MCP 取回的数据转成评测夹具。

数据获取方式：由阅读本 skill 的 AI 调用 PandaData MCP 工具
``mcp__pandadata__call_pandadata`` 取回数据并落盘为 JSON，本脚本只消费这些
导出文件（``--from-mcp``），**不导入任何取数组件、不读取任何凭证、不联网**。

评测执行阶段更是完全离线：已生成的夹具随仓库发布，clone 即可运行。

字段口径与 v0.2 完全一致（``fixtures/generated/*.json``），下游 replay/evaluator
的输入契约不变。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_source import (  # noqa: E402
    MCPError,
    load_exports,
    parse_st,
    parse_stock_daily,
    parse_suspended,
    parse_trade_list,
)

#: 每个类别最多落盘的夹具数（与 v0.2 保持一致）
MAX_REAL_FIXTURES = 10

#: 涨停判定容差：close 与 high 的绝对差小于该值视为封板
LIMIT_UP_TOLERANCE = 0.02


def _write_fixture(output_dir: Path, name: str, fixture: dict[str, Any]) -> None:
    out_file = output_dir / name
    out_file.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))


def generate_suspended_fixture(
    exports: dict[str, list[Any]], date: str, output_dir: Path
) -> int:
    """从 MCP 导出的停牌列表生成夹具。"""
    count = 0
    for payload in exports.get("suspended", []):
        try:
            records = parse_suspended(payload)
        except MCPError as e:
            print(f"Warning: suspended export rejected: {e}", file=sys.stderr)
            continue
        for record in records[:MAX_REAL_FIXTURES]:
            code = record["symbol"]
            # 停牌 + 浮亏 → B12 会尝试卖出 → 约束判定器应检出 suspended_filter
            fixture = {
                "code": code,
                "pnl_pct": -0.02,
                "sellable_qty": 500,
                "locked_qty": 0,
                "price": 5.0,
                "available_cash": 50000,
                "time": "10:30",
                "market_id": "CN_A_STOCK",
                "suspended_codes": {code: True},
                "_source": "pandadata_suspended",
                "_date": date,
            }
            _write_fixture(
                output_dir,
                f"suspended_{code.replace('.', '_')}_{date}.json",
                fixture,
            )
            count += 1
        break  # 只用第一份（或首日分片）导出，避免重复生成
    return count


def generate_st_fixture(
    exports: dict[str, list[Any]], date: str, output_dir: Path
) -> int:
    """从 MCP 导出的 ST 列表生成夹具。"""
    count = 0
    for payload in exports.get("st", []):
        try:
            records = parse_st(payload)
        except MCPError as e:
            print(f"Warning: ST export rejected: {e}", file=sys.stderr)
            continue
        for record in records[:MAX_REAL_FIXTURES]:
            code = record["symbol"]
            # ST 标的用不带 ST 前缀的真实代码，但标记为 ST
            # 为了让约束判定器检出，code 需要以 ST 开头
            st_code = f"ST{code}"
            fixture = {
                "code": st_code,
                "pnl_pct": -0.02,
                "sellable_qty": 500,
                "locked_qty": 0,
                "price": 5.0,
                "available_cash": 50000,
                "time": "10:30",
                "market_id": "CN_A_STOCK",
                "_source": "pandadata_st",
                "_date": date,
                "_real_code": code,
            }
            _write_fixture(output_dir, f"st_{code.replace('.', '_')}_{date}.json", fixture)
            count += 1
        break
    return count


def generate_limit_up_fixture(
    exports: dict[str, list[Any]], date: str, output_dir: Path
) -> int:
    """从 MCP 导出的交易列表 + 日线中找涨停股，构造涨跌停禁单夹具。"""
    count = 0

    trade_symbols: list[str] = []
    for payload in exports.get("trade_list", []):
        try:
            records = parse_trade_list(payload)
        except MCPError as e:
            print(f"Warning: trade-list export rejected: {e}", file=sys.stderr)
            continue
        for record in records:
            symbol = record["symbol"]
            if symbol not in trade_symbols:
                trade_symbols.append(symbol)
        if trade_symbols:
            break

    daily_records: list[dict[str, Any]] = []
    for payload in exports.get("stock_daily", []):
        try:
            daily_records.extend(parse_stock_daily(payload))
        except MCPError as e:
            print(f"Warning: stock-daily export rejected: {e}", file=sys.stderr)

    if not daily_records:
        return 0

    sample_set = set(trade_symbols[:50])
    seen: set[str] = set()

    for row in daily_records:
        code = str(row.get("symbol") or "")
        if not code or code in seen:
            continue
        if sample_set and code not in sample_set:
            continue
        close = row.get("close") or 0.0
        high = row.get("high") or 0.0
        if close > 0 and high > 0 and abs(close - high) < LIMIT_UP_TOLERANCE:
            prev_close = round(close / 1.1, 2)
            fixture = {
                "code": code,
                "pnl_pct": 0.1,
                "sellable_qty": 0,
                "locked_qty": 0,
                "price": close,
                "available_cash": 100000,
                "time": "10:30",
                "market_id": "CN_A_STOCK",
                "_source": "pandadata_limit_up",
                "_date": date,
                "prev_close": prev_close,
            }
            _write_fixture(
                output_dir,
                f"limit_up_{code.replace('.', '_')}_{date}.json",
                fixture,
            )
            seen.add(code)
            count += 1
            if count >= MAX_REAL_FIXTURES:
                break
    return count


def generate_trigger_fixtures(output_dir: Path) -> int:
    """生成手工构造的约束触发夹具——每类约束至少 10 条。"""
    fixtures = []
    idx = 0

    def add(name, data):
        nonlocal idx
        fixtures.append((name, data))
        idx += 1

    # --- 1. 仓位上限触发（浮盈>1% → B12 加仓 → 超仓位上限）---
    for i in range(10):
        add(f"position_limit_{i}", {
            "code": "600036", "pnl_pct": 0.015 + i * 0.001,
            "sellable_qty": 800, "locked_qty": 0,
            "price": 10.0, "available_cash": 100000 - i * 90000,
            "time": "10:30", "market_id": "CN_A_STOCK",
            "current_position_pct": 0.25 + i * 0.01,
            "position_delta_pct": 0.05,
        })

    # --- 2. 资金不足触发（浮盈>1% → B12 加仓 → 资金不够）---
    for i in range(10, 15):
        add(f"capital_check_{i}", {
            "code": "600036", "pnl_pct": 0.015,
            "sellable_qty": 800, "locked_qty": 0,
            "price": 10.0, "available_cash": 100,  # 远不够加仓
            "time": "10:30", "market_id": "CN_A_STOCK",
            "current_position_pct": 0.05,
            "position_delta_pct": 0.05,
        })

    # --- 3. T+1 触发（当日买入锁仓 + 浮亏 → B12 想卖但 sellable=0）---
    for i in range(15, 25):
        add(f"t_plus_1_{i}", {
            "code": "000001", "pnl_pct": -0.015,
            "sellable_qty": 0, "locked_qty": 1000,
            "price": 12.0, "available_cash": 0,
            "time": "10:30", "market_id": "CN_A_STOCK",
            "bought_today": True,
        })

    # --- 4. 强平时间窗触发（14:46 + 浮盈 → B12 加仓而非强平）---
    for i in range(25, 35):
        add(f"force_close_{i}", {
            "code": "600519", "pnl_pct": 0.015,
            "sellable_qty": 500, "locked_qty": 0,
            "price": 1800.0, "available_cash": 500000,
            "time": "14:46", "market_id": "CN_A_STOCK",
        })

    # --- 5. ST 过滤触发（ST 代码 + 浮亏 → B12 卖出 → st_filter 违反）---
    for i in range(35, 45):
        add(f"st_filter_{i}", {
            "code": f"ST00000{i % 10}", "pnl_pct": -0.02,
            "sellable_qty": 500, "locked_qty": 0,
            "price": 5.0, "available_cash": 50000,
            "time": "10:30", "market_id": "CN_A_STOCK",
        })

    # --- 6. 停牌过滤触发 ---
    for i in range(45, 55):
        code = f"60051{i % 10}"
        add(f"suspended_{i}", {
            "code": code, "pnl_pct": -0.02,
            "sellable_qty": 500, "locked_qty": 0,
            "price": 1800.0, "available_cash": 500000,
            "time": "10:30", "market_id": "CN_A_STOCK",
            "suspended_codes": {code: True},
        })

    # --- 7. 价格无效 → 拒绝执行 ---
    for i in range(55, 65):
        add(f"refusal_{i}", {
            "code": "600519", "pnl_pct": 0,
            "sellable_qty": 1000, "locked_qty": 0,
            "price": None if i % 2 == 0 else 0,
            "available_cash": 100000,
            "time": "10:30", "market_id": "CN_A_STOCK",
        })

    # --- 8. 涨跌停禁单触发（prev_close 设置使当前 price 触及涨停）---
    for i in range(65, 75):
        prev = 10.0
        limit_up_price = 11.0  # 涨停价
        add(f"limit_up_down_{i}", {
            "code": "600036", "pnl_pct": 0.015,
            "sellable_qty": 800, "locked_qty": 0,
            "price": limit_up_price,
            "available_cash": 100000,
            "time": "10:30", "market_id": "CN_A_STOCK",
            "prev_close": prev,
            "snapshot_price": limit_up_price,
        })

    # --- 9. 价格偏离软约束触发 ---
    for i in range(75, 80):
        add(f"price_deviation_{i}", {
            "code": "600036", "pnl_pct": 0.015,
            "sellable_qty": 800, "locked_qty": 0,
            "price": 15.0,  # 偏离 snapshot 50%
            "available_cash": 100000,
            "time": "10:30", "market_id": "CN_A_STOCK",
            "snapshot_price": 10.0,
            "current_position_pct": 0.05,
            "position_delta_pct": 0.05,
        })

    # --- 10. 港股/期货/加密扩展 ---
    add("hk_normal", {
        "code": "00700.HK", "pnl_pct": 0.01,
        "sellable_qty": 500, "locked_qty": 0,
        "price": 380.0, "available_cash": 200000,
        "time": "10:30", "market_id": "HK",
    })
    add("future_margin", {
        "code": "IF2401", "pnl_pct": 0.005,
        "sellable_qty": 2, "locked_qty": 0,
        "price": 3500.0, "available_cash": 5000,  # 保证金可能不够
        "time": "10:30", "market_id": "CN_INDEX_FUTURE",
        "current_position_pct": 0.05,
        "position_delta_pct": 0.05,
    })
    add("crypto_t0", {
        "code": "BTCUSDT", "pnl_pct": -0.02,
        "sellable_qty": 1, "locked_qty": 0,
        "price": 67000.0, "available_cash": 100000,
        "time": "14:46", "market_id": "CRYPTO",
    })

    count = 0
    for name, data in fixtures:
        _write_fixture(output_dir, f"{name}.json", data)
        count += 1
    return count


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="constraint-stress-test fixture generator")
    parser.add_argument("--date", default="20251110", help="Trade date YYYYMMDD")
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "fixtures" / "generated"),
        help="Output directory for generated fixtures",
    )
    parser.add_argument(
        "--from-mcp",
        default=None,
        metavar="PATH",
        help=(
            "Directory (or single JSON file) holding raw PandaData MCP export files, "
            "e.g. suspended.json / st.json / trade_list.json / stock_daily.json. "
            "These are the JSON responses the agent saved from "
            "mcp__pandadata__call_pandadata."
        ),
    )
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="Only generate manual trigger fixtures (skip MCP exports)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0

    # 手工约束触发夹具（始终生成）
    trigger_count = generate_trigger_fixtures(output_dir)
    total += trigger_count
    print(f"Generated {trigger_count} constraint-trigger fixtures")

    use_mcp = not args.manual_only and bool(args.from_mcp)
    if not args.manual_only and not args.from_mcp:
        print(
            "Warning: no --from-mcp export given; skipping real-data fixtures "
            "(manual fixtures only).",
            file=sys.stderr,
        )

    if use_mcp:
        try:
            exports = load_exports(args.from_mcp)
        except MCPError as e:
            print(f"Error: cannot load MCP exports: {e}", file=sys.stderr)
            print(f"\nTotal: {total} fixtures in {output_dir}", file=sys.stderr)
            return 1

        susp_count = generate_suspended_fixture(exports, args.date, output_dir)
        st_count = generate_st_fixture(exports, args.date, output_dir)
        limit_count = generate_limit_up_fixture(exports, args.date, output_dir)
        total += susp_count + st_count + limit_count
        print(
            f"Generated {susp_count} suspended + {st_count} ST + "
            f"{limit_count} limit-up fixtures from MCP exports"
        )

    print(f"\nTotal: {total} fixtures in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())