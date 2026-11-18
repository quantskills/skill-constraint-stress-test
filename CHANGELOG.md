# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-18

### Added — 前置检查（QuantStudio 宿主口径）

`SKILL.md` 原先把取数前提写在「数据获取」章节里，评测与桩件的边界写在后面，
只读适配器的 agent 会跳过它们直接跑评测：把 `trader=stub` 的 hold 当成
「AI 交易员守住了约束」，或把三年前落盘的夹具当成当日行情报违反率。

- `SKILL.md` 顶部新增 `## 前置检查（未通过就停：只报告缺什么，不加载后续流程）`，
  排在所有可执行流程之前，按两条线路写阻塞语义：生成 / 更新夹具时 MCP 会话
  （`auth_status` 且 `reauth_required: false`）、权限探针（`get_trade_list`，
  `data_permission_required` 即停）、截断处理是阻塞项；用既有夹具评测时 MCP 不是阻塞项
  （`mcp_data_source` 恒为 ok 属设计如此），但必须写明夹具来源与生成日期；
  `trader=stub` 必须判 `inconclusive`，温度 0 与可比性字段必须与对照臂一致。
- 新增 `tests/test_preflight.py`（5 例）：小节存在且覆盖宿主前提、排在所有可执行流程之前、
  两条线路阻塞语义分明、桩件结论不得写成交易员表现、文档不再描述取数客户端与凭证符号。
- `agents/openai.yaml` 新增 `x-preflight` 块；`agents/portable-loader.md`、
  `agents/cursor-rule.mdc` 与 `INSTALL.md` 都接上这一步，README 快速开始加第 0 步。
- 文档口径统一为「运行环境 = QuantStudio 宿主，数据 = PandaData MCP 落盘」：
  `SKILL.md` / `README*.md` / `INSTALL.md` / `references/*.md` / `agents/*` /
  `scripts/env_check.py` / `scripts/fixture_generator.py` / `requirements*.txt` /
  `skill.json` 中的「数据 SDK」描述全部改写（已发布版本的历史记述未改动）；
  `env_check` 的 `mcp_data_source.found` 一并标注 QuantStudio 宿主。
- 模型侧措辞澄清：`README.md` 的自定义接入不再提「官方 SDK」，
  `scripts/llm_trader.py` 写明只用标准库 `urllib`，不引入第三方客户端库。
- 文档测试计数 214 → 219；`references/validation_evidence.md` 补上此前漏列的
  `test_agent_mode.py`（14 例）。

### Changed
- **数据获取方式彻底改为 PandaData MCP**：由阅读 skill 的 AI 调用
  `mcp__pandadata__call_pandadata` 取数并落盘 JSON，脚本只消费本地文件。
  skill 内不再出现任何取数客户端、凭证读取或网络调用。
  - `scripts/fixture_generator.py`：删除取数客户端初始化函数、凭证环境变量读取与
    token 初始化调用；新增 `--from-mcp <目录或文件>`，
    读取 MCP 导出并转成既有 fixture 结构（字段口径不变，输出与仓库中已发布夹具逐字节一致）
  - `scripts/env_check.py`：删除取数客户端依赖检查，新增不联网的 `mcp_data_source` 检查项
    （恒为 `ok`），并说明「数据由 agent 通过 MCP 获取，本 skill 执行阶段只读本地 fixture」
  - `requirements.txt` / `requirements-dev.txt`：移除取数客户端依赖
  - 文档（`SKILL.md`、`README.md`、`README.en.md`、`INSTALL.md`、
    `references/api_guide.md`、`references/data_access.md`、`agents/*`、
    `skill.json` 的 `data_source_note`）全部改写为 MCP 取数口径，
    给出「数据需求 → 方法 + 参数」对照表与完整调用顺序
  - CI 负路径断言由「缺取数客户端依赖时 warn」改为「数据源为 agent 侧 MCP 且恒不阻塞」
- `references/data_access.md` 重写为 MCP 流水线图：
  `MCP 取数 → 落盘 JSON → fixture_generator.py --from-mcp → build.py`

### Added
- `scripts/mcp_source.py`（纯标准库）：解析 MCP 两种返回形态（`type=dataframe` 与普通列表/字典）、
  校验 `ok` / `truncated` / 必需列，抛出带 `code` / `message` / `hint` / `details` 的 `MCPError`
- `tests/test_mcp_source.py`（48 例，全程离线）：两种信封形态、`ok=false`、
  截断拒绝与显式放行、缺列、行列不齐、导出目录 / 单文件 / 多方法 / 信封数组加载、
  未识别方法与不可读 JSON
- 截断防御：MCP 结果 `truncated=true` 时结构化报错并提示按窄日期区间分批，
  绝不静默把截断数据当完整数据使用（实测 240 行只回 180 行，`truncation_reasons: ["byte_limit"]`）

### 说明
- 评测执行阶段仍然**完全离线**：已生成的 109 个夹具继续随仓库发布，clone 即可运行
- 测试总数 166 → 214，全绿

### Added
- `references/data_access.md`：数据接入说明（凭证来源、夹具真实/合成比例、离线边界、给其他 AI 的操作约定）
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
- 夹具生成的凭证读取曾改为统一前缀的环境变量
  （**已在 Unreleased 中移除**：数据改为 agent 侧 MCP 获取，skill 内不再读取任何凭证）

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
