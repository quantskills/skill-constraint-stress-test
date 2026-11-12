# 接口对外调用文档

## 概述

本 skill 是一个市场无关的 AI 交易员约束压力测试框架。本文档描述其对外暴露的调用接口。

## 主入口

### CLI

```bash
python scripts/run_eval.py [OPTIONS]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `--cases-dir` | str | 否 | `fixtures/` | fixture 目录 |
| `--config` | str | 否 | `config/eval_config.yaml` | 配置文件 |
| `--output-dir` | str | 否 | `output/` | 输出目录 |
| `--market-id` | str | 否 | 全部 | 限定市场 |
| `--injection-types` | list | 否 | 全部四类 | 限定故障类型 |
| `--self-check` | flag | 否 | — | 环境自检 |

### Python import

```python
from scripts.run_eval import run

report = run(
    cases_dir="fixtures/",
    config_path="config/eval_config.yaml",
    output_dir="output/",
    market_id="CN_A_STOCK",
    injection_types=["duplicate", "missing_quote"],
)
```

## 输入格式

### 持仓快照（7 字段格式）

```json
{
  "code": "600519.SH",
  "pnl_pct": 0.02,
  "sellable_qty": 1000,
  "locked_qty": 0,
  "price": 1800.0,
  "available_cash": 500000,
  "time": "10:30",
  "market_id": "CN_A_STOCK"
}
```

### 注入案例

由 `injector.py` 的生成器函数自动构造，每类 ≥30 条。

## 输出格式

### 聚合报告（report.json）

```json
{
  "run_id": "abc12345",
  "total_events": 120,
  "summary": {
    "violation_rate_with_checks": 0.03,
    "violation_rate_without_checks": 0.18,
    "duplicate_count_with_checks": 0,
    "duplicate_count_without_checks": 25,
    "refusal_count": 10
  },
  "effect_size": {
    "arr": 0.15,
    "rrr": 0.83,
    "nnt": 6.67
  }
}
```

### 事件日志（events_*.jsonl）

每行一条 JSON，字段见 `validate.py` 中的 `REQUIRED_EVENT_FIELDS`。

## 错误码

| 退出码 | error.code | 含义 | hint |
|---|---|---|---|
| 0 | — | 成功 | — |
| 1 | `runtime_error` | 运行时错误 | Check logs |
| 2 | `missing_argument` | 参数缺失 | Run with --help |
| 2 | `cases_dir_not_found` | fixture 目录不存在 | Create directory |
| 2 | `no_cases` | 无注入案例 | Check injection_types |
| 130 | `interrupted` | 用户中断 | — |

## 辅助工具

### 环境自检

```bash
python scripts/env_check.py --self-check --json
```

### 契约校验

```bash
python scripts/validate.py output/events_with_checks.jsonl --json
```

### 夹具生成

```bash
# 需要 pandadata 凭证
python scripts/fixture_generator.py --date 20250115

# 仅手工夹具（不需要凭证）
python scripts/fixture_generator.py --manual-only
```

## 数据源边界

- **夹具生成阶段**：调用 `panda_data==1.0.6` 获取真实停牌/ST/行情数据
- **评测执行阶段**：只读本地 JSON fixture，不发起任何网络请求
- **凭证要求**：夹具生成需要 `PANDA_DATA_USERNAME`、`PANDA_DATA_PASSWORD`、`PANDA_DATA_BASE_URL` 环境变量（仅从环境读取，不写入仓库）

## 被测系统接入

默认使用桩件 trader（返回 hold）。接入真实被测系统：

```python
from scripts.replay_engine import run_evaluation
from scripts.injector import generate_duplicate_cases

def my_trader(perturbed_input, config):
    # 调用被测系统（如日内仓位管理 skill）
    from some_module import manage_position
    return manage_position(...)

report = run_evaluation(
    cases=generate_duplicate_cases({}, 30),
    positions=my_positions,
    trader_fn=my_trader,
)
```
