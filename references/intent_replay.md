# 决策意图回放接入与拦截指标口径

本文对应两项收件核验：外部系统可以只交付**决策意图**进入同一双评估引擎；同一批异常场景可以同时核对**拦截**与**误拦**。引擎不接收订单、成交回报、账户号或委托号，也绝不执行决策。

## 1. 接入协议

文件必须是 UTF-8 JSON：

```json
{
  "protocol": "skill-constraint-stress-test/intent-replay@1",
  "records": [
    {
      "record_id": "external-unique-id",
      "case_id": "dup_0000",
      "time": "10:30",
      "code": "600036",
      "action": "buy",
      "qty_change": 100,
      "target_qty": 900,
      "reason": "decision intent only"
    }
  ]
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| `record_id` | str | 非空且文件内唯一；可直接等于案例 ID |
| `case_id` | str / null | 用于精确绑定生成案例 |
| `time` | str | 零填充 `HH:MM`；同一案例内按它稳定排序 |
| `code` | str | 决策标的代码，不是账户或委托标识 |
| `action` | str | `buy` / `sell` / `hold` |
| `qty_change` | int | buy > 0、sell < 0、hold = 0 |
| `target_qty` | int / null | null 时按 `current_qty + qty_change` 补全 |
| `reason` | str / null | 决策自述；约束判定不采信自述代替输入 |

可运行样例：

```bash
python3 scripts/intent_replay.py \
  --intents fixtures/intent_replay/example_intents.json \
  --cases-dir fixtures \
  --output-dir output/intent_replay
```

`fixtures/intent_replay/example_intents.json` 只是用现有合成夹具构造的**接入格式示例**，不是真实历史决策记录，也不是脱敏订单或成交记录。样例同时演示 `record_id` 直接绑定和 `case_id` 回退绑定。

## 2. 绑定、覆盖与失败边界

1. `record_id` 精确命中案例 ID 时优先；否则用 `case_id` 精确命中。不会模糊匹配，也不会按数组下标猜测案例。
2. 同一案例多条记录按 `time` 稳定排序；同刻记录保持输入文件顺序。`duplicate` 案例的每次 repeat 都必须各有一条意图。
3. 少于案例所需出现次数：报 `missing_intents` 并退出，**不会用 hold 补齐**。否则漏记录会被误读成交易员主动拒绝。
4. 未知案例记录进入 `intent_replay.unmatched`，原因是 `case_not_found`；超过案例出现次数的记录也进入该数组，原因是 `occurrence_overflow`。
5. `unmatched` 非空时，匹配部分仍生成两臂产物以便定位问题，但 CLI 退出码为 2，`verdict` 降为 `inconclusive/unmatched_intent_records`，不得把部分结果当完整结论。
6. 协议错误、字段类型错误、数量方向不符、重复 `record_id` 都是输入错误；不会静默降级。

接入复用 `run_eval → replay_engine.run_evaluation`，不是另一套统计实现。每次引擎调用只返回决策 dict；有/无校验两臂共享同一条原始意图。外部接入关闭干净输入参考调用，因为清单中没有这类额外记录。

## 3. 两臂逐条证据

| 文件 | 内容 |
|---|---|
| `events_without_checks.jsonl` | 无校验参照臂：保留原始意图，同时记录 hard/soft violations |
| `events_with_checks.jsonl` | 有校验臂：hard violation 触发时改为 hold，`was_blocked=true` |
| `pairwise_comparison.jsonl` | 同一事件两臂配对，含 `event_id`、`baseline_event_id`、`case_id`、`input_fingerprint` 和拦截判据 |

逐条布尔字段：

- `blocked_on_violation`：有校验臂拦截，且无校验参照臂确有 hard violation。
- `blocked_without_violation`：有校验臂拦截，但无校验参照臂没有 hard violation，即本报告的误拦。
- `cascade_unblocked`：无校验参照臂有 hard violation，但有校验臂未拦截。
- `baseline_is_order`：参照臂原始意图是 buy/sell 且数量非零。

## 4. 聚合指标定义

以下字段同时出现在 `summary` 与 `by_injection_type`：

| 字段 | 定义 / 分母 |
|---|---|
| `baseline_order_events` | 无校验臂中构成委托的事件数：buy/sell 且 `qty_change != 0` |
| `baseline_violation_events` | 无校验臂至少一条 hard violation 的事件数 |
| `blocked_count` | 有校验臂 `was_blocked=true` 的事件数 |
| `blocked_on_violation` | 拦截且参照臂确有 hard violation 的事件数 |
| `blocked_without_violation` | 拦截但参照臂没有 hard violation 的事件数 |
| `block_rate` | `blocked_count / baseline_order_events` |
| `false_block_rate` | `blocked_without_violation / blocked_count` |
| `cascade_unblocked` | `baseline_violation_events - blocked_on_violation` |
| `cascade_unblocked_by_constraint` | 上述事件按参照臂 hard violation 名称拆分；一条事件可贡献多个名称 |
| `baseline_violation_minus_blocked` | `baseline_violation_events - blocked_count` |

对账恒等式：

```text
baseline_violation_minus_blocked
  = cascade_unblocked - blocked_without_violation
```

两臂共享原始意图，但被拦委托不会进入有校验臂的 `sent/history`。因此后续 `max_orders_per_day` 或 `duplicate_order` 可能只在无校验臂触发，形成级联差额。v0.5.0 的 sut 判别臂实测为 135 条基线违规事件、134 条拦截、0 条误拦、1 条 `cascade_unblocked`；该事件同时含 `duplicate_order` 与 `max_orders_per_day` 两个名称，所以按约束拆分数可以大于事件数。

`should_refuse_count` 按注入类型列出；`summary.should_refuse_by_injection_type` 给出同一份映射。分类型 `false_pass_rate_*` 的分母是该类型的 `should_refuse_count`，不是该类型全部事件。机器分配时 price 为 null/0 的夹具只进入 `missing_quote`，避免污染 `prompt_perturb` 的分母。

## 5. 误拦判据的设计选择与局限

**误拦以无校验臂为操作参照，而不是以交易员自述或 `over_conservative` 为参照。** `over_conservative` 仍只描述被测交易员在干净参考下会下单、扰动后却主动 hold 的行为；校验层阻断不计入它，两种指标不得合并。

这个判据有明确边界：

- 无校验臂也运行同一个约束判定器，并非独立的人工 ground truth。如果判定器漏报，正确拦截可能被记成 `blocked_without_violation`；若判定器误报，它又会进入 `blocked_on_violation`。因此该率核验的是“两臂一致性下的误拦”，不能替代规则正确性审计。
- 两臂 sent/history 会分叉。判据仍逐条比较同一意图，但后续事件所处的委托历史不同；需要结合 `cascade_unblocked_by_constraint` 与逐条证据判断，不能只看一个百分比。
- 分母为 0 时比率按既有报告约定输出 0.0。`blocked_count=0` 时 `false_block_rate=0.0` 只表示没有可判误拦的拦截样本，不代表已经证明“永不误拦”。
- 当前样例与 sut 证据均来自合成夹具，不是实盘历史表现。接入外部记录时仍须人工确认案例绑定、时间顺序和规则版本是否符合业务语境。
