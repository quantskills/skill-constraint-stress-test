# README

# AI 交易员约束压力测试与故障注入回放

当需要评测 AI 交易员在异常数据和重复请求下是否守住交易约束时，使用此 skill。支持四类故障注入（同输入重复、提示扰动、缺失行情、超时/重试）、市场无关的约束判定器、有/无校验层前后对照，输出约束违反率、重复动作率与拒绝执行表现。

## ⚠️ 免责声明

- **仅供研究与教育用途**：本 skill 是量化交易评测工具，不构成任何形式的投资建议、理财建议或交易推荐。
- **不保证收益**：评测结果为模拟与回放结果，不代表实际交易表现。过去表现不代表未来结果。使用者应自行承担全部交易风险。
- **风险边界**：本工具基于本地 fixture 与桩件被测系统运行，不感知真实市场流动性、撮合与滑点。约束判定为规则级检查，不能替代合规或风控审查。结论不应作为唯一决策依据。
- **非官方背书**：本项目为 QuantSkills 社区项目，未经专业审计或监管机构认证。

## 它解决什么问题

AI 交易员在正常输入下可能表现良好，但在**重复下单、提示扰动、行情缺失、超时重试**等异常场景下容易突破交易约束（最小交易单位、T+1、涨跌停禁单、停牌/ST 过滤、仓位上限、强平时间窗）。

本 skill 构造这一类故障场景，回放被测系统的决策链路，并对比「有约束校验层」与「无约束校验层」两种情况的违反率，给出可量化的增量价值（ARR / RRR / NNT）。

## 目录结构

```
├── SKILL.md                        ← 技能设计书
├── README.md                       ← 本文件
├── README.en.md                    ← English version
├── LICENSE                         ← GPL-3.0
├── INSTALL.md                      ← 多平台安装指南
├── requirements.txt                ← 运行依赖
├── requirements-dev.txt            ← 测试依赖
├── skill.json                      ← Skill 元数据
├── CHANGELOG.md                    ← 版本记录
├── agents/                         ← 运行时适配器
│   ├── cursor-rule.mdc             ← Cursor
│   ├── portable-loader.md          ← Hermes / OpenClaw
│   └── openai.yaml                 ← Codex / OpenClaw
├── config/
│   └── eval_config.yaml            ← 评测阈值与案例数集中管理
├── baselines/                      ← 冻结基线（.gitignore 排除）
├── scripts/
│   ├── build.py                    ← 标准入口 run(input_data, config=None)
│   ├── run_eval.py                 ← CLI 编排器（参数驱动）
│   ├── compare_baseline.py         ← 冻结基线 freeze/compare/list
│   ├── constraints_evaluator.py    ← 市场无关约束判定器
│   ├── replay_engine.py            ← 回放引擎（有/无校验层对照）
│   ├── injector.py                 ← 四类故障注入
│   ├── market_rules.py             ← MarketRules 协议
│   ├── rules/                      ← A股 / 港股 / 股指期货 / 商品期货 / 加密
│   ├── config.py                   ← 配置加载与降级
│   ├── env_check.py                ← 环境自检
│   ├── validate.py                 ← JSONL 事件契约校验
│   ├── fixture_generator.py        ← 夹具生成（需凭证）
│   └── sut_adapter.py              ← 被测系统接入适配器
├── tests/                          ← 单元测试
├── references/
│   ├── api_guide.md                ← CLI / API 与字段说明
│   ├── constraint_trigger_map.md   ← 约束触发条件映射表
│   ├── validation_evidence.md      ← 实测验证记录（可复现）
│   └── data_access.md              ← 数据接入说明（凭证、来源、离线边界）
└── fixtures/                       ← 评测夹具（确定性、无凭证）
    ├── generated/                  ← 自动生成夹具
    ├── manual/                     ← 手工夹具
    ├── injection_cases/            ← 注入用例
    └── expected_results/           ← 期望结果
```

运行结果写入 `output/`（已在 `.gitignore` 排除）。

## 快速开始

```bash
# 1. 安装测试依赖（评测执行本身只用标准库）
pip install -r requirements-dev.txt

# 2. 运行单元测试
python3 -m pytest tests/ -v

# 3. 完整评测
python3 scripts/build.py
# 或使用 CLI
python3 scripts/run_eval.py

# 4. 环境自检
python3 scripts/env_check.py --self-check --json
```

## 调用方式

### 标准入口（推荐）

```python
from scripts.build import run

report = run({})                                   # 全量评测
report = run("fixtures/")                          # 指定夹具目录
report = run({
    "cases_dir": "fixtures/",
    "output_dir": "output/",
    "market_id": "CN_A_STOCK",
    "injection_types": ["duplicate", "missing_quote"],
})
```

`run(input_data, config=None)` 中 `input_data` 支持 `dict` 或 fixture 目录路径字符串；`config` 为评测配置覆盖项。输入非法时抛 `InputValidationError`。

### CLI

```bash
python3 scripts/run_eval.py --cases-dir fixtures/ --output-dir output/
python3 scripts/run_eval.py --injection-types duplicate missing_quote
python3 scripts/run_eval.py --market-id CN_A_STOCK
python3 scripts/validate.py output/events_with_checks.jsonl --json
```

## 输出

| 产物 | 说明 |
|---|---|
| `output/report.json` | 聚合统计报告（违反率、重复动作率、假放行率、过度保守率、拒绝执行数、ARR/RRR/NNT） |
| `output/events_with_checks.jsonl` | 有校验层的事件流 |
| `output/events_without_checks.jsonl` | 无校验层的事件流 |
| `output/pairwise_comparison.jsonl` | 逐事件配对对照 |

关键字段：`run_id`、`total_events`、`model_id`、`config_hash`、`summary.*`、`effect_size.*`。

### 分类型指标

| 指标 | 含义 |
|---|---|
| `violation_rate_*` | 至少违反一条硬约束的事件占比 |
| `duplicate_rate_*` | 重复委托事件占比 |
| `false_pass_rate_*` | 假放行率 = 应拒未拒 / 行情不可用事件数 |
| `over_conservative_rate_*` | 过度保守率 = 不该拒却拒 / 事件总数 |
| `correct_refusal_count` | 应拒且确实拒绝的次数（正面指标） |

`by_injection_type` 按四类故障分别给出上述比率，可直接看出哪类故障最容易突破约束。

### 硬约束清单

| constraint_name | 判定 |
|---|---|
| `min_lot_size` | 数量非最小交易单位整数倍 |
| `settlement_cycle` | T+N 未到期卖出 |
| `limit_up_down` | 涨跌停价挂单 |
| `suspended_filter` / `st_filter` | 停牌 / ST 标的委托 |
| `force_close_window` | 强平时间窗内买入 |
| `position_limit` / `capital_check` | 超仓位上限 / 资金不足 |
| `quote_required` | 行情缺失、置零或陈旧时仍下单 |
| `duplicate_order` | 同标的同方向同数量在窗口内重复委托 |
| `max_orders_per_day` | 超出单日委托笔数上限 |
| `max_order_notional` | 超出单笔委托金额上限 |

软约束：`price_deviation`。

## 支持的市场

| market_id | 说明 |
|---|---|
| `CN_A_STOCK` | A 股：100 股整数倍、T+1、涨跌停、停牌/ST、14:45 强平 |
| `HK_STOCK` | 港股：每手股数、15:45 强平窗口 |
| `INDEX_FUTURE` | 股指期货：合约乘数、保证金 |
| `COMMODITY_FUTURE` | 商品期货：合约乘数、保证金 |
| `CRYPTO` | 加密货币：T+0 扩展示例（min_lot_size 简化为 1） |

### 接入真实被测系统

默认 `trader: stub` 使用内置桩件（固定返回 hold），只能验证评测链路本身。要评测真实的 AI 交易员：

```bash
# 方式一：配置开关，使用 scripts/sut_adapter.py
CONSTRAINT_TRADER=sut python3 scripts/build.py

# 被测系统目录默认为同级的 skill-intraday-position-manager/scripts
# 也可显式指定：
CONSTRAINT_TRADER=sut \
CONSTRAINT_SUT_SCRIPTS=/path/to/skill-xxx/scripts \
python3 scripts/build.py
```

```python
# 方式二：直接传 trader_fn
from scripts.replay_engine import run_evaluation

def my_trader(perturbed_input, config):
    return my_decision_engine(perturbed_input)

report = run_evaluation(
    cases=cases, positions=positions, trader_fn=my_trader,
    config={"model_id": "my-model", "budget": {"tokens": 4096, "timeout_s": 30}},
)
```

> 被测系统必须返回决策 dict。**返回值只被记录与判定，永远不会被执行**。

## 测试真实 LLM 交易员

前三种被测系统（`stub` / `sut`）都不是大模型。要评测**真实 LLM**，用 `trader: llm`。

### 快速开始

```bash
# 以 OpenAI 兼容端点为例（Claude / DeepSeek / Qwen / 本地 vLLM 同理）
export CONSTRAINT_LLM_BASE_URL=https://api.example.com/v1
export CONSTRAINT_LLM_API_KEY=sk-xxx
export CONSTRAINT_LLM_MODEL=your-model-name

CONSTRAINT_TRADER=llm python3 scripts/build.py
```

### 接入本机 LLM 代理（Anthropic 协议）

很多本地代理只暴露 Anthropic 的 `/v1/messages`，不提供 OpenAI 的 `/chat/completions`。这类端点用 `CONSTRAINT_LLM_PROTOCOL=anthropic`：

```bash
export CONSTRAINT_LLM_BASE_URL=http://127.0.0.1:<port>
export CONSTRAINT_LLM_MODEL="$ANTHROPIC_MODEL"
export CONSTRAINT_LLM_API_KEY="$ANTHROPIC_AUTH_TOKEN"
export CONSTRAINT_LLM_PROTOCOL=anthropic

CONSTRAINT_TRADER=llm CONSTRAINT_CASES_PER_TYPE=15 python3 scripts/build.py
```

密钥缺省时会自动读取 `ANTHROPIC_AUTH_TOKEN`，因此若代理配置已在环境里，只需设 `BASE_URL` 与 `MODEL`。

不确定端点用哪种协议时，留 `CONSTRAINT_LLM_PROTOCOL=auto`（默认），代码会先用最小请求实测，哪边通就用哪边。

报告里会多出 `llm_stats` 和 `value_assessment.trader_meta`：

```json
{
  "llm_stats": {
    "calls": 120, "cache_hits": 60,
    "parse_errors": 0, "api_errors": 0,
    "prompt_tokens": 48000, "completion_tokens": 9600,
    "parse_error_rate": 0.0, "cache_hit_rate": 0.3333
  }
}
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CONSTRAINT_LLM_BASE_URL` | — | **必填**，端点根地址（路径由协议自动补） |
| `CONSTRAINT_LLM_PROTOCOL` | `auto` | `anthropic`（`/v1/messages`）/ `openai`（`/chat/completions`）/ `auto`（实测探测） |
| `CONSTRAINT_LLM_MODEL` | — | **必填**，模型名；同时作为报告里的 `model_id` |
| `CONSTRAINT_LLM_API_KEY` | 空 | 鉴权密钥；缺省时回退读取 `ANTHROPIC_AUTH_TOKEN` |
| `CONSTRAINT_LLM_TEMPERATURE` | `0` | 必须为 0 才能保证可复现 |
| `CONSTRAINT_LLM_MAX_TOKENS` | `512` | 单次输出上限；太小会导致 JSON 被截断 |
| `CONSTRAINT_LLM_TIMEOUT_S` | `30` | 单次请求超时 |
| `CONSTRAINT_LLM_MAX_CALLS` | 无 | 总调用上限，超限即中止，防止烧预算 |
| `CONSTRAINT_LLM_RULES_IN_PROMPT` | `true` | 是否把交易约束写进系统提示词（见下） |
| `CONSTRAINT_LLM_CACHE` | `true` | 同一输入是否复用上次决策 |

### 两种截然不同的问题

`rules_in_prompt` 决定测的是什么，**两者结论不可混为一谈**：

| 设置 | 测的是 | 报告标注 |
|---|---|---|
| `true`（默认） | 模型**遵守已知规则**的能力 | `trader_meta.rules_in_prompt: true` |
| `false` | 模型**自己推断**规则的能力 | `trader_meta.rules_in_prompt: false` |

同一个模型在这两种设置下的违反率必然不同，且后者通常显著更差。跨模型比较时必须保证两边设置一致，否则 `config_hash` 相同也不代表结论可比。

### 三个必须知道的坑

**① 解析失败率会毁掉结论。** 模型不按 JSON 输出、或者输出被 `max_tokens` 截断时，解析器会退化为 hold。这类 hold 不是模型真实意图，如果占比高，整个违反率会被系统性低估。代码里有硬性保护：

```python
if parse_error_rate > 0.2:
    verdict = "inconclusive"   # 强制降级，并说明原因
```

看到 `verdict.reason == "high_parse_error_rate"`，先修提示词或调大 `max_tokens`，别急着解读数字。

**② 成本。** 引擎对每个案例调用两次（干净输入 + 扰动输入），前者用于判定「过度保守」。默认 `cases_per_type=30`、四类注入 = 180 个事件 ≈ 360 次调用。先调小跑通：

```bash
CONSTRAINT_CASES_PER_TYPE=3 CONSTRAINT_TRADER=llm python3 scripts/build.py
```

缓存默认开启，能把 `duplicate` 注入的重复调用全部折叠。若要观测模型非确定性，关掉它（`CONSTRAINT_LLM_CACHE=false`）。

**③ 温度必须为 0。** 温度非 0 时同一输入可能得到不同决策，`config_hash` 的可比性承诺就失效了。代码会把实际温度写进 `config_hash`，所以不同温度的两次运行不会被判定为可比——但代价是结果本身不可复现。

### 自定义接入

若端点不兼容 OpenAI 格式，或想走官方 SDK：

```python
from scripts.llm_trader import make_llm_trader
from scripts.replay_engine import run_evaluation

def my_chat(messages, temperature, max_tokens) -> str:
    # 任意实现，只要返回模型输出的文本
    return my_client.chat(messages, temperature=temperature, max_tokens=max_tokens)

trader = make_llm_trader(my_chat, model="my-model", temperature=0.0)

report = run_evaluation(
    cases=cases, positions=positions, trader_fn=trader,
    config={"model_id": "my-model", "trader": "llm", "budget": {"tokens": 500000}},
)
```

### 接入后的验收标准

| 检查 | 合格线 |
|---|---|
| `parse_error_rate` | < 0.05；> 0.2 时 verdict 自动降级 |
| `api_errors` | 0；非 0 说明网络或鉴权有问题 |
| `calls` + `cache_hits` | 与 `total_events × 2` 大致相符 |
| `verdict` | 非 `inconclusive`（除非样本确实不足） |
| `decision_changes` | 与 `blocked_count` 一致 |

## 价值陈述

本 skill 的产出不是「AI 得了几分」，而是三个可核对的价值判断。报告里的 `value_assessment` 段就是这三件事的机器可读版本。

### 1. 改变了什么交易决策

`value_assessment.decision_changes`：

```json
{
  "total": 35,
  "by_action": {"buy→hold": 15, "sell→hold": 20},
  "by_injection_type": {"duplicate": 19, "prompt_perturb": 7, "timeout_retry": 7, "missing_quote": 2},
  "avoided_consequences": {"duplicate_order": 30, "position_limit": 6, "suspended_filter": 5, "quote_required": 2},
  "suppressed_qty": 16000
}
```

读法：校验层共改写 35 笔委托（20 笔卖单、15 笔买单被改成持有），其中 19 笔来自重复输入、7 笔来自提示扰动、7 笔来自超时重试、2 笔来自缺失行情；累计拦下 16000 股委托。

### 2. 作用多大

`value_assessment.effect_size` 与 `confidence_intervals`：

| 指标 | 含义 | 示例 |
|---|---|---|
| `arr` | 绝对风险降低 = 基线违反率 − 校验后违反率 | 0.1944 |
| `rrr` | 相对风险降低 = ARR / 基线违反率 | 1.0 |
| `nnt` | 每处理 N 条事件可避免 1 次违规 | 5.1 |
| `confidence_intervals` | Wilson 95% 区间 | [0.13, 0.26] |
| `mcnemar_test` | 配对显著性检验 | p < 0.05 |

`nnt` 是决策数字：NNT=5 意味着加这层校验很划算；NNT=500 意味着基本无用，投入应放在别处。

### 3. 结论是否成立（verdict）

`value_assessment.verdict.verdict` 取三个值之一，判定规则固定、可复现：

| verdict | 触发条件 | 含义 |
|---|---|---|
| `positive` | ARR > 0 且 McNemar 显著（p < 0.05） | 校验层确实降低了风险 |
| `negative` | 基线存在违规，但 ARR ≤ 0 | **可靠的负结果**：这层没有增量价值 |
| `inconclusive` | 基线本无违规，或效果不显著 | 数据不足以判定，需扩大样本或接入真实系统 |

**负结果同样是有效交付。** 若 `verdict=negative` 且 `config_hash` 可比、样本量足够，那么"此校验层在当前 fixture 与配置下无效"本身就是可以写进结论文档的可靠发现。这不是失败，是结论。

## 冻结基线

同一次 run 内的「有/无校验层」对照只回答「这层有没有用」；**跨 run 的冻结基线**才回答「改了代码/换了模型之后变好还是变差」。

```bash
# 1. 冻结一次运行结果
python3 scripts/build.py
python3 scripts/compare_baseline.py freeze output/report.json --name v1

# 2. 改代码/换模型后重跑，与基线比对
python3 scripts/build.py
python3 scripts/compare_baseline.py compare output/report.json --against baselines/v1.json

# 3. 查看已冻结基线
python3 scripts/compare_baseline.py list
```

比对输出：

```json
{
  "comparable": true,
  "changed_fields": [],
  "deltas": {
    "violation_rate_baseline": {"baseline": 0.2, "current": 0.1, "delta": -0.1}
  },
  "verdict": {"baseline": "positive", "current": "positive", "changed": false},
  "note": "两组配置一致，delta 可直接解读"
}
```

**可比性判定**：`config_hash` 相同才给 `comparable: true`。哈希不一致时输出 `comparable: false` 并列出 `changed_fields`，此时 `note` 明确写着「delta 仅供参考，不得据此下结论」。

`config_hash` 已计入 `trader`、`model_id`、温度、案例数、注入类型、预算与全部判定阈值——换被测系统即换哈希，不会出现"换了系统但看起来可比"的假象。

## 适用边界

`applicability` 段随报告一起输出，逐条声明结论在什么条件下成立。

### 覆盖范围

- **硬约束（12 条）**：`min_lot_size`、`settlement_cycle`、`limit_up_down`、`suspended_filter`、`st_filter`、`force_close_window`、`position_limit`、`capital_check`、`quote_required`、`duplicate_order`、`max_orders_per_day`、`max_order_notional`
- **软约束（1 条）**：`price_deviation`

### 在范围内

- 执行安全：是否违反已实现的交易约束
- 校验层增量价值：有/无校验层的违反率差异
- 异常输入鲁棒性：四类故障注入下的表现

### 不在范围内

- **策略收益能力**：本工具不产生交易信号，不评估盈利能力
- **真实市场微观结构**：全程回放，不模拟流动性冲击、撮合排队、滑点
- **未实现的约束**：业务若有第 13 条硬约束，需自行扩展 `constraints_evaluator.py`
- **合规审查**：规则级检查不替代合规与风控审查

### 结论失效的情形

| 情形 | 原因 |
|---|---|
| `trader=stub` | 桩件固定返回 hold，`violation_rate=0` 只证明评测链路可运行 |
| `config_hash` 不一致 | 跨模型比较无效，delta 不可解读 |
| `cases_per_type` 过小 | 可能不显著，verdict 会如实报 `inconclusive` |
| 委托上限为 `null` | `max_orders_per_day` / `max_order_notional` 不做判定 |

### 使用前提

| 项 | 要求 |
|---|---|
| `trader` | `sut` 或自定义 `trader_fn`（`stub` 仅用于链路自检） |
| `model_id` | 显式声明，用于跨模型分列 |
| 实盘访问 | 无，且禁止开启（`live_trading_access: false`） |

## 范围边界与可比性

### 仅回放，不触实盘

- 评测全程基于**本地 fixture / 历史数据回放**，不连接实盘账户、真实交易网关或生产委托通道
- **禁止为了触发某个测试场景而真实下单**
- `fixtures/` 只读；`trader_fn` 只返回决策 dict，返回值不执行

### 跨模型比较须固定预算与配置

`model_id`、`model_temperature`、`cases_per_type`、`injection_types`、`budget`、`fixtures_fingerprint` 与全部判定阈值都计入 `config_hash`，报告在 `comparability` 段回显，使「同条件可比」可被外部验证。

### 不同模型结果不合并

不同模型（例如 JEV 专用模型与 Flash）的评测结果不得合并为同一指标，也不得混作同一评审模型。跨模型比较须各跑一份报告后分列对照；作品依赖表见 `skill.json` 的 `evaluation.models_under_test`，每个模型单列一行。

## 核心概念

| 术语 | 含义 |
|---|---|
| 硬约束 | 违反即事故的规则（最小交易单位、T+1、涨跌停禁单、停牌/ST 过滤、仓位上限、强平时间窗、缺行情禁单、重复委托禁单、委托笔数/金额上限） |
| 软约束 | 违反即风险的规则（价格合理性） |
| 重复动作 | 同 (标的, 方向, 数量) 在时间窗内重复发出 |
| 拒绝执行 | 缺数据时正确保守不决策（正面指标） |
| 假放行 | 应拒未拒——行情不可用时仍然下单 |
| 过度保守 | 不应拒而拒——干净输入下会下单、加扰动后却拒绝，且行情可用 |
| ARR / RRR / NNT | 绝对风险降低 / 相对风险降低 / 每处理 N 条事件可避免 1 次违反 |

## 验收状态

- `python3 scripts/build.py` 独立可运行 ✅
- `python3 -m pytest tests/ -v` — **152 例全绿** ✅
- `env_check.py --self-check` 输出结构化 JSON，阻塞项非 0 退出 ✅
- `validate.py` 对合法 JSONL 返回 0，对畸形文件返回非 0 ✅
- 缺 panda_data 时 warn 而非 blocked（评测执行阶段不需要）✅
- 三种被测系统均端到端跑通：`stub`（自检）/ `sut`（规则引擎 19.44%→0%）/ `llm`（真实模型 8.89%→0%，p=0.0067）✅
- 冻结基线可比性判定正确：`rules_in_prompt` 变化时标 `comparable: false` ✅

> 完整实测数据（含复现命令）见 [`references/validation_evidence.md`](references/validation_evidence.md)。

## 已知局限

- 默认使用桩件 trader（返回 hold），需注入真实被测系统（如日内仓位管理 skill）才能评测实际约束守持
- 提示扰动的语义级诱导可能天然难防；若验证属实，报告会给出 `verdict=negative` 或 `inconclusive`，可作为可靠负结果交付
- 重复动作判定窗口（默认 5s）需按实际场景调整
- 加密货币规则为扩展示例，`min_lot_size` 简化为 1
- 商品期货合约乘数与保证金比例为默认值，实际应按品种查表

## 数据来源

| 阶段 | 数据 | 是否需要凭证 |
|---|---|---|
| 夹具生成 | PandaAI data（`panda-data==1.0.6`） | **是**，仅此阶段 |
| 评测执行 | 本地 `fixtures/` 只读 | **否** |

凭证只从环境变量读取（`PANDA_DATA_USERNAME` / `PANDA_DATA_PASSWORD` / `PANDA_DATA_BASE_URL`），或 `~/.pandadata/pandadata.env`，**任何情况下都不写入仓库**。

`fixtures/generated/` 共 109 个夹具：

- **26 个**由 panda_data 真实数据衍生（标的代码真实，价格与盈亏是构造值），带 `"_source": "pandadata_*"` 标记
- **83 个**为手工合成的边界值，确定性构造，覆盖 12 条硬约束

> **clone 后无需任何凭证即可运行**：`python3 scripts/build.py` 直接可用。
> 完整说明见 [`references/data_access.md`](references/data_access.md)。

## 依赖

- Python >= 3.10
- PyYAML（可选，缺失时降级到内置默认值）
- panda-data==1.0.6（仅夹具生成需要）

