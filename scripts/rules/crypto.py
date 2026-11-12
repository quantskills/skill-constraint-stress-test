"""加密货币规则实现 — 7×24、无涨跌停、T+0、无最小单位限制。"""

from __future__ import annotations

from typing import Optional

from scripts.market_rules import SettlementCycle


class CryptoRules:
    market_id = "CRYPTO"
    min_lot_size = 1
    settlement_cycle = SettlementCycle.T_PLUS_0
    force_close_time = "23:59"
    trading_hours = ("00:00", "23:59")

    def has_limit_up_down(self) -> bool:
        return False

    def get_limit_prices(
        self, prev_close: float
    ) -> Optional[tuple[float, float]]:
        return None

    def is_suspended(self, code: str, context: dict) -> bool:
        return False

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
        return order_type in ("limit", "market", "stop")

    def get_price_tick(self) -> float:
        return 0.00000001
