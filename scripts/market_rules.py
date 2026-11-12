"""市场规则抽象接口 — 约束判定器只依赖此模块，不依赖具体市场实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class SettlementCycle(Enum):
    T_PLUS_0 = 0
    T_PLUS_1 = 1
    T_PLUS_2 = 2


@dataclass(frozen=True)
class ConstraintResult:
    """单条约束判定结果。"""

    constraint_name: str
    passed: bool
    detail: str = ""
    severity: str = "hard"  # "hard" | "soft"


@runtime_checkable
class MarketRules(Protocol):
    """市场规则抽象接口。

    约束判定器通过此接口查询所有市场属性，
    新增市场只需实现此协议并注册到 rules/__init__.py。
    """

    @property
    def market_id(self) -> str: ...

    @property
    def min_lot_size(self) -> int: ...

    @property
    def settlement_cycle(self) -> SettlementCycle: ...

    @property
    def force_close_time(self) -> str: ...

    @property
    def trading_hours(self) -> tuple[str, str]: ...

    def has_limit_up_down(self) -> bool: ...

    def get_limit_prices(
        self, prev_close: float
    ) -> Optional[tuple[float, float]]: ...

    def is_suspended(self, code: str, context: dict) -> bool: ...

    def is_st_or_special(self, code: str) -> bool: ...

    def requires_margin(self) -> bool: ...

    def get_margin_ratio(self) -> float: ...

    def get_contract_multiplier(self) -> float: ...

    def can_sell_today(self, bought_today: bool) -> bool: ...

    def supports_order_type(self, order_type: str) -> bool: ...

    def get_price_tick(self) -> float: ...
