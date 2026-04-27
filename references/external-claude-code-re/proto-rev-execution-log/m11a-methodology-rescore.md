# M11a methodology tightening rescore

**Owner:** codex-eval-throughput  
**Worktree:** `/tmp/proto-rev-m11a-methodology`  
**Branch:** `proto-rev/impl/m11a-methodology-tightening`

## Scorer changes validated

`tools/stress/score_collab.py` now keeps the historical M11a percentile method (stdlib exclusive p95 across matched RTT samples) but makes the sample basis explicit:

- `samples_used_for_M11a` — matched peer-DM RTT samples used in percentile calculations; unmatched sends are not injected as cap values.
- `M11a_p50` — team/agent p50 over matched RTT samples.
- `M11a_max` — observed maximum matched RTT sample.
- M11a p95 is clamped to the observed max so undersampled pair buckets cannot report p95 greater than max.
- `M11a_peer_dm_rtt_seconds_by_semantic.classifier_method = "prefix_v1"` and prefix buckets expose why old W7 runs remain `other`-dominated.
- `M11a_classification_coverage` is machine-readable; all rescored old runs have coverage `0.0` because their peer-DMs lacked `ask:` / `answer:` / `handoff:` / `fyi:` prefix discipline.
- `M11a_coupling_compliance.violations[]` uses structured `{turn_id, sender, recipient, semantic, reason, event_id}` records. Canonical scorer intent is `coupling.intent: tight_peer_loop | loose_parallel | batched_async`; scorer-only aliases `tight`/`loose` normalize for legacy fixtures.

## Rescore inputs

Rescored from archived event logs under `/home/rosado/Projects/codex-teammate/references/external-claude-code-re/proto-rev-execution-log/runs/` with enhanced outputs written to `/tmp/m11a-rescore/`.

| run | samples_used_for_M11a | M11a_p50 | M11a_p95 | M11a_max | classification_coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| `S6-W7-20260427T1755Z-post17` | 162 | 65.946 | 155.204 | 310.336 | 0.0 |
| `S6-W7-20260427T1840Z-post18` | 40 | 117.846 | 562.655 | 586.129 | 0.0 |
| `S4-W7-20260427T1751Z-postrestore` | 59 | 38.906 | 211.604 | 273.773 | 0.0 |

## Outlier sensitivity check

| run | trim top 1 p95 | trim top 2 p95 | trim top 5 p95 | trim top 10 p95 | trim top 16 p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| post-#17 S6/W7 | 154.863 | 153.989 | 150.566 | 145.635 | 143.661 |
| post-#18 S6/W7 | 502.017 | 487.286 | 391.849 | 348.022 | 151.122 |
| S4 kimi/W7 | 155.111 | 151.222 | 118.119 | 98.383 | 77.065 |

**Conclusion:** the S4 kimi p95 behaves like a 1–2 outlier artifact: removing the top two samples drops p95 from 211.604s to 151.222s. The post-#18 S6/W7 p95 does **not** reduce to ~150s after removing 1–2 samples; it requires trimming the top 16 of 40 samples. So post-#18's 562.655s is a small-sample/tail-sensitivity signal, but not a 1–2 outlier artifact. The high value is driven by a broad directional tail in `codex-pair-b -> codex-pair-a`, whose matched RTT distribution has p50 359.742s, p95/max 586.129s, and 6 unmatched sends.
