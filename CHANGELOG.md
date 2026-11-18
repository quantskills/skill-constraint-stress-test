## [0.4.3] — 2026-11-19

### Fixed
- **文档用例数与逐文件表长期失真**：0.3.x 的 5 刀重构改掉了用例分布
  （`test_llm_trader.py` 24 → 13：代理 LLM 通道被删；`test_metrics.py` 37 → 41；
  `test_replay_engine.py` 11 → 16：补 McNemar 精确/渐近 p、配对口径、极小 p 不归零），
  但 `README.md` / `README.en.md` / `references/data_access.md` 仍写 219，
  `references/validation_evidence.md` 的头部计数与逐文件表也停在旧值。
  现在全部按 `pytest --collect-only` 实测改为 **221**，表内三行的数字与描述同步更新
  （`test_llm_trader.py` 不再声称覆盖 Anthropic 协议——那部分实现已删除）。

### Added
- `tests/test_doc_counts.py`（4 例）：把「文档 == 实测」钉成断言——两份 README 的套件总数、
  `data_access.md` 的 CI 用例数、证据文档头部的 用例总数/通过/失败、以及逐文件表的
  **漏列 / 多列 / 数字过期**三类失真。此前 cst 没有任何文档对账测试，这类过期全靠人眼，
  而 219 恰好是审核方最容易照着复跑的第一个数字。

---

## [0.4.2] — 2026-11-19

### Added
- `skill.json` 的 `evaluation.models_under_test` 按任务要求**逐模型单列**：
  `stub-trader`（链路自检，须判 `inconclusive`）、`sut-rule-engine`（判别臂，已跑，
  `positive`）、`host-agent`（宿主 agent 作答臂，已跑，`inconclusive`），
  以及 **`jev-dedicated` 与 `flash` 两行显式标 `not_evaluated`**（含未跑原因），
  并新增 `no_merge_policy`：不同模型指标不合并、不互为评审。
  此前依赖表只有 `stub-trader` 一行，「JEV 专用模型与 Flash 不可混作同一评审模型」
  这条协作要求只做到了「没混算」，没有做到「单列」。

---

## [0.4.1] — 2026-11-19

### Fixed
- **0.4.0 少发了一件自己声称发过的产物**：`output/report_sut_discriminative.json` 被
  `.gitignore` 的 `output/*` 挡住——放行清单是逐文件点名的，新增证据文件时忘记同步追加
  `!output/report_sut_discriminative.json`。锚点 `baselines/sut_discriminative.json` 当时已在包内，
  因此远端并非空证据，但 §2ter 引用的原始报告缺失。现补放行，并把「正向断言失败即中止提交」
  固化为流程要求（本次正是该断言在提交前抓出的）。

---

## [0.4.0] — 2026-11-19

### Added
- **判别臂运行随包固化**：`trader=sut`（会真实犯错）在 `cases_per_type=60` +
  `max_orders_per_day=8` + `max_order_notional=50000` 下：**35.28% → 0%**、拦下 126 笔、
  不一致对 127、渐进 p `5.069e-29`（精确 `1.175e-38`）、`verdict=positive`、
  ARR 0.3528 / RRR 1.0 / NNT 2.83。锚点 `baselines/sut_discriminative.json`，
  产物 `output/report_sut_discriminative.json`，说明见 `references/validation_evidence.md` §2ter。
  与 §2bis 的宿主 agent 守规臂（`inconclusive`）合起来构成**区分度证据**：
  同一套夹具与阈值，守规臂不判 positive、犯错臂判 positive——不是「加一层就归零」的自证。

### Fixed
- **极小 p 被显示成 `0.0`**：报告用 `round(p, 6)`，`1e-38` 量级会被压成 `0.0`，
  读者会误读成「p 恰好为 0」。改为 4 位有效数字输出，并加锚定用例。
- **调用数声明不实**：README 中/英原写「180 事件 ≈ 360 次调用」，实际干净输入参考调用
  **按不同持仓去重缓存**，不是每事件两次；真实上界约 `180 + 109 = 289` 次。已按实现改写。

---

## [0.3.3] — 2026-11-19

### Fixed
- **修正 0.3.2 的一处虚假交付**：那次提交声称已随包发布 `output/report.json` 与
  `output/agent_decisions_host.json`，实际**一个都没进包**。根因是 `.gitignore` 里
  整目录排除 `output/` 仍然存在，我却在文件末尾追加了 `output/*` + `!output/report.json`——
  git 对被整体忽略的目录**不会**再放行其中任何文件，negation 全部失效。
  现删除 `output/` 那一行，改为 `output/*` 加两条点名放行，并用 `git ls-files` 实证。
- 自查方式也跟着修正：原先用 `git ls-files | grep output/ | grep -v <放行清单>` 判断，
  当没有任何 output 文件被跟踪时该管道输出为空、退出码非零，会**打印出「OK」的假绿**。
  改为断言 `git ls-files` 必须**包含**那两个路径。

## [0.3.2] — 2026-11-19

### Added
- 随包发布本次真实模型运行的**实际产物**：`output/report.json`（宿主 agent 那一轮，
  config_hash `66d02fbaac0777e1`）与 `output/agent_decisions_host.json`（180 条作答原件，
  含那笔越限买入的自述理由）。此前包内只有锚点摘要，§2bis 的关键结论无法被外部直接复核。
- `.gitignore` 由整目录 `output/` 改为 `output/*` + 两份点名放行：既让证据可核，
  又不把机器本地可再生产物整包推上公网。

---

## [0.3.1] — 2026-11-18

### Added
- 以**宿主 agent** 作为被测系统跑通一次真实模型评测（180 请求 / 100% 作答 /
  违反率 0.56%→0% / McNemar 精确 p=1.0 → ），并固化为随包锚点
  （含 config_hash、夹具指纹、复现命令）。
  此前该通道的证据随内置 LLM 代理一起删除，包内没有任何真实模型运行痕迹。
-  新增 §2bis：记录这次运行，并写明它的真正信息量——
  唯一那笔越限买入是**决策器自己犯的**，且其自述理由与实际输入不符，被独立校验层拦下。

### Fixed
- 文档口径：不得把「守规模型下基线零违规」写成「校验层无用」或「AI 完全守规」，
  两者都超出  能支撑的范围。

---

## [0.3.0] — 2026-11-18

### Fixed（结论级）
- **McNemar p 值被系统性低估一半**：`erfc(sqrt(chi2/2)) / 2` 中的 `/2` 是多余的——df=1 卡方的生存函数
  本身就是 `erfc(sqrt(chi2/2))`。`b=5, c=0` 由「显著（0.0368）」纠正为「不显著（0.0736）」，
  足以翻转 `positive` 判定。现同时报渐进值与精确二项检验值，不一致对子 ≤100 时以精确值判定。
- **McNemar 配对基与结论口径不一致**：配对用 `violations`（含软约束）、`_verdict` 用
  `hard_violations`，改为统一 `hard_violations`，并在报告中输出 `pairing_basis`。
- **`config_hash` 未绑定实际生效的约束**：`max_orders_per_day` / `max_order_notional` 原先只在
  `fixed_fields` 里声明、不进哈希，改上限后 `compare_baseline` 仍判「可比」。
- **`fixtures_fingerprint` 全仓无赋值点**：它参与哈希却永远取到 `None`，换整套夹具哈希不变。
  现按实际载入夹具（键名＋来源文件＋内容）计算，并输出 `fixture_count` / `fixtures_bound`，
  两者不等时报告 `fixtures_partially_used` 告警。
- **`duplicate_time_window_seconds` 是装饰性旋钮**：参数收下却从不参与判定，文档写的「5 秒窗口」
  实为全历史窗口。现真正生效；输入时钟只有分钟粒度已写明；时间缺失时保守判为窗口内
  （「不知道时间」不许冒充「不在窗口内」）。
- **夹具绑定空转**：生成器写 `position_{i:04d}`，夹具编号却来自「按文件名排序后依次编号」，
  `cases_per_type=15` 时四类案例全挤在同一批 15 个夹具上，其余 94 个（86%）永不参与评测；
  引用不到时还会静默退回「第一个夹具」甚至硬编码假持仓。改为 round-robin 绑定并在引用不到时
  结构化报错 `position_ref_unbound`。
- **导出清单与评测各建一套案例**：请求身份是扰动输入的内容指纹，两处绑定逻辑不同步会造成
  「导出的请求在评测里对不上号」的静默失配。现共用 `injector.bind_machine_refs`。
- **`agent` 模式 replay CLI 必崩**：以 dict 调用 `run(cases_dir=...)`，`Path(dict)` 抛 `TypeError`
  且未被捕获；`--cases-per-type` 解析后也从未传入。
- **`build.run(input, config)` 的 config 覆盖被整体丢弃**：docstring 承诺「参数 > config > 配置文件
  > 默认值」，实际除 `output_dir` 外全部不生效。
- **配置文件里的 `injection_types` 不生效**：只进哈希、不参与案例选择。
- **环境变量类型转换**：默认值为 `None`/`dict`/`list` 的键原先原样塞字符串，
  `CONSTRAINT_MAX_ORDERS_PER_DAY=10` 会在约束比较处抛 `TypeError`。

### Removed
- 删除 skill 内部的 LLM 协议客户端（`_post_json` / `anthropic_messages_chat` /
  `openai_compatible_chat` / `detect_chat_fn` / `make_llm_trader` / `llm_trader_from_env`）与
  `CONSTRAINT_LLM_*` 通道：真实模型评测统一改走 `agent` 模式，由阅读本 skill 的 agent 亲自作答。
  `trader=llm` 现在显式报错 `trader_llm_removed`，不再静默降级。保留提示词渲染与决策解析。

### Tests
- 新增锚定用例：McNemar 精确/渐进值与「不砍半」回归锚点、`hard_violations` 配对基、
  窗口旋钮有效性（同输入只改窗口必须改变结论）、时间缺失保守判定。216 passed / 0 failed。

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
