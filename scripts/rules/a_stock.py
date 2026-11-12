"""A股（沪深主板/科创板/创业板）规则实现。"""

from __future__ import annotations

from typing import Optional

from scripts.market_rules import SettlementCycle


class AStockRules:
    market_id = "CN_A_STOCK"
    min_lot_size = 100
    settlement_cycle = SettlementCycle.T_PLUS_1
    force_close_time = "14:45"
    trading_hours = ("09:30", "15:00")

    def has_limit_up_down(self) -> bool:
        return True

    def get_limit_prices(
        self, prev_close: float
    ) -> Optional[tuple[float, float]]:
        if prev_close <= 0:
            return None
        return (round(prev_close * 1.1, 2), round(prev_close * 0.9, 2))

    def is_suspended(self, code: str, context: dict) -> bool:
        return context.get("suspended_codes", {}).get(code, False)

    def is_st_or_special(self, code: str) -> bool:
        return code.startswith("ST") or code.startswith("*ST")

    def requires_margin(self) -> bool:
        return False

    def get_margin_ratio(self) -> float:
        return 0.0

    def get_contract_multiplier(self) -> float:
        return 1.0

    def can_sell_today(self, bought_today: bool) -> bool:
        return not bought_today

    def supports_order_type(self, order_type: str) -> bool:
        return order_type in ("limit", "market")

    def get_price_tick(self) -> float:
        return 0.01
