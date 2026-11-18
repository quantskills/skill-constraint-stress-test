# Multi-Platform Install Guide

This package contains one evaluation skill:

- `skill-constraint-stress-test`: AI-trader constraint stress test and fault-injection replay.

---

## Requirements

- Python 3.10+
- `pip install -r requirements-dev.txt` for running tests (PyYAML + pytest)
- **Nothing to install for data access.** Runtime and data both live on the QuantStudio host: data is fetched by the AI reading this skill through PandaData MCP (`mcp__pandadata__call_pandadata`), and `scripts/fixture_generator.py --from-mcp` consumes the saved JSON. The evaluation itself needs no external dependency and runs fully offline.

## Pre-flight checks

Before running anything, work through the checklist in `SKILL.md` (`## 前置检查`,
mirrored in `agents/openai.yaml` under `x-preflight`). Stop at the first failing item
and report only what is missing: never switch data source, never read a stub result as
AI-trader behaviour, and never present fixtures as same-day data without their landing date.

## PandaData MCP (only when regenerating fixtures)

This skill never stores credentials. The agent obtains data via MCP:

| Item | Value |
|---|---|
| Server | `pandadata` (Streamable HTTP) |
| URL | `https://pandadatamcp.pandaaiquant.com/mcp` |
| Auth | OAuth login in your MCP client |
| Tools | `mcp__pandadata__auth_status`, `search_methods`, `get_method_doc`, `call_pandadata` |

Register the server in your client, e.g. for Claude Code:

```bash
claude mcp add --transport http pandadata https://pandadatamcp.pandaaiquant.com/mcp
```

Then, in the agent session:

```
mcp__pandadata__auth_status({})
mcp__pandadata__call_pandadata({
  "method": "get_suspended_list",
  "params": {"start_date": "20251110", "end_date": "20251110", "is_suspended": true},
  "preview_rows": 2000
})
```

Save each response as JSON and run:

```bash
python3 scripts/fixture_generator.py --from-mcp mcp_exports/ --date 20251110
```

> Results can be truncated (`truncated: true`). Fetch in narrow date ranges; `scripts/mcp_source.py` refuses truncated data.

---

## Claude Code

```bash
# macOS / Linux
cp -R skill-constraint-stress-test ~/.claude/skills/
```

```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$HOME\.claude\skills"
Copy-Item ".\" "$HOME\.claude\skills\skill-constraint-stress-test" -Recurse -Force
```

Invoke in Claude Code:

```
/skill-constraint-stress-test
```

Or import from any agent:

```python
import sys
sys.path.insert(0, "~/.claude/skills/skill-constraint-stress-test")
from scripts.build import run

report = run({"cases_dir": "fixtures/", "output_dir": "output/"})
```

---

## Codex (OpenAI)

Use `agents/openai.yaml` as the tool definition, or register manually:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "skill_constraint_stress_test",
        "description": "Evaluate whether an AI trader respects trading constraints under fault injection (duplicate input, prompt perturbation, missing quotes, timeout/retry). Returns violation rate, duplicate-action rate, refusal count, and ARR/RRR/NNT effect sizes comparing with/without a constraint layer.",
        "parameters": {
          "type": "object",
          "properties": {
            "cases_dir": {
              "type": "string",
              "description": "Fixture directory. Defaults to fixtures/."
            },
            "output_dir": {
              "type": "string",
              "description": "Output directory. Defaults to output/."
            },
            "market_id": {
              "type": "string",
              "enum": ["CN_A_STOCK", "HK_STOCK", "INDEX_FUTURE", "COMMODITY_FUTURE", "CRYPTO"],
              "description": "Restrict evaluation to one market."
            },
            "injection_types": {
              "type": "array",
              "items": {
                "type": "string",
                "enum": ["duplicate", "prompt_perturb", "missing_quote", "timeout_retry"]
              },
              "description": "Restrict to specific fault types."
            }
          }
        }
      }
    }
  ]
}
```

Local run:

```bash
cd skill-constraint-stress-test
python3 scripts/build.py
python3 scripts/run_eval.py --cases-dir fixtures/ --output-dir output/
```

---

## Cursor

Add `agents/cursor-rule.mdc` to your project's Cursor rules (or copy it into `.cursor/rules/`), or add to `.cursorrules`:

```
When the user asks about constraint stress testing, fault injection, AI trader safety,
or violation-rate evaluation for trading systems, use the constraint-stress-test skill.

Run: python3 skill-constraint-stress-test/scripts/build.py
Outputs: output/report.json, output/events_*.jsonl
```

---

## Hermes

Use `agents/portable-loader.md` as the loader description, or register in your skill registry:

```yaml
# hermes_skills.yml
skills:
  constraint-stress-test:
    name: skill-constraint-stress-test
    type: build
    runtime: python3
    entry: skill-constraint-stress-test/scripts/build.py
    description: >
      AI-trader constraint stress test. Injects duplicate input, prompt
      perturbation, missing quotes, and timeout/retry faults, then replays
      decisions with and without a constraint-validation layer to report
      violation rate, duplicate-action rate, refusal count, and ARR/RRR/NNT.
    input_schema:
      type: object
      properties:
        cases_dir:
          type: string
        output_dir:
          type: string
        market_id:
          type: string
          enum: [CN_A_STOCK, HK_STOCK, INDEX_FUTURE, COMMODITY_FUTURE, CRYPTO]
        injection_types:
          type: array
          items:
            type: string
            enum: [duplicate, prompt_perturb, missing_quote, timeout_retry]
    constraints:
      - Python 3.10+ required
      - research and educational use only
```

---

## OpenClaw

OpenClaw consumes either `agents/openai.yaml` or `agents/portable-loader.md`.

```bash
# Register via the portable loader
openclaw skill add ./skill-constraint-stress-test --loader agents/portable-loader.md
```

Or point OpenClaw at the OpenAI-compatible tool definition:

```bash
openclaw tool register --from agents/openai.yaml
```

The skill exposes a single callable entry:

```python
from scripts.build import run
report = run(input_data={"cases_dir": "fixtures/"}, config=None)
```

---

## Verification

```bash
python3 -m pytest tests/ -v
python3 scripts/env_check.py --self-check --json
python3 scripts/build.py
```

Expected: all tests pass, self-check emits structured JSON, `output/report.json` is produced.

## Connect a real LLM

```bash
export CONSTRAINT_LLM_BASE_URL=https://api.example.com/v1
export CONSTRAINT_LLM_API_KEY=sk-xxx
export CONSTRAINT_LLM_MODEL=model-name
CONSTRAINT_TRADER=llm CONSTRAINT_CASES_PER_TYPE=3 python3 scripts/build.py
```

## Value acceptance workflow

```bash
# 1. Connect a real system under test (stub only validates the pipeline)
CONSTRAINT_TRADER=sut CONSTRAINT_SUT_SCRIPTS=/path/to/sut/scripts python3 scripts/build.py

# 2. Check the verdict — positive / negative / inconclusive
python3 -c "import json;r=json.load(open('output/report.json'));print(r['value_assessment']['verdict'])"

# 3. Freeze as a baseline for future regression comparison
python3 scripts/compare_baseline.py freeze output/report.json --name v1

# 4. After changes, compare against the frozen baseline
python3 scripts/compare_baseline.py compare output/report.json --against baselines/v1.json
```

A `negative` verdict is a valid deliverable: it states that the constraint layer has no incremental value under the current fixture and configuration, provided `config_hash` matches between runs.