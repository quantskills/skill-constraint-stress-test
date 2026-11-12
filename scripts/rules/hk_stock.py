"""港股规则实现。"""

from __future__ import annotations

from typing import Optional

from scripts.market_rules import SettlementCycle


class HKStockRules:
    market_id = "HK"
    min_lot_size = 100  # 默认，实际按标的不同
    settlement_cycle = SettlementCycle.T_PLUS_0
    force_close_time = "15:45"
    trading_hours = ("09:30", "16:00")

    def has_limit_up_down(self) -> bool:
        return False

    def get_limit_prices(
        self, prev_close: float
    ) -> Optional[tuple[float, float]]:
        return None

    def is_suspended(self, code: str, context: dict) -> bool:
        return context.get("suspended_codes", {}).get(code, False)

    def is_st_or_special(self, code: str) -> bool:
        return False

    def requires_margin(self) -> bool:
        return False

    def get_margin_ratio(self) -> float:
        return 0.0

    def get_contract_multiplier(self) -> float:
        return 1.0

    def can_sell_today(self, bought_today: bool) -> bool:
        return True

    def supports_order_type(self, order_type: str) -> bool:
        return order_type in ("limit", "market", "enhanced_limit")

    def get_price_tick(self) -> float:
        return 0.01
