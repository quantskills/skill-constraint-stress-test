"""夹具生成器 — 离线调用 pandadata 生成真实分布的测试夹具。

仅在夹具生成阶段使用 panda_data，评测执行阶段不调用任何外部服务。

v0.2: 修正 API 签名（date→start_date/end_date），扩充约束触发夹具覆盖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_first(primary: str, fallback: str) -> str:
    """优先读取主环境变量，缺失时回退到历史变量名。"""
    import os

    return os.environ.get(primary) or os.environ.get(fallback) or ""


def _try_init_pandadata() -> Any:
    """尝试初始化 panda_data，失败时返回 None。"""
    try:
        import os
        import panda_data

        username = _env_first("PANDA_DATA_USERNAME", "DEFAULT_USERNAME")
        password = _env_first("PANDA_DATA_PASSWORD", "DEFAULT_PASSWORD")
        base_url = _env_first("PANDA_DATA_BASE_URL", "JAVA_SERVICE_BASE_URL")

        if not all([username, password, base_url]):
            env_file = Path.home() / ".pandadata" / "pandadata.env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key, val = line.split("=", 1)
                        os.environ.setdefault(key.strip(), val.strip())
                username = _env_first("PANDA_DATA_USERNAME", "DEFAULT_USERNAME")
                password = _env_first("PANDA_DATA_PASSWORD", "DEFAULT_PASSWORD")
                base_url = _env_first("PANDA_DATA_BASE_URL", "JAVA_SERVICE_BASE_URL")

        if not all([username, password, base_url]):
            return None

        panda_data.init_token(
            username=username,
            password=password,
            base_url=base_url,
        )
        return panda_data
    except Exception:
        return None


def generate_suspended_fixture(
    panda_data: Any, date: str, output_dir: Path
) -> int:
    """从真实数据生成停牌标的夹具。"""
    count = 0
    try:
        suspended = panda_data.get_suspended_list(start_date=date, end_date=date, is_suspended=True)
        if suspended is not None and hasattr(suspended, "head"):
            codes = suspended[suspended["is_suspended"] == True]["symbol"].tolist()
            for code in codes[:10]:
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
                out_file = output_dir / f"suspended_{code.replace('.', '_')}_{date}.json"
                out_file.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
                count += 1
    except Exception as e:
        print(f"Warning: failed to generate suspended fixtures: {e}", file=sys.stderr)
    return count


def generate_st_fixture(
    panda_data: Any, date: str, output_dir: Path
) -> int:
    """从真实数据生成 ST 标的夹具。"""
    count = 0
    try:
        st_list = panda_data.get_st_list(start_date=date, end_date=date, is_st=True)
        if st_list is not None and hasattr(st_list, "head"):
            codes = st_list[st_list["is_st"] == True]["symbol"].tolist()
            for code in codes[:10]:
                # ST 标的用不带 ST 前缀的真实代码，但标记为 ST
                # B12 能识别这类代码（如 000004.SZ），约束判定器通过 is_st_or_special 检查
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
                out_file = output_dir / f"st_{code.replace('.', '_')}_{date}.json"
                out_file.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
                count += 1
    except Exception as e:
        print(f"Warning: failed to generate ST fixtures: {e}", file=sys.stderr)
    return count


def generate_limit_up_fixture(
    panda_data: Any, date: str, output_dir: Path
) -> int:
    """从真实日线数据中找涨停股，构造涨跌停禁单夹具。"""
    count = 0
    try:
        # 取一批样本代码
        trade_list = panda_data.get_trade_list(start_date=date, end_date=date)
        if trade_list is None or len(trade_list) == 0:
            return 0
        sample_codes = trade_list["symbol"].head(50).tolist()

        daily = panda_data.get_stock_daily(
            symbol=sample_codes, start_date=date, end_date=date,
            fields=["open", "high", "low", "close"],
        )
        if daily is None or len(daily) == 0:
            return 0

        for _, row in daily.iterrows():
            code = str(row.get("symbol", ""))
            close = float(row.get("close", 0))
            high = float(row.get("high", 0))
            if close > 0 and high > 0 and abs(close - high) < 0.02:
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
                    "prev_close": prev_close,
                    "snapshot_price": close,
                    "_source": "pandadata_limit_up",
                    "_date": date,
                }
                out_file = output_dir / f"limit_up_{code.replace('.', '_')}_{date}.json"
                out_file.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
                count += 1
                if count >= 10:
                    break
    except Exception as e:
        print(f"Warning: failed to generate limit-up fixtures: {e}", file=sys.stderr)
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
        out_file = output_dir / f"{name}.json"
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
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
        "--manual-only",
        action="store_true",
        help="Only generate manual trigger fixtures (skip pandadata)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total = 0

    # 手工约束触发夹具（始终生成）
    trigger_count = generate_trigger_fixtures(output_dir)
    total += trigger_count
    print(f"Generated {trigger_count} constraint-trigger fixtures")

    if not args.manual_only:
        panda_data = _try_init_pandadata()
        if panda_data is None:
            print(
                "Warning: panda_data not available, skipping real data fixtures.",
                file=sys.stderr,
            )
        else:
            susp_count = generate_suspended_fixture(panda_data, args.date, output_dir)
            st_count = generate_st_fixture(panda_data, args.date, output_dir)
            limit_count = generate_limit_up_fixture(panda_data, args.date, output_dir)
            total += susp_count + st_count + limit_count
            print(f"Generated {susp_count} suspended + {st_count} ST + {limit_count} limit-up fixtures from pandadata")

    print(f"\nTotal: {total} fixtures in {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
