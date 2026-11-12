# 实测验证记录

本文件记录在真实环境中跑出的结果，供审核方核对。**所有数字均可通过下列命令复现**，除 LLM 部分需要端点凭证。

---

## 1. 单元测试

```bash
python3 -m pytest tests/ -q
```

| 项 | 结果 |
|---|---|
| 用例总数 | 152 |
| 通过 | 152 |
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

```bash
export CONSTRAINT_LLM_BASE_URL=http://127.0.0.1:<port>
export CONSTRAINT_LLM_MODEL="$ANTHROPIC_MODEL"
export CONSTRAINT_LLM_PROTOCOL=anthropic
CONSTRAINT_TRADER=llm CONSTRAINT_CASES_PER_TYPE=15 python3 scripts/build.py
```

被测模型：一台通过 Anthropic 协议暴露的本地 LLM 代理（温度 0）

#### rules_in_prompt=true（模型已知规则）

| 指标 | 值 |
|---|---|
| 事件数 | 90 |
| 基线违反率 → 校验后 | **8.89% → 0%** |
| verdict | `positive`（p = 0.0067） |
| McNemar | only_without 8 / only_with 0 / neither 82 |
| ARR / RRR / NNT | 0.0889 / 1.0 / 11.25 |
| 95% CI | [0.0457, 0.1657] |
| 基线违反明细 | `duplicate_order` 8、`position_limit` 4、`quote_required` 1 |
| 决策改变 | 8 笔，全部 `buy→hold` |
| 按故障类型 | duplicate 5、prompt_perturb 2、missing_quote 1 |
| 解析错误率 | 0.0 |
| 调用 / 缓存命中 | 56 / 47 |
| token | 10960 输入 + 21008 输出 |

#### rules_in_prompt=false（模型自行推断规则）

| 指标 | 值 |
|---|---|
| 事件数 | 90 |
| 基线违反率 | 3.33% |
| verdict | `inconclusive`（p = 0.124） |
| 基线违反明细 | `st_filter` 1、`quote_required` 2、`duplicate_order` 1 |
| 解析错误率 | 0.0 |

**观察**：不提供规则时违反率反而更低（3.33% vs 8.89%），但样本不足以判定显著。一种解释是：有规则提示时模型更倾向主动下单，因而更容易撞上重复委托与仓位限制。这是一次观测，不构成结论。

---

## 3. 冻结基线可比性

两轮仅 `rules_in_prompt` 不同：

```bash
CONSTRAINT_LLM_RULES_IN_PROMPT=false python3 scripts/build.py
python3 scripts/compare_baseline.py freeze output/report.json --name off
CONSTRAINT_LLM_RULES_IN_PROMPT=true  python3 scripts/build.py
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
- 第 2.3 节需要可用的 LLM 端点；替换 `CONSTRAINT_LLM_BASE_URL` / `MODEL` 即可换成其他模型
- `fixtures/generated/` 随仓库发布（确定性、无凭证），保证开箱可跑
