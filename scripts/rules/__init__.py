"""市场规则注册表 — 按 market_id 获取规则实例。"""

from __future__ import annotations

from typing import Type

from scripts.market_rules import MarketRules
from scripts.rules.a_stock import AStockRules
from scripts.rules.crypto import CryptoRules
from scripts.rules.hk_stock import HKStockRules
from scripts.rules.index_future import IndexFutureRules
from scripts.rules.commodity_future import CommodityFutureRules

_REGISTRY: dict[str, Type] = {
    "CN_A_STOCK": AStockRules,
    "HK": HKStockRules,
    "CN_INDEX_FUTURE": IndexFutureRules,
    "CN_COMMODITY_FUTURE": CommodityFutureRules,
    "CRYPTO": CryptoRules,
}


def get_rules(market_id: str) -> MarketRules:
    """按市场标识获取规则实例。"""
    cls = _REGISTRY.get(market_id)
    if cls is None:
        raise ValueError(
            f"Unknown market: {market_id}. Available: {list(_REGISTRY.keys())}"
        )
    return cls()


def register_rules(market_id: str, rules_cls: Type) -> None:
    """运行时注册新市场规则。"""
    _REGISTRY[market_id] = rules_cls


def list_markets() -> list[str]:
    return list(_REGISTRY.keys())
