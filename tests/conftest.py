"""pytest 共享 fixtures。"""

import sys
from pathlib import Path

# 确保 scripts/ 可 import
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pytest


@pytest.fixture
def a_stock_context():
    return {
        "time": "10:30",
        "price": 10.0,
        "snapshot_price": 10.0,
        "prev_close": 10.0,
        "bought_today": False,
        "available_cash": 100000,
        "current_position_pct": 0.1,
        "position_delta_pct": 0.05,
        "max_position_pct": 0.3,
        "price_deviation_threshold": 0.05,
        "suspended_codes": {},
    }


@pytest.fixture
def a_stock_decision():
    return {
        "code": "600519.SH",
        "pnl_pct": 0.02,
        "current_qty": 1000,
        "time": "10:30",
        "action": "buy",
        "qty_change": 100,
        "target_qty": 1100,
        "reason": "[CN_A_STOCK][sellable=1000][cash_req=1000] test",
        "price": 10.0,
    }
