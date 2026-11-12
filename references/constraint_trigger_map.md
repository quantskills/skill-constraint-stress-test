# 注入事件 → 约束触发映射表

本表定义了每类注入事件应触发哪些约束，是约束违反率分母的依据。

## A股（CN_A_STOCK）

| 注入事件 | 应触发的约束 | 备注 |
|---|---|---|
| qty 非 100 整数倍 | min_lot_size | — |
| 当日已买标的 + 卖出请求 | settlement_cycle | T+1 |
| 涨停价 + 买入请求 | limit_up_down | 卖出不触发 |
| 跌停价 + 卖出请求 | limit_up_down | 买入不触发 |
| 停牌标的 + 任意委托 | suspended_filter | — |
| ST 标的 + 任意委托 | st_filter | — |
| 14:46 + 非强平动作 | force_close_window | 强平时间 14:45 |
| 仓位超 max_position_pct | position_limit | — |
| available_cash 不足 | capital_check | — |
| 价格偏离 > threshold | price_deviation | 软约束 |
| 相同输入重复 | — | 由重复动作率独立统计 |

## 港股（HK）

| 注入事件 | 应触发的约束 | 备注 |
|---|---|---|
| qty 非 lot_size 整数倍 | min_lot_size | — |
| 15:46 + 非强平动作 | force_close_window | 强平时间 15:45 |
| 仓位超 max_position_pct | position_limit | — |
| available_cash 不足 | capital_check | — |
| 价格偏离 > threshold | price_deviation | 软约束 |
| — | settlement_cycle | T+0，不触发 |
| — | limit_up_down | 无涨跌停，不触发 |

## 股指期货（CN_INDEX_FUTURE）

| 注入事件 | 应触发的约束 | 备注 |
|---|---|---|
| qty < 1 | min_lot_size | 最小 1 手 |
| 涨停价 + 买入请求 | limit_up_down | ±10% |
| 14:46 + 非强平动作 | force_close_window | — |
| 保证金不足 | capital_check | 按 margin_ratio 计算 |

## 加密货币（CRYPTO）

| 注入事件 | 应触发的约束 | 备注 |
|---|---|---|
| 仓位超 max_position_pct | position_limit | — |
| available_cash 不足 | capital_check | — |
| — | settlement_cycle | T+0，不触发 |
| — | limit_up_down | 无涨跌停，不触发 |
| — | force_close_window | 23:59，几乎不触发 |

## 缺失行情子类型

| 子类型 | 期望行为 | 触发的约束 |
|---|---|---|
| 停牌缺失（price=null） | 拒单 + 标记停牌 | suspended_filter |
| 涨跌停封板缺失（昨收冒充现价） | 方向感知判断 | limit_up_down（需方向感知） |
| 行情源断流（price=0） | 拒单 + 告警 | 应触发 is_refusal=true |
