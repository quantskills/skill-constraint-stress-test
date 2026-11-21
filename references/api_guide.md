# 数据接口（api_guide）

本 skill 只有一条取数通道：**PandaData MCP**，由阅读 skill 的 AI 调用，脚本只消费落盘的 JSON。
取数只有「agent 经 MCP 落盘」这一条通道，不存在其他取数方式，也不做任何静默降级。

## 1. MCP 服务器

| 项 | 值 |
|---|---|
| 服务器名 | `pandadata` |
| 传输 | Streamable HTTP |
| 地址 | `https://pandadatamcp.pandaaiquant.com/mcp` |
| 认证 | OAuth（用户在客户端登录） |
| skill 内凭证 | 无。不写入任何账号 / 密码 / token |

## 2. 工具清单与调用顺序

| 工具 | 用途 |
|---|---|
| `mcp__pandadata__auth_status` | 确认登录状态 |
| `mcp__pandadata__search_methods` | 按关键词检索方法名 |
| `mcp__pandadata__get_method_doc` | 取方法的参数签名与返回列 |
| `mcp__pandadata__call_pandadata` | 实际取数 |

```
auth_status → search_methods → get_method_doc → call_pandadata
```

### `call_pandadata` 参数

| 字段 | 类型 | 说明 |
|---|---|---|
| `method` | str | `get_*` 方法名 |
| `params` | dict | 方法参数 |
| `preview_rows` | int | 0–2000，**只控制本地预览行数，不控制网关取数** |

### 返回信封

```json
{"ok": true, "method": "...", "params": {...}, "result": ..., "request_id": "..."}
```

两种 `result`：

- 表格型：`{"type":"dataframe","columns":[...],"rows":[[...]],"row_count":N,"total_rows":N,"truncated":bool,"truncation_reasons":[...]}`
- 普通列表/字典：`[{"date":..., "symbol":...}]` / `{"date": ...}`

## 3. 本 skill 用到的 4 个方法

| 方法 | 参数 | 返回 |
|---|---|---|
| `get_suspended_list` | `start_date`(必填), `end_date`(必填), `is_suspended`(可选), `symbol`(可选) | dataframe：`symbol,date,is_suspended` |
| `get_st_list` | `start_date`(必填), `end_date`(必填), `is_st`(可选), `symbol`(可选) | dataframe：`symbol,date,is_st` |
| `get_trade_list` | `date`(必填), `exchange`(可选) | 列表 `[{date,symbol}, ...]` |
| `get_stock_daily` | `symbol`(list), `start_date`(必填), `end_date`(必填), `fields`(可选), `indicator`, `st` | dataframe：`date,symbol,open,high,low,close,pre_close,volume,amount,limit_up,limit_down,trade_status,name` |

## 4. 调用示例

```
mcp__pandadata__auth_status({})
```

```
mcp__pandadata__get_method_doc({"method": "get_suspended_list"})
```

```
mcp__pandadata__call_pandadata({
  "method": "get_suspended_list",
  "params": {"start_date": "20251110", "end_date": "20251110", "is_suspended": true},
  "preview_rows": 2000
})
```

```
mcp__pandadata__call_pandadata({
  "method": "get_st_list",
  "params": {"start_date": "20251110", "end_date": "20251110", "is_st": true},
  "preview_rows": 2000
})
```

```
mcp__pandadata__call_pandadata({
  "method": "get_trade_list",
  "params": {"date": "20251110"},
  "preview_rows": 2000
})
```

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

## 5. 字段映射（MCP 列 → 内部字段）

| MCP 列 | 内部字段 | 处理 |
|---|---|---|
| `symbol` | `symbol` / fixture `code` | 原样；缺或空 → `mcp_missing_columns` |
| `date` | `date` / fixture `_date` | 归一为 `YYYYMMDD` 字符串（数值/浮点均可） |
| `is_suspended` | 过滤条件 | 布尔化（`true/1/"true"`…）；无法解析 → `mcp_invalid_shape` |
| `is_st` | 过滤条件 | 同上 |
| `open/high/low/close/pre_close` | 同名 | 转 float；空值 → `None` |
| `limit_up/limit_down` | 同名 | 转 float |
| `name` | 同名 | 原样 |

## 6. 截断必须处理

网关会截断大结果（实测 240 行 → 180 行，`truncation_reasons: ["byte_limit"]`）。

- 取数按窄日期区间、小批 symbol 分批
- `truncated=true` → `scripts/mcp_source.py` 抛 `MCPError(code="mcp_truncated")`
- **绝不静默把截断数据当完整数据用**

## 7. 错误码（`MCPError.code`）

| code | 含义 | 处置 |
|---|---|---|
| `mcp_ok_false` | 信封 `ok=false` 且无 `error.code` | 先 `auth_status`，再重试 |
| `mcp_truncated` | 结果被截断 | 缩小日期区间 / symbol 批次后重取 |
| `mcp_missing_columns` | 表格缺必需列 | 用 `fields` 显式请求，或查 `get_method_doc` |
| `mcp_invalid_shape` | 结构不符（行列不齐、类型不对） | 检查是否手工改过导出文件 |
| `mcp_unknown_method` | 导出了本 skill 不用的方法 | 只导出 4 个方法之一 |
| `mcp_export_not_found` | `--from-mcp` 路径不存在或为空目录 | 先完成 MCP 取数并落盘 |
| `mcp_export_unreadable` | JSON 无法解析 | 原样保存响应，不要手工编辑 |
| `mcp_export_empty` | 目录里没有可识别的方法导出 | 按约定的文件名落盘 |

## 8. 离线边界

`scripts/mcp_source.py` 与 `scripts/fixture_generator.py` 均为**纯标准库、不联网**。
评测执行阶段只读 `fixtures/`，clone 即可运行。

## 9. 决策意图接入

只输出决策意图的外部系统使用 `skill-constraint-stress-test/intent-replay@1`，不需要订单、
成交或账户字段。协议、精确绑定、稳定排序、unmatched 非零退出，以及 block_rate /
false_block_rate 的定义与局限见 [`intent_replay.md`](intent_replay.md)。
