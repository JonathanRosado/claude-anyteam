# claude-anyteam bug report

> Copy this template, fill in the fields, paste into your issue/PR. The
> CLI commands at the bottom collect the diagnostic context the
> maintainers will ask for first; running them up front saves a round
> trip.

## Summary

_What did you observe?_ One sentence.

## Severity

- [ ] LOW — cosmetic / friction, work continued
- [ ] MEDIUM — workaround possible, productivity impacted
- [ ] HIGH — blocked work / data loss / install-broken

## Environment

- claude-anyteam version (output of `claude-anyteam --version` or `pip show claude-anyteam`):
- OS / shell:
- Routed backend(s) involved (codex / gemini / kimi):
- Backend CLI version (e.g. `codex --version`, `gemini --version`):

## Reproduction

_What did you do?_ Numbered steps. Include the team name, agent names, and the prompts/messages you sent.

1.
2.
3.

## Expected vs actual

**Expected:** _what did you think would happen_

**Actual:** _what actually happened_

## Diagnostic surfaces

Paste the output of these commands (omit any you don't have access to or that don't apply):

### Team roster (resolved)

```
$ claude-anyteam team-roster --team <T>
<paste here>
```

The resolved view shows host model + adapter overrides side by side. Useful when "the teammate seems to be running at the wrong model" symptoms are involved.

### Resolved spawn-time config for the affected teammate(s)

```
$ claude-anyteam team-config <agent> --team <T>
<paste here>
```

### Recent incidents

```
$ claude-anyteam diagnose --team <T> --limit 20
<paste here>
```

### Specific incident detail (if a fallback message named one)

```
$ claude-anyteam diagnose --incident inc-XXXXXXXX
<paste here>
```

### Wrapper stderr (last 50 lines, redact paths/PII as needed)

```
<paste here>
```

## Anything else?

_Logs, screenshots, hypotheses, related issue links._
