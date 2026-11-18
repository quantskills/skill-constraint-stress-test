# 数据接入说明 / Data Access

给需要复现或扩展本 skill 的其他 AI 阅读。**核心结论先说**：

> 数据获取 = 调用 MCP 工具 **`mcp__pandadata__call_pandadata`**（由阅读 skill 的 AI 执行）。
> 脚本不导入任何取数组件、不读取任何凭证、不联网。
> 评测执行阶段**完全离线**，clone 即可运行。

---

## 1. 流水线总览

```
┌──────────────────────── agent 侧（需要 MCP + OAuth 登录）────────────────────────┐
│                                                                                 │
│  ① mcp__pandadata__auth_status           确认已登录                              │
│  ② mcp__pandadata__search_methods        找到方法名                              │
│  ③ mcp__pandadata__get_method_doc        确认参数签名 / 返回列                    │
│  ④ mcp__pandadata__call_pandadata        取数（窄区间分批，防截断）               │
│  ⑤ 把每个响应原样落盘为 JSON             mcp_exports/*.json                      │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼  mcp_exports/{suspended,st,trade_list,stock_daily}.json
┌──────────────────────── 本地脚本（纯标准库，不联网）─────────────────────────────┐
│                                                                                 │
│  scripts/mcp_source.py                                                 │
│     解析两种信封形态 / 校验 ok / 校验 truncated / 校验必需列 / 抛结构化错误  │
│                                   │                                             │
│                                   ▼                                             │
│  python3 scripts/fixture_generator.py --from-mcp mcp_exports/ --date 20251110    │
│     转成既有 fixture 结构 → fixtures/generated/*.json                            │
│                                   │                                             │
│                                   ▼                                             │
│  python3 scripts/build.py / run_eval.py                                          │
│     只读 fixtures/generated/*.json → replay_engine → report.json                 │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. MCP 连接契约

| 项 | 值 |
|---|---|
| 服务器名 | `pandadata` |
| 传输 | Streamable HTTP |
| 地址 | `https://pandadatamcp.pandaaiquant.com/mcp` |
| 认证 | OAuth，**用户在客户端登录** |
| skill 内的凭证 | **无**。skill 中不出现账号、密码或 token 等任何凭证 |

模型可见的工具名（Claude Code 形态）：

| 工具 | 用途 |
|---|---|
| `mcp__pandadata__auth_status` | 先确认已登录 |
| `mcp__pandadata__search_methods` | 按关键词检索方法 |
| `mcp__pandadata__get_method_doc` | 取方法文档（参数、返回列） |
| `mcp__pandadata__call_pandadata` | 实际取数 |

### 调用顺序

```
auth_status → search_methods → get_method_doc → call_pandadata
```

### `call_pandadata` 参数

| 字段 | 说明 |
|---|---|
| `method` | `get_*` 方法名 |
| `params` | 方法参数 dict |
| `preview_rows` | 0–2000，**只控制本地预览行数，不控制网关取数** |

### 返回信封

```json
{
  "ok": true,
  "method": "get_trade_list",
  "params": {"date": "20251110"},
  "result": [{"date": "20251110", "symbol": "000001.SZ"}],
  "request_id": "…"
}
```

`result` 两种形态：

1. **表格型**

```json
{
  "type": "dataframe",
  "columns": ["symbol", "date", "is_suspended"],
  "rows": [["000407.SZ", "20251110", true]],
  "row_count": 1,
  "total_rows": 1,
  "truncated": false,
  "truncation_reasons": []
}
```

2. **普通列表 / 字典**

```json
[{"date": "20251110", "symbol": "000001.SZ"}]
```

```json
{"date": "20251107"}
```

---

## 3. 数据需求 → 方法对照

| 数据需求 | MCP 方法 | 参数 | 返回 |
|---|---|---|---|
| 停牌标的 | `get_suspended_list` | `start_date`(必填), `end_date`(必填), `is_suspended`(可选), `symbol`(可选) | dataframe `symbol,date,is_suspended` |
| ST 标的 | `get_st_list` | `start_date`(必填), `end_date`(必填), `is_st`(可选), `symbol`(可选) | dataframe `symbol,date,is_st` |
| 在售股票列表 | `get_trade_list` | `date`(必填), `exchange`(可选) | 列表 `[{date,symbol}, ...]` |
| 日线（构造涨跌停） | `get_stock_daily` | `symbol`(list), `start_date`(必填), `end_date`(必填), `fields`(可选), `indicator`, `st` | dataframe `date,symbol,open,high,low,close,pre_close,volume,amount,limit_up,limit_down,trade_status,name` |

---

## 4. 可复制的 MCP 调用示例

**确认登录**

```
mcp__pandadata__auth_status({})
```

**检索方法（可选，用于确认方法名）**

```
mcp__pandadata__search_methods({"query": "停牌 suspended"})
```

**查看方法文档**

```
mcp__pandadata__get_method_doc({"method": "get_st_list"})
```

**取停牌列表**

```
mcp__pandadata__call_pandadata({
  "method": "get_suspended_list",
  "params": {"start_date": "20251110", "end_date": "20251110", "is_suspended": true},
  "preview_rows": 2000
})
```

**取 ST 列表**

```
mcp__pandadata__call_pandadata({
  "method": "get_st_list",
  "params": {"start_date": "20251110", "end_date": "20251110", "is_st": true},
  "preview_rows": 2000
})
```

**取交易列表**

```
mcp__pandadata__call_pandadata({
  "method": "get_trade_list",
  "params": {"date": "20251110"},
  "preview_rows": 2000
})
```

**取日线（分批：小 symbol 列表 + 单日）**

```
mcp__pandadata__call_pandadata({
  "method": "get_stock_daily",
  "params": {
    "symbol": ["000001.SZ", "000407.SZ", "000851.SZ"],
    "start_date": "20251110",
    "end_date": "20251110",
    "fields": ["open", "high", "low", "close", "pre_close"]
  },
  "preview_rows": 2000
})
```

> 把每个响应**原样**（完整信封）保存下来，不要手工裁剪 `rows`。

---

## 5. ⚠️ 截断：最重要的坑

MCP 网关会截断大结果。实测：请求 240 行分钟线，只返回 180 行，信封里写着
`"truncated": true, "truncation_reasons": ["byte_limit"]`。

**处理规则：**

- 取数时按**窄日期区间** + **小批次 symbol** 分批，每批单独保存
- 看到 `truncated: true` 必须**重新分批取**，不能继续用
- `scripts/mcp_source.py` 默认对 `truncated=true` 抛
  `MCPError(code="mcp_truncated")`，并提示缩小范围
- 只有显式传 `allow_truncated=True`（仅测试用）才会放行

被截断的数据当完整数据用，会**静默改变评测结论**——这是比报错严重得多的失败模式。

---

## 6. 落盘约定

把响应保存为下面的文件名（目录或单文件都支持）：

```
mcp_exports/
├── suspended.json          ← get_suspended_list 的完整响应（信封）
├── st.json                 ← get_st_list
├── trade_list.json         ← get_trade_list
└── stock_daily.json        ← get_stock_daily
```

同一方法分多片时：

```
mcp_exports/
├── stock_daily_20251110.json
├── stock_daily_20251111.json
└── ...
```

也支持：

- **单文件多方法**：`{"suspended": {...}, "trade_list": {...}}`
- **信封数组**：`[{"ok": true, "method": "get_st_list", "result": ...}, ...]`
- 文件名也认全名 `get_suspended_list.json`，以及 `mcp_` / `export_` 前缀

文件名识别规则见 `scripts/mcp_source.py::EXPORT_METHODS` 与 `_filename_key()`。

---

## 7. 夹具来源分布

`fixtures/generated/` 共 **109** 个夹具：

| 来源 | 数量 | 标记 |
|---|---|---|
| 由 MCP 取回的真实数据衍生 | 26 | 带 `"_source": "pandadata_*"` 字段 |
| 手工合成 | 83 | 无 `_source` 字段 |

### 真实数据衍生（26 个）

| 文件 | 量 | `_source` | 真实部分 | 来源方法 |
|---|---|---|---|---|
| `suspended_*.json` | 9 | `pandadata_suspended` | 标的代码来自真实停牌列表 | `get_suspended_list` |
| `st_*.json` | 10 | `pandadata_st` | 标的代码来自真实 ST 列表 | `get_st_list` |
| `limit_up_*.json` | 7 | `pandadata_limit_up` | 标的代码来自真实交易列表 + 日线 | `get_trade_list` + `get_stock_daily` |

> 注意：**代码是真实的，价格与盈亏是构造的**。例如 `suspended_000407_SZ_20251110.json` 用了真实停牌代码 `000407.SZ`，但 `price: 5.0`、`pnl_pct: -0.02` 是合成值——目的是稳定触发特定约束，而非还原当日行情。

### 手工合成（83 个）

按要触发的约束命名，覆盖 12 条硬约束的边界：

```
t_plus_1_*.json        10   当日买入锁定
position_limit_*.json  10   超仓位上限
force_close_*.json     10   强平窗口内买入
refusal_*.json         10   缺行情必须拒单
limit_up_down_*.json   10   涨跌停禁单
st_filter_*.json       10   ST 过滤
capital_check_*.json    5   资金不足
price_deviation_*.json  5   价格偏离（软约束）
suspended_*.json       10   停牌过滤
hk_normal / future_margin / crypto_t0   3   跨市场样例
```

这些是**确定性构造**的边界值，用于精确验证判定器，不依赖任何外部数据。

---

## 8. 为什么评测阶段完全离线

- `constraints_evaluator.py`、`replay_engine.py`、`injector.py`、`mcp_source.py` 全部**纯标准库**
- `mcp_source.py` 只读本地 JSON，不发起任何网络请求
- `env_check.py` 的 `mcp_data_source` 检查项**恒为 ok**：数据由 agent 侧 MCP 获取，缺失 MCP 也不会 blocked
- CI 不安装任何取数组件，221 个测试在 1 秒内跑完

**所以**：clone 之后直接 `python3 scripts/build.py` 就能跑，无需申请账号、无需登录 MCP。

---

## 9. 给其他 AI 的操作约定

1. **取数只走 MCP 工具**。不要试图 `pip install` 任何取数组件，也不要把账号密码写进代码、配置或文档。登录由用户在客户端 OAuth 完成。
2. **结果必须原样落盘**。保存完整信封（`ok` / `method` / `result` / `request_id`），不要手工裁剪或改写 `rows`。
3. **遇到 `ok=false` 立即停止**。先 `auth_status` 确认登录，再重试；`mcp_source.py` 会把 `ok=false` 转成 `MCPError`。
4. **遇到 `truncated=true` 必须分批重取**。窄日期区间、小批次 symbol，每批单独保存。
5. **不要假设真实价格**。带 `_source: pandadata_*` 的夹具，只有代码来自真实数据，数值是构造的。
6. **参数以 `get_method_doc` 为准**。方法签名会变化，凭记忆写容易失败。

---

## 10. 复现命令

```bash
# 完整评测（无需凭证、无需 MCP、完全离线）
python3 scripts/build.py

# 仅手工夹具（无需任何数据）
python3 scripts/fixture_generator.py --manual-only

# 从 MCP 导出重建真实数据衍生夹具（脚本本身不联网）
python3 scripts/fixture_generator.py --from-mcp mcp_exports/ --date 20251110

# 环境自检
python3 scripts/env_check.py --self-check --json

# 测试（全程离线）
python3 -m pytest tests/ -v
```