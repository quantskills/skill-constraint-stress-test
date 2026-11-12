"""市场规则测试。"""

import pytest
from scripts.rules import get_rules, list_markets, register_rules
from scripts.rules.a_stock import AStockRules
from scripts.rules.crypto import CryptoRules
from scripts.rules.hk_stock import HKStockRules
from scripts.rules.index_future import IndexFutureRules
from scripts.market_rules import MarketRules, SettlementCycle


class TestRegistry:
    def test_list_markets(self):
        markets = list_markets()
        assert "CN_A_STOCK" in markets
        assert "HK" in markets
        assert "CRYPTO" in markets

    def test_get_known_market(self):
        rules = get_rules("CN_A_STOCK")
        assert isinstance(rules, AStockRules)

    def test_get_unknown_market_raises(self):
        with pytest.raises(ValueError, match="Unknown market"):
            get_rules("UNKNOWN_MARKET")

    def test_register_custom_market(self):
        class CustomRules:
            market_id = "CUSTOM"
            min_lot_size = 1
            settlement_cycle = SettlementCycle.T_PLUS_0
            force_close_time = "23:59"
            trading_hours = ("00:00", "23:59")
            def has_limit_up_down(self): return False
            def get_limit_prices(self, p): return None
            def is_suspended(self, c, ctx): return False
            def is_st_or_special(self, c): return False
            def requires_margin(self): return False
            def get_margin_ratio(self): return 0.0
            def get_contract_multiplier(self): return 1.0
            def can_sell_today(self, b): return True
            def supports_order_type(self, o): return True
            def get_price_tick(self): return 0.01

        register_rules("CUSTOM", CustomRules)
        rules = get_rules("CUSTOM")
        assert rules.market_id == "CUSTOM"


class TestAStockRules:
    def test_protocol_compliance(self):
        rules = AStockRules()
        assert isinstance(rules, MarketRules)

    def test_min_lot_size(self):
        assert AStockRules().min_lot_size == 100

    def test_t_plus_1(self):
        assert AStockRules().settlement_cycle == SettlementCycle.T_PLUS_1

    def test_cannot_sell_today(self):
        assert AStockRules().can_sell_today(True) is False

    def test_limit_prices(self):
        rules = AStockRules()
        up, down = rules.get_limit_prices(10.0)
        assert up == 11.0
        assert down == 9.0

    def test_force_close_time(self):
        assert AStockRules().force_close_time == "14:45"


class TestCryptoRules:
    def test_no_limit_up_down(self):
        assert CryptoRules().has_limit_up_down() is False

    def test_t_plus_0(self):
        assert CryptoRules().settlement_cycle == SettlementCycle.T_PLUS_0

    def test_can_sell_today(self):
        assert CryptoRules().can_sell_today(True) is True

    def test_min_lot_size_1(self):
        assert CryptoRules().min_lot_size == 1


class TestHKStockRules:
    def test_force_close_1545(self):
        assert HKStockRules().force_close_time == "15:45"

    def test_no_limit_up_down(self):
        assert HKStockRules().has_limit_up_down() is False


class TestIndexFutureRules:
    def test_requires_margin(self):
        assert IndexFutureRules().requires_margin() is True

    def test_contract_multiplier(self):
        assert IndexFutureRules().get_contract_multiplier() == 300.0

    def test_min_lot_size_1(self):
        assert IndexFutureRules().min_lot_size == 1
