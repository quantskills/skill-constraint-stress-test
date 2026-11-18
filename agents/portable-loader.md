# skill-constraint-stress-test

AI trader constraint stress test and fault-injection replay.

## Overview

This skill injects abnormal conditions into an AI trader's input stream and replays the resulting decision chain twice — once with a constraint-validation layer and once without — to quantify how many trading-constraint violations the layer actually prevents.

Four fault types are covered: duplicate input, prompt perturbation, missing quotes, and timeout/retry.

## Architecture

```
Fixtures  →  Fault Injector  →  Replay Engine  ─┬─→ with checks    ─┐
(fixtures/)   (4 types)          (decision log)   └─→ without checks ─┴─→ Aggregate Stats
                                                        (violations, duplicates, refusals, ARR/RRR/NNT)
```

## Directory Structure

```
skill-constraint-stress-test/
├── SKILL.md
├── README.md / README.en.md
├── LICENSE
├── INSTALL.md
├── requirements.txt / requirements-dev.txt
├── skill.json
├── CHANGELOG.md
├── agents/                     ← Runtime adapters
├── config/
│   └── eval_config.yaml        ← Thresholds and case counts
├── scripts/
│   ├── build.py                ← Standard entry: run(input_data, config=None)
│   ├── run_eval.py             ← CLI orchestrator
│   ├── constraints_evaluator.py
│   ├── replay_engine.py
│   ├── injector.py
│   ├── market_rules.py
│   ├── rules/                  ← Per-market rule implementations
│   ├── config.py
│   ├── env_check.py
│   ├── validate.py
│   ├── fixture_generator.py
│   └── mcp_source.py           ← MCP envelope parsing / truncation & column checks
├── tests/
├── references/
└── fixtures/
```

## Quick Start

Work through the pre-flight checklist (`## 前置检查` in `SKILL.md`, mirrored in
`agents/openai.yaml` under `x-preflight`) before anything else: stop at the first
failing item and report only what is missing.

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
python3 scripts/build.py
```

## Agent Integration

```python
import sys
from pathlib import Path

skill_root = Path(__file__).resolve().parent
sys.path.insert(0, str(skill_root))

from scripts.build import run

report = run({
    "cases_dir": str(skill_root / "fixtures"),
    "output_dir": str(skill_root / "output"),
    "injection_types": ["duplicate", "missing_quote"],
})

print(report["summary"]["violation_rate_with_checks"])
print(report["effect_size"])
```

## Markets

| market_id | Constraints enforced |
|---|---|
| `CN_A_STOCK` | 100-share lots, T+1, price limits, suspended/ST filter, position cap, 14:45 force-close |
| `HK_STOCK` | Board lots, 15:45 force-close window |
| `INDEX_FUTURE` | Contract multiplier, margin |
| `COMMODITY_FUTURE` | Contract multiplier, margin |
| `CRYPTO` | T+0 extension example |

## Outputs

| File | Content |
|---|---|
| `output/report.json` | Aggregate statistics |
| `output/events_with_checks.jsonl` | Event stream with constraint layer |
| `output/events_without_checks.jsonl` | Event stream without constraint layer |
| `output/pairwise_comparison.jsonl` | Per-event paired comparison |

## Tests

```bash
python3 -m pytest tests/ -v
```

Covers the constraint evaluator, fault injector, market rules, replay engine, and event-log validator.

## Dependencies

- Python 3.10+
- PyYAML (optional; built-in defaults used when absent)
- pytest (dev only)
- Runtime and data live on the QuantStudio host: the agent fetches data through PandaData MCP, and the skill ships no fetching client
  (`mcp__pandadata__call_pandadata`) and saved as JSON; fixture generation consumes it
  via `scripts/fixture_generator.py --from-mcp`. Evaluation needs no external calls.

## Data Acquisition (MCP)

Server `pandadata`, Streamable HTTP, `https://pandadatamcp.pandaaiquant.com/mcp`, OAuth login.
Call order: `auth_status` → `search_methods` → `get_method_doc` → `call_pandadata`.

| Data need | Method | Parameters |
|---|---|---|
| Suspended symbols | `get_suspended_list` | `start_date`, `end_date`, `is_suspended`, `symbol` |
| ST symbols | `get_st_list` | `start_date`, `end_date`, `is_st`, `symbol` |
| Tradable list | `get_trade_list` | `date`, `exchange` |
| Daily bars | `get_stock_daily` | `symbol` (list), `start_date`, `end_date`, `fields`, `indicator`, `st` |

```
mcp__pandadata__call_pandadata({
  "method": "get_trade_list",
  "params": {"date": "20251110"},
  "preview_rows": 2000
})
```

Save each raw response as JSON under `mcp_exports/`, then:

```bash
python3 scripts/fixture_generator.py --from-mcp mcp_exports/ --date 20251110
```

Results may be truncated (`truncated: true`); fetch narrow date ranges and never use
truncated output as if complete.

## Evaluation Policy

- **Replay only**: runs on local fixtures; never connects to a live account, trading gateway, or production order channel. Never place a real order to trigger a test scenario.
- **Fixed budget & config**: `model_id`, `model_temperature`, `cases_per_type`, `injection_types`, `budget`, and all decision thresholds feed into `config_hash`; the report echoes them under `comparability`.
- **No mixing across models**: different models must produce separate reports and must not be merged into one metric.

## Notes

Research and educational use only. Not investment advice. Results are simulated replays, not actual trading performance. This is a QuantSkills community project and is not officially audited or certified.