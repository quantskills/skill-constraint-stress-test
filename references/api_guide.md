# 数据接口（api_guide）

本 skill 有两条行情通道，产出结构完全相同，调用方通过参数选择，绝不静默切换。

## 1. panda_data（生产通道）

| 项 | 值 |
|---|---|
| 包名 | `panda-data==1.0.6` |
| Python | 实测 3.10+（SDK 自身声明） |
| 入口 | `scripts/data_loader.py::load_panda` |
| 认证 | `panda_data.init_token(username=..., password=..., base_url=..., country="CN")` |
| 默认服务地址 | `https://pandadata.pandaaiquant.com` |

### 凭证

只从环境变量读取，任一别名命中即可：

| 变量 | 别名 |
|---|---|
| `PANDA_DATA_USERNAME` | `PANDADATA_USERNAME` |
| `PANDA_DATA_PASSWORD` | `PANDADATA_PASSWORD` |
| `PANDA_DATA_BASE_URL` | `PANDADATA_BASE_URL`（可选） |

缺失时抛 `DataUnavailable`，不做任何降级。

### 使用的接口

| 接口 | 用途 | 关键参数 |
|---|---|---|
| `get_future_dominant` | 主力连续日线，品种代码如 `RB` | `underlying_symbol`, `start_date`, `end_date` |
| `get_future_daily` | 具体合约日线，如 `RB2610` | `symbol`, `start_date`, `end_date` |

选择规则：`symbol` 含数字视为具体合约，走 `get_future_daily`；否则走 `get_future_dominant`。

### 字段映射

| panda_data 列 | 内部字段 |
|---|---|
| `trade_date` / `date` | `date`（`YYYYMMDD`） |
| `open/high/low/close` | 同名 |
| `volume` | `volume`（缺失记 0） |

### 调用示例

```python
from data_loader import load_bars

loaded = load_bars("RB", "20240101", "20241231")
print(loaded["source"])   # "panda"
print(loaded["bars"][0])
```

## 2. CSV fallback（离线通道）

用于无凭证、无网络或 CI 环境。

文件要求：表头为 `date,open,high,low,close[,volume]`，`date` 为 `YYYYMMDD` 或 `YYYY-MM-DD`。

```python
loaded = load_bars("RB", "20240101", "20241231", csv_path="tests/fixtures/bars_RB.csv")
print(loaded["source"])   # "csv"
```

`load_csv` 会校验列缺失、数值可解析、非空行；任何一条不满足都抛 `DataUnavailable` 并指出具体原因。

## 3. 观点文本输入

观点文本不来自行情接口，由调用方提供。契约见 `references/schema.md`。

必备字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `text` | 是 | 观点原文 |
| `published_at` | 回测必需 | ISO-8601，抽取阶段可为 null |
| `id` | 否 | 缺省 `doc001` |
| `source` | 否 | 来源标识 |

## 4. 合法性与时间戳前提

- 观点来源必须**合法可用**：公开网页、公开报告、获得授权的终端数据。本 skill 不提供任何抓取实现，也不内置任何来源。
- 时间戳必须是**首次发布时间**，不是最后编辑时间。若来源只能给出编辑时间，必须在上游显式修正，不能靠本 skill 猜。
- 同一观点被多处转载时，应在上游去重，只保留首次发布那条。本 skill 不做跨来源去重，重复输入会被当成多条独立观点。
- 覆盖要求：样本必须包含**沉默（未表态）、反转（否定/修正）、错误观点**，不能只留命中的。

## 5. 市场范围

| 项 | 范围 |
|---|---|
| 支持 | 内盘商品期货、股指期货（日线） |
| 品种映射 | 白名单，见 `scripts/common.py::INSTRUMENTS` |
| 不支持 | 表外品种、外盘合约、期权、股票（本版本） |
| 频率 | 日线，收盘口径 |

期货模拟赛核验时，若某品种或接口不可用，应在报告 `applicability.out_of_scope` 中明确写出等价回放范围，而不是替换成别的品种继续跑。