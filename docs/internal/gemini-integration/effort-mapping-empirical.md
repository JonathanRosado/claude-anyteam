# Empirical Codex effort → Gemini thinking mapping

Date: 2026-04-24 local / 2026-04-25 UTC  
Researcher: `codex-effort-researcher`  
Gemini CLI: `0.39.0` (`/usr/local/bin/gemini`)  
Branch: `feat/gemini-integration`

## Executive recommendation

Implement Option B with adapter-owned `modelConfigs.customAliases` in the isolated Gemini settings file and pass the generated alias via `gemini --model <alias>`.

### Gemini 2.5-style `thinkingBudget` mapping

| Codex effort | Recommended Gemini alias config | Rationale |
|---|---:|---|
| `minimal` | `extends: "base"`, `model: <2.5 model>`, `thinkingConfig: { "thinkingBudget": 0 }` | Works only when we do **not** inherit `chat-base-2.5`, because that parent sets `includeThoughts: true`; extending the normal `gemini-2.5-flash` alias with budget 0 produced a 400. Minimal is therefore a special no-thinking alias, not a normal child override. |
| `low` | `thinkingBudget: 512` | Gemini CLI itself uses 512 for its built-in `classifier` alias. My 256 run solved the task, but it was noisier/slower and needed extra recovery; 512 is the smallest documented built-in thinking budget and a safer low tier. |
| `medium` | `thinkingBudget: 1024` | 1024 solved the canonical task cleanly with fewer tool calls than 256 and without jumping to the default 8192 behavior. |
| `high` | `thinkingBudget: 4096` | Matches the installed docs' agent-specific override example and gave deeper exploration/tooling than 1024. |
| `xhigh` | `thinkingBudget: 8192` | Matches Gemini CLI's built-in `chat-base-2.5` default (`DEFAULT_THINKING_MODE` / docs show 8192). 16384 worked but added no clear success benefit on this task and caused more edit retries, so it should remain an opt-in override rather than Codex `xhigh`. |

### Gemini 3-style `thinkingLevel` mapping

| Codex effort | Recommended Gemini 3 alias config | Rationale |
|---|---|---|
| `minimal` | Prefer a base/no-thinking model alias if available; otherwise `thinkingLevel: "LOW"` | Gemini 3 exposes coarse levels, not numeric budgets. There is no tested exact no-thinking equivalent for Gemini 3. |
| `low` | `thinkingLevel: "LOW"` | LOW solved the canonical task. |
| `medium` | `thinkingLevel: "MEDIUM"` | MEDIUM solved the canonical task with the lowest observed latency on this run. |
| `high` | `thinkingLevel: "HIGH"` | HIGH solved the canonical task and is the built-in Gemini CLI default for `chat-base-3`. |
| `xhigh` | `thinkingLevel: "HIGH"` | No higher exposed Gemini 3 level exists in the installed docs/schema. Document this lossy mapping. |

## Documentation and CLI inspection

`gemini --help` confirms there is no `--effort`, `--thinking`, or `--thinking-budget` flag. Headless mode is `gemini -p/--prompt`; model selection is `--model`; output can be `--output-format stream-json`; non-interactive auto-approval can be `--approval-mode yolo`.

The generation-settings doc is present at the location predicted by the parity research:

```text
/usr/local/lib/node_modules/@google/gemini-cli/bundle/docs/cli/generation-settings.md
```

I read it in full. Key points:

- Model configuration lives under `modelConfigs` in `settings.json`.
- `customAliases` are named presets with optional `extends` inheritance.
- `overrides` are conditional rules matched by `model` and/or `overrideScope`.
- `generateContentConfig` is passed through to `@google/genai` with minimal validation.
- `thinkingConfig` is accepted inside `generateContentConfig`, including `thinkingBudget` and `includeThoughts`.

I also inspected the bundled configuration reference and runtime bundle. They show built-ins equivalent to:

```json
{
  "chat-base-2.5": {
    "extends": "chat-base",
    "modelConfig": {
      "generateContentConfig": {
        "thinkingConfig": { "thinkingBudget": 8192 }
      }
    }
  },
  "chat-base-3": {
    "extends": "chat-base",
    "modelConfig": {
      "generateContentConfig": {
        "thinkingConfig": { "thinkingLevel": "HIGH" }
      }
    }
  },
  "classifier": {
    "extends": "base",
    "modelConfig": {
      "model": "gemini-2.5-flash-lite",
      "generateContentConfig": {
        "maxOutputTokens": 1024,
        "thinkingConfig": { "thinkingBudget": 512 }
      }
    }
  },
  "prompt-completion": {
    "extends": "base",
    "modelConfig": {
      "model": "gemini-2.5-flash-lite",
      "generateContentConfig": {
        "maxOutputTokens": 16000,
        "thinkingConfig": { "thinkingBudget": 0 }
      }
    }
  }
}
```

The runtime bundle also destructures both `overrides` and `customOverrides`, so the adapter can use `customAliases` for the generated effort aliases and does not need `customOverrides` for this mapping.

## Test harness

The harness ran Gemini in an isolated `$HOME` with only copied auth files and a generated `~/.gemini/settings.json`. Each case used a fresh tiny Python repo and invoked Gemini in headless mode.

Important implementation detail discovered empirically: budget 0 must not inherit `chat-base`/`gemini-2.5-*`, because `chat-base` includes `includeThoughts: true`; the API rejects `includeThoughts` when thinking is disabled. The harness therefore special-cases budget 0 to extend `base` and set the concrete model directly.

```python
#!/usr/bin/env python3
import json, os, shutil, subprocess, tempfile, time
from pathlib import Path

BUDGETS = [0, 256, 1024, 4096, 8192, 16384]
LEVELS = ["LOW", "MEDIUM", "HIGH"]
MODEL_25 = os.environ.get("GEMINI_TEST_BASE_MODEL", "gemini-2.5-flash")
MODEL_3 = os.environ.get("GEMINI3_TEST_BASE_MODEL", "gemini-3-flash-preview")
ROOT = Path(tempfile.mkdtemp(prefix="gemini-effort-"))

PROMPT = """
You are in a small Python repo. Fix the implementation so the tests pass.
Inspect the files, run `python -m unittest -q`, make the minimal code change,
run the tests again, and finish with a concise summary. Do not ask for confirmation.
""".strip()

CALC = r'''
import re
_TOKEN = re.compile(r"(?P<num>\d+)\s*(?P<unit>ms|s|m|h)", re.I)

def parse_duration(text: str) -> int:
    """Return duration in milliseconds."""
    text = text.strip()
    match = _TOKEN.fullmatch(text)
    if not match:
        raise ValueError(f"invalid duration: {text!r}")
    value = int(match.group("num"))
    unit = match.group("unit").lower()
    if unit == "ms": return value
    if unit == "s": return value * 1000
    if unit == "m": return value * 60 * 1000
    if unit == "h": return value * 60 * 60 * 1000
    raise ValueError(f"invalid unit: {unit}")
'''.lstrip()

TEST = r'''
import unittest
from calc import parse_duration

class TestDuration(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("250ms"), 250)
        self.assertEqual(parse_duration("2s"), 2000)
        self.assertEqual(parse_duration("3m"), 180000)
        self.assertEqual(parse_duration("1h"), 3600000)

    def test_compound_units_with_and_without_spaces(self):
        self.assertEqual(parse_duration("1h 30m"), 5400000)
        self.assertEqual(parse_duration("2m10s"), 130000)
        self.assertEqual(parse_duration("1h 2m 3s 4ms"), 3723004)

    def test_rejects_empty_unknown_or_junk(self):
        for value in ["", "abc", "1d", "1h nope", "s", "1.5s"]:
            with self.assertRaises(ValueError):
                parse_duration(value)

if __name__ == "__main__":
    unittest.main()
'''.lstrip()

def copy_auth(home: Path) -> None:
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True)
    for name in ["oauth_creds.json", "google_accounts.json", "installation_id"]:
        src = Path.home() / ".gemini" / name
        if src.exists():
            shutil.copy(src, gemini_dir / name)


def settings_for_budget(alias: str, budget: int) -> dict:
    if budget == 0:
        # Do not inherit chat-base, because it sets includeThoughts=true and
        # Gemini rejects includeThoughts when thinkingBudget is 0.
        alias_body = {
            "extends": "base",
            "modelConfig": {
                "model": MODEL_25,
                "generateContentConfig": {
                    "temperature": 0,
                    "topP": 1,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            },
        }
    else:
        alias_body = {
            "extends": MODEL_25,
            "modelConfig": {
                "generateContentConfig": {
                    "temperature": 0,
                    "topP": 1,
                    "thinkingConfig": {
                        "thinkingBudget": budget,
                        "includeThoughts": True,
                    },
                },
            },
        }
    return {
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "modelConfigs": {"customAliases": {alias: alias_body}},
    }


def settings_for_level(alias: str, level: str) -> dict:
    return {
        "security": {"auth": {"selectedType": "oauth-personal"}},
        "modelConfigs": {"customAliases": {alias: {
            "extends": MODEL_3,
            "modelConfig": {"generateContentConfig": {
                "temperature": 0,
                "topP": 1,
                "thinkingConfig": {"thinkingLevel": level, "includeThoughts": True},
            }},
        }}},
    }


def run_case(label: str, settings: dict, alias: str) -> dict:
    case = ROOT / label
    home = case / "home"
    work = case / "work"
    copy_auth(home)
    work.mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text(json.dumps(settings, indent=2))
    (work / "calc.py").write_text(CALC)
    (work / "test_calc.py").write_text(TEST)
    (work / "README.md").write_text("Run `python -m unittest -q`. Fix only calc.py.\n")

    out = case / "stream.jsonl"
    err = case / "stderr.txt"
    env = os.environ.copy()
    env["HOME"] = str(home)
    cmd = [
        "gemini", "--model", alias, "--output-format", "stream-json",
        "--approval-mode", "yolo", "-p", PROMPT,
    ]
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=work, env=env, text=True,
                          stdout=out.open("w"), stderr=err.open("w"), timeout=240)
    wall = time.perf_counter() - start
    check = subprocess.run(["python", "-m", "unittest", "-q"], cwd=work,
                           text=True, capture_output=True, timeout=60)

    tools, stats, assistant_chars, thought_chars = [], {}, 0, 0
    for line in out.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "tool_use":
            tools.append(ev.get("tool_name"))
        if ev.get("type") == "message" and ev.get("role") == "assistant":
            content = ev.get("content") or ""
            assistant_chars += len(content)
            if ev.get("thought") or ev.get("metadata", {}).get("thought"):
                thought_chars += len(content)
        if ev.get("type") == "result":
            stats = ev.get("stats") or {}

    return {
        "label": label,
        "returncode": proc.returncode,
        "wall_seconds": round(wall, 2),
        "cli_duration_ms": stats.get("duration_ms"),
        "tool_calls": len(tools),
        "distinct_tools": sorted(set(tools)),
        "success": check.returncode == 0,
        "assistant_chars": assistant_chars,
        "thought_chars_visible": thought_chars,
        "stderr_tail": err.read_text(errors="replace")[-800:],
    }

results = []
for budget in BUDGETS:
    alias = f"effort-test-{budget}"
    results.append(run_case(f"budget-{budget}", settings_for_budget(alias, budget), alias))
for level in LEVELS:
    alias = f"effort-test-{level.lower()}"
    results.append(run_case(f"level-{level.lower()}", settings_for_level(alias, level), alias))
print(json.dumps(results, indent=2))
```

## Measurements

Canonical task: fix `parse_duration()` so it supports compound durations (`"1h 30m"`, `"2m10s"`, `"1h 2m 3s 4ms"`) while rejecting invalid text. Success means an independent post-run `python -m unittest -q` or equivalent `pytest` check passed in the worktree.

Raw run directories were under:

- `/tmp/gemini-effort-2zxca9od` for the main 2.5/3 matrix.
- `/tmp/gemini-budget0-base-4vye32_b` for the budget-0 special-case confirmation.

### Gemini 2.5-style `thinkingBudget` (`gemini-2.5-flash`)

| thinkingBudget | Alias inheritance tested | API result | Latency wall / CLI | Tool calls | Distinct tools | Task success | Visible thought chars | Assistant chars | Notes |
|---:|---|---|---:|---:|---|---|---:|---:|---|
| 0 | `extends: gemini-2.5-flash` | **400 invalid** | 4.73s / 0ms | 0 | none | no | 0 | 0 | Failed because parent alias inherits `includeThoughts: true`: `include_thoughts is only enabled when thinking is enabled`. |
| 0 | `extends: base`, `model: gemini-2.5-flash` | success | 65.28s / 63457ms | 12 | `read_file`, `replace`, `run_shell_command` | yes | 0 | 2289 | Confirms budget 0 is viable only as a base/no-includeThoughts alias. |
| 256 | `extends: gemini-2.5-flash` | success | 70.47s / 69914ms | 13 | `read_file`, `replace`, `run_shell_command` | yes | 0 | 2850 | Solved but showed recovery/noise around test runner setup. |
| 1024 | `extends: gemini-2.5-flash` | success | 39.07s / 37020ms | 10 | `read_file`, `replace`, `run_shell_command` | yes | 0 | 1768 | Clean medium candidate. |
| 4096 | `extends: gemini-2.5-flash` | success | 80.24s / 80562ms | 16 | `list_background_processes`, `read_file`, `replace`, `run_shell_command` | yes | 0 | 2437 | Deeper/more exploratory; one shell approval warning appeared despite yolo, but final patch passed. |
| 8192 | `extends: gemini-2.5-flash` | success | 46.34s / 44448ms | 9 | `read_file`, `replace`, `run_shell_command` | yes | 0 | 1611 | Matches Gemini CLI 2.5 default thinking budget. |
| 16384 | `extends: gemini-2.5-flash` | success | 55.12s / 52775ms | 14 | `read_file`, `replace`, `run_shell_command` | yes | 0 | 3347 | Worked, but extra failed replace attempts and no clear quality win over 8192. |

### Gemini 3-style `thinkingLevel` (`gemini-3-flash-preview`)

| thinkingLevel | API result | Latency wall / CLI | Tool calls | Distinct tools | Task success | Visible thought chars | Assistant chars | Notes |
|---|---|---:|---:|---|---|---:|---:|---|
| LOW | success | 29.29s / 27095ms | 13 | `list_directory`, `read_file`, `replace`, `run_shell_command`, `write_file` | yes | 0 | 1868 | Coarse low tier works. |
| MEDIUM | success | 22.07s / 20729ms | 9 | `read_file`, `replace`, `run_shell_command`, `write_file` | yes | 0 | 921 | Fastest and concise in this run. |
| HIGH | success | 59.02s / 59359ms | 12 | `replace`, `run_shell_command`, `write_file` | yes | 0 | 1333 | Matches Gemini CLI 3 default. |

## Interpretation

1. **`customAliases` work in headless mode.** The stream `result.stats.models` field reported the concrete backing model (`gemini-2.5-flash` or `gemini-3-flash-preview`) while `init.model` reported the custom alias, confirming alias resolution happened.
2. **Budget 0 is the main imperfection.** Gemini CLI's public 2.5 aliases inherit `chat-base`, and `chat-base` includes `thinkingConfig.includeThoughts: true`. If Option B simply extends `gemini-2.5-flash` and overwrites `thinkingBudget` to 0, the API rejects the request. The adapter must generate a separate alias that extends `base`, sets `model` to the concrete Gemini 2.5 model, and sets only `thinkingBudget: 0`.
3. **Visible chain-of-thought is not available in stream-json.** Even with `includeThoughts: true`, no stream events were marked as thought content and `thought_chars_visible` was 0 for every successful run. The qualitative proxy available to us is assistant text length plus behavior/tool depth, not true hidden reasoning length.
4. **Latency is noisy.** Several runs hit transient quota retry messages. Do not overfit exact latency ordering. The mapping should lean on documented built-ins plus observed pass/fail and behavior.
5. **8192 is the defensible `xhigh`, not 16384.** 16384 is accepted by the API in this environment, but it did not improve task success and produced more verbose/retry-prone behavior. Because Gemini CLI's own `chat-base-2.5` default is 8192, using 8192 for Codex `xhigh` is easier to defend.
6. **Gemini 3 has only three effort buckets.** `minimal` and `xhigh` are necessarily lossy unless future Gemini 3 config exposes more levels or a no-thinking/base mode for the selected model.

## Suggested adapter alias shapes

### 2.5 minimal / no thinking

```json
{
  "modelConfigs": {
    "customAliases": {
      "claude-anyteam-effort-minimal": {
        "extends": "base",
        "modelConfig": {
          "model": "gemini-2.5-flash",
          "generateContentConfig": {
            "thinkingConfig": { "thinkingBudget": 0 }
          }
        }
      }
    }
  }
}
```

### 2.5 low through xhigh

```json
{
  "modelConfigs": {
    "customAliases": {
      "claude-anyteam-effort-high": {
        "extends": "gemini-2.5-flash",
        "modelConfig": {
          "generateContentConfig": {
            "thinkingConfig": { "thinkingBudget": 4096 }
          }
        }
      }
    }
  }
}
```

### 3-style models

```json
{
  "modelConfigs": {
    "customAliases": {
      "claude-anyteam-effort-medium": {
        "extends": "gemini-3-flash-preview",
        "modelConfig": {
          "generateContentConfig": {
            "thinkingConfig": { "thinkingLevel": "MEDIUM" }
          }
        }
      }
    }
  }
}
```

## Implementation guardrails

- Do not expose this as exact parity with Codex. Document it as a best-effort generation-config mapping.
- Generate aliases only in adapter-owned isolated settings, not in the user's real `~/.gemini/settings.json`.
- Detect Gemini 3 by model-family string (`gemini-3`) and use `thinkingLevel`; otherwise use 2.5-style numeric budgets.
- Special-case 2.5 `minimal` to avoid inherited `includeThoughts: true`.
- If a user supplies a custom model alias rather than a raw Gemini model, minimal may not be safely rewriteable to `extends: base` unless the adapter can recover the concrete model. In that case either:
  - extend the supplied alias and use `low` semantics instead of budget 0, or
  - document that `minimal` requires a concrete 2.5 model name for true no-thinking behavior.
