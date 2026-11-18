# 实测验证记录

本文件记录在真实环境中跑出的结果，供审核方核对。**所有数字均可通过下列命令复现**，除 LLM 部分需要端点凭证。

---

## 1. 单元测试

```bash
python3 -m pytest tests/ -q
```

| 项 | 结果 |
|---|---|
| 用例总数 | 219 |
| 通过 | 219 |
| 失败 | 0 |
| 环境 | Python 3.11.16 |

测试分布：

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `test_constraints_evaluator.py` | 27 | 12 条硬约束 + 1 条软约束的判定 |
| `test_metrics.py` | 37 | 分类型指标、价值验收、verdict 分支、配置哈希 |
| `test_llm_trader.py` | 24 | LLM 解析、缓存、预算、Anthropic 协议 |
| `test_market_rules.py` | 19 | 五个市场的规则实现 |
| `test_injector.py` | 13 | 四类故障注入 |
| `test_compare_baseline.py` | 11 | 冻结基线 freeze/compare/list |
| `test_replay_engine.py` | 11 | 回放与聚合 |
| `test_validate.py` | 10 | 事件契约校验 |
| `test_mcp_source.py` | 48 | MCP 信封两种形态、截断拒绝、缺列、ok=false、导出加载（全程离线） |
| `test_agent_mode.py` | 14 | agent 模式的参数暴露、导出契约与运行时对齐 |
| `test_preflight.py` | 5 | **前置检查小节存在且覆盖宿主前提**、**排在所有可执行流程之前**、**取新数据与离线评测两条线路的阻塞语义分明**、**桩件结论不得写成交易员表现**、**文档不再描述取数组件与凭证通道** |

---

## 2. 三种被测系统（trader）

### 2.1 stub —— 链路自检

```bash
python3 scripts/build.py
```

| 指标 | 值 |
|---|---|
| 事件数 | 180 |
| 基线违反率 | 0.0 |
| verdict | `inconclusive`（`baseline_has_no_violations`） |

**结论**：桩件永远返回 hold，零违规只证明评测链路可运行。这是设计上的预期，不是缺陷。

### 2.2 sut —— 真实规则引擎

```bash
CONSTRAINT_TRADER=sut python3 scripts/build.py
```

被测系统：`skill-intraday-position-manager`

| 指标 | 值 |
|---|---|
| model_id | `sut-trader` |
| 事件数 | 180 |
| 基线违反率 → 校验后 | **19.44% → 0%** |
| blocked_count | 35 |
| verdict | `positive` |
| ARR / RRR / NNT | 0.1944 / 1.0 / 5.14 |
| 基线违反明细 | `duplicate_order` 34、`position_limit` 6、`suspended_filter` 5、`quote_required` 2 |
| 决策改变 | 35 笔（`sell→hold` 20、`buy→hold` 15） |

### 2.3 llm —— 真实大模型

> **已移除（v0.2.1）**：本节原先记录一次由 skill 内部直连本地 LLM 代理（Anthropic 协议）的端到端运行。
> 该通道已删除——真实模型评测改走 `agent` 模式：`scripts/agent_mode.py export` 出题、
> 由阅读本 skill 的 agent 作答、再 `replay --decisions <file>` 回填重放，skill 自身不再发起任何模型调用。
> 因此那次运行的数字（含 8.89%→0% 与其 p 值）一并作废，不再作为交付证据引用。
> 顺带记录一处口径修正：原先报告的 p 值来自 `erfc(sqrt(chi2/2)) / 2`，把 df=1 卡方的生存函数
> 又除了一次 2，**系统性低估一半**（`b=5, c=0` 会把 0.0736 报成 0.0368 而误判显著）。修正后
> 渐进值即生存函数本身，且不一致对子 ≤100 时以精确二项检验为准；原先判「不显著」的结论不会因
> 此翻转（p 只会变大），可能翻转的只有原先判「显著」的那些。

---

## 3. 冻结基线可比性

两轮仅 `rules_in_prompt` 不同：

```bash
CONSTRAINT_RULES_IN_PROMPT=false python3 scripts/build.py
python3 scripts/compare_baseline.py freeze output/report.json --name off
CONSTRAINT_RULES_IN_PROMPT=true  python3 scripts/build.py
python3 scripts/compare_baseline.py compare output/report.json --against baselines/off.json
```

| 项 | 结果 |
|---|---|
| OFF config_hash | `88eb958cd7339087` |
| ON config_hash | `9021d6eb7ccc408d` |
| comparable | `false` |
| changed_fields | `["rules_in_prompt"]` |

**结论**：两个不同的问题（遵守已知规则 / 自行推断规则）不会被误判为可比。

---

## 4. 错误路径

| 场景 | 结果 |
|---|---|
| 缺 LLM 配置 | `{"ok": false, "error": {"code": "LLMError", ...}}`，退出码 1 |
| 未知 trader 值 | `{"ok": false, "error": {"code": "unknown_trader", ...}}` |
| cases 目录不存在 | `InputValidationError: cases_dir_not_found` |
| 畸形 JSONL | `validate.py` 退出码 1 |
| stub 判 positive | 不可能，强制 `inconclusive` |

---

## 5. 复现说明

- 第 1、2.1、2.2、3、4 节**无需任何凭证**，clone 后可直接复现
- 真实模型评测改走 agent 模式（export → 宿主 agent 作答 → replay），不再需要任何端点或密钥
- `fixtures/generated/` 随仓库发布（确定性、无凭证），保证开箱可跑
