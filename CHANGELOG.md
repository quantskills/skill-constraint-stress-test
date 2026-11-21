## [0.5.1] — 2026-11-21

独立评审（以审核方视角）通过的补丁版本。0.5.0 的功能与结论不变，本条只修评审
实测暴露的两处失败模式；交易指标、锚点数值、`config_hash` 全部沿用 0.5.0 的重生成结果。

### Fixed（review：缺被测系统时静默全 hold → 结构化失败）
0.5.0 的 `trader=sut` 在被测系统不可用时，会静默导入本包 `scripts/build.py` 并吞成
逐条 hold，产出一份「零违规、零拦截」的假报告（详见下方条目）。

- **`trader=sut` 在被测系统不可用时会产出一份「看起来正常」的假报告。**
  `scripts/sut_adapter.py` 原以 `from build import run` 取被测系统入口，当被测系统目录
  不存在、而本包 `scripts/` 又在 `sys.path` 上时，会静默导入**本包自己的**
  `scripts/build.py`；那个 `run()` 返回 dict 而非 list，随后被 `except Exception`
  吞成逐条 hold。结果：退出码 0、`config_hash` 不变、报告显示
  `baseline_order_events=0`、违规 0、拦截 0、`inconclusive` —— 读起来像「被测系统很守规」，
  实际是「被测系统一次都没跑」。这是本包最坏的一种失败：既不可判也不可查。
- 新增 `SutUnavailable` 与 `resolve_sut()`：导入后**校验 `run()` 的来源文件**必须落在
  `CONSTRAINT_SUT_SCRIPTS` 之下，顶替与缺失都结构化报错；`sut_trader` 显式
  `except SutUnavailable: raise`，不再被兜底 `except Exception` 吞掉。
- `run_eval` 在进入回测**之前**调用 `resolve_sut()`，失败即抛 `InputValidationError`
  （`code=sut_unavailable`，退出码 2），不落任何报告产物。
- 新增 `tests/test_sut_adapter.py`（3 例）：缺目录、被本包 `build.py` 顶替、真 SUT
  在自身目录内三种情形；其中「真 SUT 必须通过」这例防止校验退化成一律拒绝。
- 注意：本包**不随带**被测系统。`trader=sut` 需要 `skill-intraday-position-manager`
  在同级目录，或用 `CONSTRAINT_SUT_SCRIPTS` 指过去。此前的文档措辞「内置规则引擎」
  不准确，已改为如实表述。

### Fixed（review：config_hash 对数字写法敏感）
- **同一个业务上限，写成 `50000` 与 `50000.0` 会得到两个不同的 `config_hash`**。
  原因是 `json.dumps` 保留 int/float 区别：YAML 里 `max_order_notional: 50000`
  解析成 int、`50000.0` 解析成 float。后果与「同条件可比」的承诺正好相反——
  两次本可比的运行被判为配置不一致（实测：复跑 sut 臂得 `038b1b8a`，
  而随包锚点是 `ab63975d`，仅因这一项类型不同）。
- 修法是 `_compute_config_hash` 经 `_hash_number` 把 `price_deviation_threshold`、
  `max_position_pct`、`duplicate_time_window_seconds`、`max_orders_per_day`、
  `max_order_notional` 归一化为 float；`None` 保持 `None`（＝不限制，与 `0` 不同义）。
  约束判定逻辑不变，只是把「写法差异」从可比性判断里剔除。
- **因此本版的 `config_hash` 与 0.4.x 不可直接比较**：锚点
  `baselines/sut_discriminative.json`、`output/report_sut_discriminative.json`
  与三份逐事件证据已按归一化后的哈希**重新生成**（同一次运行，`run_id=09422823`），
  指标未变：360 事件、135 条基线违规、134 次拦截、0 次误拦、`block_rate=0.9926`、
  `false_block_rate=0`、`p=9.008e-31`、verdict=`positive`。
- `skill.json` 的 `outputs.anchor_check` 命令原本缺 `--against`（照抄跑不通），一并修正。

---

## [0.5.0] — 2026-11-21

### Added（审核补交 1：只输出决策意图的回放接入版本）
- 新增 `scripts/intent_replay.py` 与 `intent-replay@1` 协议：外部只提供
  `record_id / case_id / time / code / action / qty_change / target_qty / reason`，
  复用既有 `replay_engine` 双评估，不接历史订单适配器，也不执行任何决策。
- 精确绑定优先级为 record_id → case_id；同案例按 time 稳定排序。未知/超次数记录进入
  `intent_replay.unmatched` 并非零退出，重复案例缺记录报 `missing_intents`，不以 hold 掩盖。
- 新增合成格式样例 `fixtures/intent_replay/example_intents.json`（明确非真实历史记录）、
  接入与指标文档 `references/intent_replay.md`、9 个接入契约测试。

### Added（审核补交 2：同批场景核验拦截与误拦）
- `summary` 与 `by_injection_type` 同时新增 `baseline_order_events`、
  `baseline_violation_events`、`blocked_on_violation`、`blocked_without_violation`、
  `block_rate`、`false_block_rate`、`cascade_unblocked` 及对账字段。
- 误拦判据是「有校验臂拦截，但同一决策在无校验参照臂无 hard violation」；
  **不改用或改写 over_conservative**。对账恒等式为
  `baseline_violation_minus_blocked = cascade_unblocked - blocked_without_violation`，
  显式解释被拦委托不进入 sent/history 所造成的级联差额。
- 两臂逐条证据增加双方 event ID、输入指纹及拦截/误拦/级联布尔字段；
  `.gitignore` 放行 events_with/without 与 pairwise 三份 JSONL。
- v0.5.0 sut 判别臂复跑：360 事件，135 条基线违规、134 次拦截、0 次误拦，
  `block_rate=0.9926`、`false_block_rate=0`、`cascade_unblocked=1`。

### Fixed（审核收件一致性与既有文档失真）
- 缺行情夹具的机器分配只进入 missing_quote；默认 stub 臂 `should_refuse` 从
  「missing 30 + prompt 10」修正为「missing 30，其余 0」，分类型 false_pass_rate
  分母改为该类型 should_refuse_count，并输出各类型计数。
- `skill.json` 证据清单逐文件标明被测系统与臂：report.json 实为 stub 臂，不再误称
  宿主 agent；宿主 agent 的真实归属是作答原件与历史冻结锚点。
- 删除文档中未接入的 injection_cases/expected_results 宣称，修正 `--output`、
  agent replay 命令及已移除模型通道的残留；删除不生效的 enable_constraint_checks 死配置。
- 测试总数按实收集更新为 **237**（其中 16 例为本版新增），README、data_access 与
  validation_evidence 同步对账；随后修哈希类型敏感缺陷再补 3 例，最终 **240**。

> 0.5.0 原始的 `config_hash` 类型敏感与 SUT 静默退化两处缺陷，修复合并在 **0.5.1**。

---

## [0.4.3] — 2026-11-19