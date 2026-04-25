# GPT Images prompt: README architecture diagram

Source prompt that produced the current `assets/diagrams/architecture.png` (the hero visual on the README). Keep this in sync with the README's supported-backends and "Coming next" lists — when a backend graduates from "next" to "shipping today", update this prompt accordingly and re-render.

## Workflow when adding a new backend

1. Update the README's supported-backends / "Coming next" sections to move the backend out of "next"
2. Edit the prompt below: change the matching `<backend> · next` chip to `<backend> · shipping today` (emerald-green, with OpenAI-style starburst replaced by the new backend's recognizable mark)
3. Regenerate via GPT Images using the updated prompt
4. Save output to `assets/diagrams/architecture.png` (same filename — the README references it)
5. Commit the new PNG + the updated prompt in the same commit so they don't drift

## Current prompt (used 2026-04-23 to generate the v0.2.0 diagram)

> A cinematic, ultra-modern software architecture diagram rendered as a wide landscape illustration (16:9 aspect ratio). Dark premium background: a deep charcoal-to-midnight gradient (from `#0b1220` in the top-left to `#1e293b` in the bottom-right), with a subtle starfield of tiny blue-white particles and a faint dot-grid overlay for a technical feel. Soft volumetric glow emanates from the center.
>
> The diagram shows a vertical data flow with four layered nodes connected by glowing flow arrows, and a fifth layer branching horizontally at the bottom.
>
> **Top layer (level 1):** a soft-glowing rounded hexagonal plate labeled **"Claude Code · leader session"** in bold pearl-white Inter/SF Pro font. The plate has a cool silver-steel finish with a subtle inner glow. A small orange/amber dot pulses on its edge indicating an active session.
>
> **Level 2 (below, connected by a downward glowing cyan flow line):** a rounded pill-shaped node labeled **"spawn shim"** in a hot-pink-to-violet gradient (`#f472b6` → `#a78bfa`). Subtle diamond pattern on the surface. Off to the right, a branching side-arrow leads to a faded yellow/amber node labeled **"native claude"** (semi-transparent, greyed to show it's the bypass route, not the hero path).
>
> **Level 3 (below the shim, connected by a bright cyan-to-blue gradient flow line):** a large, centered, prominent node — the hero element — styled as a premium card with rounded corners, a subtle inner gradient from cyan (`#22d3ee`) through blue (`#60a5fa`) to violet (`#a78bfa`), with a thin pearl-white border glow. Label inside in bold pearl-white type: **"claude-anyteam"** with smaller italic subtitle beneath: **"protocol adapter"**. A small orbiting-nodes mark (three small circles around a central dot) sits in the top-left corner of the card.
>
> **Level 4 (below the adapter):** two solid emerald-green (`#10a37f`) rounded rectangles side-by-side labeled **"codex · shipping today"** and **"gemini · shipping today"**. Give Codex an OpenAI-style subtle starburst icon and Gemini a simple sparkle / twin-star mark. A thin pearl-white checkmark badge sits in each corner. Connect Codex to the adapter by a bright green glowing bidirectional arrow labeled in small type `JSON-RPC`; connect Gemini to the adapter by a bright green glowing arrow labeled `stream-json`.
>
> **Level 5 (bottom row, branching horizontally to the right of the shipping backends):** three smaller rounded rectangles with dashed borders to signal "coming next". Order them left-to-right by priority. The leftmost — **"kimi · next up"** — is the most prominent of the three: render it in a slightly warmer, lighter grey (`#94a3b8`) with a slightly bolder dashed border and a thin pearl-white "1" badge in the corner indicating it's the priority next adapter. The other two — **"glm · next"** and **"deepseek · next"** — stay in the muted pale grey (`#64748b`) with thinner dashed borders. Each has a subtle adapter icon. Connect all three to the spawn shim via dashed grey routing lines (lighter, greyed out to visually de-emphasize vs the solid green shipping paths).
>
> **Side annotations (small, subtle):** on the right edge, a vertical label "Agent Teams native UX" in small pearl-white type. On the left edge, "Any LLM. One protocol." in the same style.
>
> **Lighting and depth:** gentle rim lighting around the hero adapter card, soft drop shadows under each node, slight chromatic aberration on glow edges, volumetric light haze between levels. High contrast, ultra-clean, Vercel/Linear/Stripe aesthetic. No stock iconography. No photorealism. No text outside the labels described. No official logos or brand marks; use only abstract starburst/sparkle motifs. Precise typography. Premium, marketing-grade.
