"""商品期货规则实现。"""

from __future__ import annotations

from typing import Optional

from scripts.market_rules import SettlementCycle


class CommodityFutureRules:
    market_id = "CN_COMMODITY_FUTURE"
    min_lot_size = 1
    settlement_cycle = SettlementCycle.T_PLUS_0
    force_close_time = "14:45"
    trading_hours = ("09:00", "15:00")

    def has_limit_up_down(self) -> bool:
        return True

    def get_limit_prices(
        self, prev_close: float
    ) -> Optional[tuple[float, float]]:
        if prev_close <= 0:
            return None
        # 商品期货涨跌停幅度因品种而异，这里用 ±7% 作为默认
        return (round(prev_close * 1.07, 2), round(prev_close * 0.93, 2))

    def is_suspended(self, code: str, context: dict) -> bool:
        return False

    def is_st_or_special(self, code: str) -> bool:
        return False

    def requires_margin(self) -> bool:
        return True

    def get_margin_ratio(self) -> float:
        return 0.10

    def get_contract_multiplier(self) -> float:
        return 10.0  # 默认，实际按品种不同

    def can_sell_today(self, bought_today: bool) -> bool:
        return True

    def supports_order_type(self, order_type: str) -> bool:
        return order_type in ("limit", "market")

    def get_price_tick(self) -> float:
        return 1.0
