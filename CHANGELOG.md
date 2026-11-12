# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `references/validation_evidence.md`：实测验证记录（152 例测试分布、三种被测系统的真实结果、冻结基线可比性、错误路径），全部附复现命令
- **Anthropic 协议支持**：`CONSTRAINT_LLM_PROTOCOL=anthropic` 走 `/v1/messages`，接入本机 LLM 代理；`auto` 自动实测两种协议
- `CONSTRAINT_LLM_API_KEY` 缺省时回退读取 `ANTHROPIC_AUTH_TOKEN`
- 真实 LLM 端到端实测：一个 Anthropic 协议的本地模型代理在 `rules_in_prompt=true` 下
  基线违反率 8.89% → 有校验层 0%，`verdict=positive`（p=0.0067，NNT=11.25）
- **真实 LLM 接入** `scripts/llm_trader.py`：`trader: llm` 走 OpenAI 兼容端点，零第三方依赖（标准库 urllib）
  - `rules_in_prompt` 区分「遵守已知规则」与「自行推断规则」两种评测问题
  - `llm_stats` 输出 calls / cache_hits / parse_errors / api_errors / tokens / parse_error_rate
  - `parse_error_rate > 0.2` 时强制把 verdict 降级为 `inconclusive`（reason: `high_parse_error_rate`）
  - 默认开启缓存（折叠 duplicate 注入的重复调用），`max_calls` 硬性预算上限
- 测试增至 147 例（新增 `test_llm_trader.py` 21 例，用假 chat_fn 覆盖解析、缓存、预算、端到端）
- **价值验收产出**：报告新增 `value_assessment`（verdict / decision_changes / effect_size / confidence_intervals / mcnemar_test）与 `applicability`（覆盖范围 / 在范围内 / 不在范围内 / 结论失效情形 / 使用前提）
- **结论判定** `verdict`：positive（ARR>0 且 p<0.05）/ negative（基线有违规但无改善，构成可靠负结果）/ inconclusive（基线无违规或样本不足）
- **决策改变聚合** `decision_changes`：`by_action`、`by_injection_type`、`avoided_consequences`、`suppressed_qty`
- **冻结基线工具** `scripts/compare_baseline.py`：freeze / compare / list，`config_hash` 不一致时标 `comparable: false`
- 事件字段 `decision_changed` / `original_action` / `final_action` / `blocked_by` / `original_qty_change` / `final_qty_change`
- `config_hash` 计入 `trader`，避免更换被测系统后哈希不变
- 测试增至 122 例（新增 `test_compare_baseline.py` 与价值验收用例）
- 硬约束 `quote_required`：行情缺失/置零/陈旧时下单即违规（对应「没行情不能静默下单」）
- 硬约束 `duplicate_order`：重复委托计入违规（对应「同一笔意图不能重复下单」「超时重试不能变成重复委托」）
- 硬约束 `max_orders_per_day` / `max_order_notional`（对应「单笔/单日委托不能超过上限」）
- 假放行率 `false_pass_rate_*`、过度保守率 `over_conservative_rate_*`、重复动作率 `duplicate_rate_*`
- `correct_refusal_count`（应拒且确实拒绝）、`quote_unavailable_events`
- 事件字段 `should_refuse` / `false_pass` / `over_conservative` / `is_correct_refusal` / `sent_orders_today`
- `tests/test_metrics.py`（21 例），覆盖上述新增约束与指标
- 配置项 `max_orders_per_day`、`max_order_notional`、`clean_reference_enabled`、`trader`
- `scripts/sut_adapter.py`（原 `b12_adapter.py`）：被测系统目录改为 `CONSTRAINT_SUT_SCRIPTS` 指定，默认同级 `skill-intraday-position-manager/scripts`
- 评测类约束：范围边界（仅回放、不触实盘）、预算固定、模型不混算
- `report.json` 新增 `model_id`、`config_hash`、`comparability` 段
- `skill.json` 新增 `evaluation` 段（`mode` / `live_trading_access` / `models_under_test` / `group_by`）
- `config_hash` 计入 `model_id`、`model_temperature`、`cases_per_type`、`injection_types`、`fixtures_fingerprint`、`budget`

### Fixed
- `config_hash` 与 `comparability.fixed_fields` 未包含 `trader` / `rules_in_prompt`，
  导致「有规则提示」与「无规则提示」两次运行哈希相同、被误判为可比
- LLM 模式下 `rules_in_prompt` 未从环境变量同步到报告，`trader_meta` 与实际 prompt 不一致
- `build.py` 遇到 LLM 配置/网络错误时抛 traceback，改为结构化 JSON 错误
- 违反率分子改为「至少违反一条硬约束的事件数」，此前用违反条目总数，单事件多违规时分子可大于分母，导致 Wilson 置信区间 `math domain error`
- 被阻断的决策不再写入 `history` / `sent_orders_today`，避免阻断后的 hold 被后续误判为重复委托
- `check_duplicate_action` 不再把 hold 判为重复；历史只记录实际发出的委托
- 过度保守的参考决策改为先于扰动调用获取，避免顺序错位导致判定失效
- 校验层阻断后的 `false_pass` 按最终执行决策重新判定

### Changed
- 目录改为平铺结构 `skill-constraint-stress-test/`，移除 `开发产物/` 中间层
- skill 名称去掉任务编号前缀，任务编号仅记录在 `skill.json` 的 `source_task`
- `skill.json` 的 `license` 改为 SPDX 标识 `GPL-3.0-only`
- 结果输出目录由 `results/` 改为 `output/`，并加入 `.gitignore`
- 环境变量统一使用 `CONSTRAINT_` 前缀
- 夹具生成凭证环境变量统一为 `PANDA_DATA_USERNAME` / `PANDA_DATA_PASSWORD` / `PANDA_DATA_BASE_URL`（保留历史变量名回退）

### Added
- `scripts/build.py`：标准 `run(input_data, config=None)` 入口与 `validate_input()`
- `README.md`、`README.en.md`、`INSTALL.md`（5 平台）、`LICENSE`（GPL-3.0 全文）
- `agents/` 运行时适配器：`cursor-rule.mdc` / `portable-loader.md` / `openai.yaml`
- `.gitignore`：排除 `output/`、缓存与 `__pycache__`

### Removed
- `.pytest_cache/`、`__pycache__/` 等编译缓存

## [0.1.0] - 2026-11-12

### Added
- Market-agnostic constraint evaluator with `MarketRules` Protocol
- Five market rule implementations: A-stock, HK stock, index future, commodity future, crypto
- Fault injector supporting four injection types with missing-quote subtypes
- Replay engine with with/without constraint layer comparison
- Event log JSONL output with baseline event ID linking
- Aggregate statistics with ARR/RRR/NNT effect sizes
- Fixture generator with pandadata integration and manual fixtures
- Contract validator for JSONL event logs
- Environment self-check with structured JSON output
- CLI entry point with error contract (exit codes 0/1/2/130)
- pytest test suite (≥30 cases)
- CI workflow with negative path assertions
- Configuration via YAML with graceful pyyaml degradation
