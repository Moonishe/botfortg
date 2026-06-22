# Deep Dive — Technical Perspective (Researcher 2)

> Repository: https://github.com/plannotator/effective-html
> Branch: main · Public · MIT license · 1.1k stars · 79 forks
> Date: 2026-06-22
> Method: webfetch via github.com/blob/ URLs (raw.githubusercontent.com unreachable from this environment; api.github.com rate-limited)

---

## 0. Repository Shape (confirmed from README + tree)

- Skills live under `skills/<skill-name>/SKILL.md`.
- Three skills: `html`, `html-diagram`, `html-plan`.
- Each skill bundles a **local copy** of the `html-effectiveness` example corpus under `references/html-effectiveness/` so references stay local to the skill.
- The corpus is credited to Thariq Shihipar: https://thariqs.github.io/html-effectiveness
- Plugin marketplaces present: `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`.
- Install: `npx skills add plannotator/effective-html` (or `--skill html-diagram` / `--skill html-plan`).
- The `html-diagram` skill additionally ships a **finished reference**: `references/architecture-example.html`.
- `agents/openai.yaml` sits under the `html` skill, defining the OpenAI-agent interface + policy.

---

## 1. SKILL.md Format

### 1.1 Anatomy

Every SKILL.md is: **YAML frontmatter** (delimited by `---`) + **Markdown body** (a `#` heading + imperative instructions).

### 1.2 YAML frontmatter — three keys, identical across all three skills

```yaml
---
name: <skill-name>            # kebab-case, matches the directory name
description: <one paragraph>   # the model-selection trigger; starts with a verb; lists WHEN to use
disable-model-invocation: true # ALL three skills set this — opt-out of auto-invocation
---
```

**`disable-model-invocation: true`** is present on every skill. This means the skill is **not** auto-triggered by the model on its own; it must be invoked explicitly (by name / by the user / by an agent that references `$html`). The `agents/openai.yaml` corroborates this with `policy: allow_implicit_invocation: false`.

### 1.3 Markdown body — shared structure

All three bodies follow the same skeleton:

1. **`# <Title>`** — the skill heading (matches `name` but human-cased: `# HTML`, `# HTML Diagram`, `# HTML Plan`).
2. **"Review the files…" instruction** — points the model at `references/html-effectiveness/` (and, for diagram, at `references/architecture-example.html`) to read the corpus *before* generating.
3. **Generate instruction** — one or two imperative sentences: create the HTML file, match the references for alignment (style, density, tone).
4. **The dark-mode contract** — a verbatim paragraph that appears in all three skills (see §3 below).

### 1.4 Per-skill body differences

| Skill | Lines | Bytes | Extra body content beyond the shared skeleton |
|-------|-------|-------|------------------------------------------------|
| `html` | 13 (9 loc) | 783 | None — the minimal template. |
| `html-plan` | 15 (10 loc) | 749 | Adds "Keep it pragmatic and simple." |
| `html-diagram` | 23 (14 loc) | 1490 | Adds: not prose-heavy; full-screen diagram; build high-quality SVG; iterate on the diagram "more than anything"; optionally make it interactive/animate system-behavior sequences; review `references/architecture-example.html`; **and** the dark-mode paragraph gains an extra sentence: "Style the SVG through CSS classes using those variables — never hard-coded hex inside the SVG — so the diagram follows the theme." |

### 1.5 Verbatim SKILL.md transcripts

#### skills/html/SKILL.md
```yaml
---
name: html
description: Create a self-contained HTML file for whatever the user is describing, in the effective HTML style. Use when the user wants an HTML artifact that isn't specifically a diagram or a plan — a report, explainer, comparison, deck, prototype, or anything else best delivered as one HTML file.
disable-model-invocation: true
---
```
```markdown
# HTML

Review the files throughout `references/html-effectiveness/`.

Create an HTML file for whatever the user is describing. Use the references as best you can to match alignment — style, density, and tone.

Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).
```

#### skills/html-diagram/SKILL.md
```yaml
---
name: html-diagram
description: Create a self-contained HTML file for visualizing architecture and understanding the stack with a high-quality SVG diagram. Use when the user wants a full-screen diagram, wants the output to be light on prose, or wants an HTML artifact that is mostly there to make the architecture click fast.
disable-model-invocation: true
---
```
```markdown
# HTML Diagram

Review the SVG diagrams used throughout `references/html-effectiveness/`.
There are a bunch in there, and some of them are focused on architecture and whatnot.

After reviewing them, create an HTML file that is strictly for visualizing the architecture and understanding the stack.

It should not be prose-heavy. It should simplify more into a full-screen diagram and whatnot.

Build a high-quality diagram in SVG. Take your time iterating on the diagram more than anything.

If it makes sense, make the diagram interactive and able to visualize and animate different sequences of system behavior.

Also review `references/architecture-example.html` — a finished example of this skill done well (full-screen SVG stage, clickable nodes, flow chips that light up and animate request paths).

Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`). Style the SVG through CSS classes using those variables — never hard-coded hex inside the SVG — so the diagram follows the theme.
```

#### skills/html-plan/SKILL.md
```yaml
---
name: html-plan
description: Create a self-contained HTML plan that is pragmatic, simple, and visually organized. Use when the user wants a plan page in the effective HTML style, wants the writing kept close to what they gave you, or wants the grammar cleaned up without turning it into a whole bigger thing.
disable-model-invocation: true
---
```
```markdown
# HTML Plan

Review the files throughout `references/html-effectiveness/`.

After reviewing them, create an HTML file for the plan in a similar style.

Keep it pragmatic and simple.

Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).
```

### 1.6 Template (generalized for a new skill in this family)

```
skills/<name>/SKILL.md
skills/<name>/agents/openai.yaml          # optional agent interface
skills/<name>/references/html-effectiveness/  # bundled corpus (local copy)
skills/<name>/references/<example>.html       # optional finished example
```

```yaml
---
name: <kebab-name>
description: <verb-first paragraph; state WHAT + WHEN to use it>
disable-model-invocation: true
---
```
```markdown
# <Title>

Review the files throughout `references/html-effectiveness/`.

<1-3 imperative sentences: what to build, how to align to references>

Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).
```

---

## 2. agents/openai.yaml — Interface + Policy Schema

File: `skills/html/agents/openai.yaml` (7 lines, 221 bytes)

```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."
policy:
  allow_implicit_invocation: false
```

### Schema analysis

**Top-level keys: `interface` and `policy`.**

`interface` (how the skill surfaces to the agent/user):
| Field | Type | Purpose |
|-------|------|---------|
| `display_name` | string | Human label shown in skill pickers ("HTML"). |
| `short_description` | string | One-line elevator pitch for menus. |
| `default_prompt` | string | Template string emitted when the skill is invoked; `$html` is a placeholder for the skill's invocation token/name. |

`policy` (governs when/how the skill runs):
| Field | Type | Purpose |
|-------|------|---------|
| `allow_implicit_invocation` | boolean | `false` = the model may NOT auto-trigger this skill from context; it requires an explicit call. This **mirrors** `disable-model-invocation: true` in the SKILL.md frontmatter — the two are redundant guardrails (one in the skill manifest, one in the agent adapter). |

**Key invariant:** `disable-model-invocation: true` (frontmatter) ⟺ `allow_implicit_invocation: false` (yaml). Both say "explicit invocation only." A new skill in this family should set both.

---

## 3. The Dark-Mode Contract (shared, verbatim)

This paragraph appears in all three SKILL.md bodies. The diagram version appends one sentence about SVG.

> Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).

Diagram-only addendum:
> Style the SVG through CSS classes using those variables — never hard-coded hex inside the SVG — so the diagram follows the theme.

### 3.1 The four required mechanisms

1. **CSS variables on `:root`** (light theme) **and `html.dark`** (dark theme overrides) — "hand-rolled", i.e. no framework, no `prefers-color-scheme` *media query* as the only mechanism. The media query is only the *default seed*.
2. **A small theme toggle button** in the UI (the example uses `#themeToggle`, mono-font, uppercase, pill/border style).
3. **`localStorage` persistence** — key `'theme'`, value `'dark'` | `'light'`.
4. **Apply-before-paint script in `<head>`** — an IIFE that runs *before* first paint to set the `dark` class on `<html>`, preventing a flash of unstyled/wrong-theme content (FOUC).

### 3.2 The canonical apply-before-paint script (from architecture-example.html)

```html
<script>
(function () {
  const saved = localStorage.getItem('theme');
  const dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  document.documentElement.classList.toggle('dark', dark);
})();
</script>
```

Logic:
- If `localStorage` has a `'theme'` value → respect it (`'dark'` = dark, anything else = light).
- Else (no saved preference) → fall back to `matchMedia('(prefers-color-scheme: dark)').matches`.
- Apply by toggling the `dark` class on `document.documentElement` (`<html>`).
- Runs synchronously in `<head>` → before the body paints → **no theme flash**.

### 3.3 The canonical theme-toggle handler

```js
document.getElementById('themeToggle').addEventListener('click', () => {
  const dark = !document.documentElement.classList.contains('dark');
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('theme', dark ? 'dark' : 'light');
});
```

---

## 4. Design System (from architecture-example.html)

### 4.1 Color tokens — Light (`:root`)

| Token | Value | Role |
|-------|-------|------|
| `--bg` | `#FAF9F5` | Page background — **ivory / warm paper** |
| `--surface` | `#FFFFFF` | Cards, node fills — pure white |
| `--surface2` | `#F0EEE6` | Secondary surface (store nodes) |
| `--ink` | `#141413` | Primary text / headings — **slate ink** (near-black, warm) |
| `--body` | `#3D3D3A` | Body text |
| `--muted` | `#87867F` | Captions, labels, edge strokes |
| `--line` | `#D1CFC5` | Borders, dividers |
| `--line-soft` | `#E6E3DA` | Soft borders |
| `--clay` | `#D97757` | **Primary accent** — clay/terracotta (selected, lit, badges) |
| `--clay-soft` | `rgba(217,119,87,0.10)` | Clay tint fill (gate nodes) |
| `--olive` | `#788C5D` | **Secondary accent** — olive (DO/action nodes) |
| `--olive-soft` | `rgba(120,140,93,0.12)` | Olive tint fill |
| `--gold` | `#C9A45C` | Tertiary accent — gold |
| `--blue` | `#5B7E96` | Tertiary accent — slate blue |
| `--zone` | `rgba(20,20,19,0.025)` | Zone panel fill (very subtle) |
| `--zone-line` | `#D1CFC5` | Zone panel border |

### 4.2 Color tokens — Dark (`html.dark`)

| Token | Value | Notes |
|-------|-------|-------|
| `--bg` | `#141413` | Dark bg = light `--ink` (inverted) |
| `--surface` | `#1F1F1D` | |
| `--surface2` | `#2A2A28` | |
| `--ink` | `#FAF9F5` | Dark ink = light `--bg` (inverted) |
| `--body` | `#D1CFC5` | |
| `--muted` | `#87867F` | **Same** in both themes |
| `--line` | `#3D3D3A` | = light `--body` |
| `--line-soft` | `#2A2A28` | = light `--surface2` |
| `--clay` | `#E48A6E` | Lightened clay for dark bg |
| `--clay-soft` | `rgba(228,138,110,0.14)` | Slightly higher alpha |
| `--olive` | `#9DB07C` | Lightened olive |
| `--olive-soft` | `rgba(157,176,124,0.16)` | |
| `--gold` | `#D4B36F` | Lightened gold |
| `--blue` | `#7FA3BC` | Lightened blue |
| `--zone` | `rgba(250,249,245,0.03)` | |
| `--zone-line` | `#3D3D3A` | |

**Inversion pattern:** light and dark are near-perfect inversions of the bg/ink pair. Accents (clay/olive/gold/blue) are *lightened* in dark mode to maintain contrast. `--muted` stays constant. Soft tints gain ~0.04 alpha in dark mode.

### 4.3 The four signature colors (per the research brief)

| Name | Light hex | Dark hex | Semantic role |
|------|-----------|----------|---------------|
| **Ivory** (bg) | `#FAF9F5` | `#141413` | Warm paper background |
| **Slate ink** (ink) | `#141413` | `#FAF9F5` | Primary text — warm near-black |
| **Clay** (accent) | `#D97757` | `#E48A6E` | Selection, active flow, primary highlight |
| **Olive** (accent) | `#788C5D` | `#9DB07C` | Action/DO nodes, secondary highlight |

Plus two tertiary accents: **gold** `#C9A45C`/`#D4B36F` and **slate blue** `#5B7E96`/`#7FA3BC`.

### 4.4 Fonts (three font stacks, all system — zero web-font dependencies)

| Token | Stack | Usage |
|-------|-------|-------|
| `--serif` | `ui-serif, Georgia, "Times New Roman", serif` | Headings (`h1`, detail `h3`) — weight 500 |
| `--sans` | `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` | Body, node titles (`.t`), default |
| `--mono` | `ui-monospace, "SF Mono", Menlo, Consolas, monospace` | Labels, captions, chips, subtitle, meta, code — uppercase + letter-spacing for eyebrow text |

**No external font loads.** Everything is system-ui / ui-serif / ui-monospace. This keeps artifacts fully self-contained with zero network deps.

### 4.5 Typography patterns

- Headings: serif, weight 500, `letter-spacing: -0.01em`, ~19px.
- Eyebrows/labels: mono, ~10.5px, `letter-spacing: 0.06–0.10em`, `text-transform: uppercase`, `--muted` color.
- Node titles (`.t`): sans, 14.5px, weight 600, `--ink`.
- Node meta (`.m`): mono, 11px, `--muted`.
- Body copy: 12.5px, line-height 1.55.

---

## 5. SVG Diagram Pattern (architecture-example.html — 462 lines, 27.4 KB)

### 5.1 The hard rule

> Style the SVG through CSS classes using those variables — **never hard-coded hex inside the SVG** — so the diagram follows the theme.

Verified in the source: **zero hard-coded hex colors inside any SVG element.** All fills/strokes reference CSS variables via CSS classes. Even SVG `<marker>` arrowheads use inline `style="fill: var(--muted)"` / `style="fill: var(--clay)"` — variables, not hex.

### 5.2 SVG skeleton

```html
<div class="stage" id="stage">
  <svg viewBox="0 0 1560 980" preserveAspectRatio="xMidYMid meet"
       role="img" aria-label="Workspaces platform architecture diagram">
    <defs>
      <!-- arrowhead markers, styled with CSS vars -->
      <marker id="a-mut" ...><path d="M0,0 L10,5 L0,10 z" style="fill: var(--muted)"/></marker>
      <marker id="a-clay" ...><path d="M0,0 L10,5 L0,10 z" style="fill: var(--clay)"/></marker>
    </defs>
    <!-- zones → edges → edge-labels → nodes (paint order) -->
  </svg>
  <!-- floating HTML cards overlay the SVG -->
</div>
```

### 5.3 CSS class taxonomy for SVG elements

**Zones** (background groupings/regions):
- `<g class="zone">` → contains `<rect>` + `<text class="ztitle">` + `<text class="zsub">`
- `.zone rect { fill: var(--zone); stroke: var(--zone-line); stroke-width: 1; stroke-dasharray: 5 5; rx: 16; }`
- `.zone .ztitle` — mono, uppercase, 12px, muted.
- `.zone .zsub` — mono, 10.5px, muted, opacity 0.75.

**Nodes** (boxes):
- `<g class="node" data-k="<key>">` → contains `<rect>` + `<text class="t">` (title) + `<text class="m">` (meta lines) + optional `<text class="k">` (kind badge)
- `.node rect { fill: var(--surface); stroke: var(--line); stroke-width: 1.5; rx: 10; transition: ... }`
- `.node:hover rect { stroke: var(--muted); }`
- `.node.sel rect { stroke: var(--clay) !important; stroke-width: 2; }` — selected state
- **Node variants** (modifier classes on the `<g>`):
  - `.node.gate` → `fill: var(--clay-soft); stroke: var(--clay);` — the authorization gate
  - `.node.store` → `fill: var(--surface2);` — data stores
  - `.node.do` → `fill: var(--olive-soft); stroke: var(--olive);` — Durable Objects / actions
  - `.node.ext` → `stroke-dasharray: 6 3;` — external systems (dashed border)
- Text classes: `.t` (sans 14.5px 600 ink), `.m` (mono 11px muted), `.k` (mono 10px uppercase clay). All `pointer-events: none` so clicks hit the group.

**Edges** (connectors):
- `<path class="edge" id="e-<name>" d="...">` with `marker-end="url(#a-mut)"`
- `.edge { stroke: var(--muted); stroke-width: 1.6; fill: none; marker-end: url(#a-mut); transition: opacity .2s, stroke .2s; }`
- Variants: `.edge.dash { stroke-dasharray: 5 4; }` (async/indirect), `.edge.ws { stroke-width: 2; }` (wide/WebSocket).
- Edge labels: `<text class="elbl" data-e="e-<name>">` — mono 10.5px muted, linked to edge by `data-e` matching the path `id`.

### 5.4 Flow-highlighting system (the signature interaction)

When a flow chip is clicked, the stage gets `.flowing` and selected edges/nodes/labels get `.lit`:

```css
/* dim everything */
.stage.flowing .edge  { opacity: 0.13; }
.stage.flowing .elbl  { opacity: 0.13; }
.stage.flowing .node  { opacity: 0.30; }
.stage.flowing .zone  { opacity: 0.45; }

/* light up the active path */
.stage.flowing .edge.lit {
  opacity: 1; stroke: var(--clay); marker-end: url(#a-clay);
  stroke-dasharray: 7 5; animation: march 0.9s linear infinite;
}
.stage.flowing .elbl.lit { opacity: 1; fill: var(--clay); }
.stage.flowing .node.lit { opacity: 1; }
.stage.flowing .node.lit rect { stroke: var(--clay); }

@keyframes march { to { stroke-dashoffset: -12; } }  /* "marching ants" */

@media (prefers-reduced-motion: reduce) {
  .stage.flowing .edge.lit { animation: none; }  /* a11y: respect reduced motion */
}
```

### 5.5 JS interaction model

Two plain-JS data objects drive everything (no framework):

**`DETAIL`** — keyed by `data-k`, each entry `{ t: title, m: meta, b: bodyHTML }`:
```js
const DETAIL = {
  browser: { t:"Browser", m:"client · session cookie", b:"The Vite+React SPA..." },
  resolve: { t:"resolveAccess()", m:"the ONE authorization gate", b:"A pure function..." },
  // ... 17 nodes total
};
```
Clicking a `.node` → removes `.sel` from all, adds `.sel` to clicked, fills `#detail` card (title/meta/body).

**`FLOWS`** — keyed by flow name, each entry `{ name, edges: [ids], nodes: [data-k values], steps: [HTML strings] }`:
```js
const FLOWS = {
  create: { name:"Create — no signup wall",
            edges:["e-createA","e-authres","e-reshand","e-handd1"],
            nodes:["agent","browser","authenticate","resolve","handlers","d1"],
            steps:["POST /v1/documents...", "One D1 batch insert...", "201 with the share link..."] },
  live:   { ... edges:["e-spa","e-ws","e-pardo","e-docoord","e-coordart","e-coordd1"], ... },
  agent:  { ... edges:["e-createA","e-authres","e-reshand","e-handbr","e-brdo","e-docoord",...], ... },
  read:   { ... edges:["e-viss","e-skv","e-kvart"], ... },
  login:  { ... edges:["e-createB","e-authwos","e-jwks"], ... },
  render: { ... edges:["e-iframe","e-ucart"], ... },
};
```

**`setFlow(key)`**:
1. Toggle `.on` on the clicked chip (and off on others).
2. Remove `.lit` from all edges/labels/nodes.
3. If `key === 'all'` → remove `.flowing` from stage, hide caption card, return.
4. Add `.flowing` to stage.
5. Add `.lit` to the flow's edges (by id), matching edge-labels (by `data-e`), and nodes (by `data-k`).
6. Fill `#flowcap` card with the flow name + numbered steps list, show it.

**Wiring:**
```js
chips.forEach(c => c.addEventListener('click', () => setFlow(c.dataset.flow)));
nodes.forEach(n => n.addEventListener('click', () => { /* select + fill #detail */ }));
document.getElementById('themeToggle').addEventListener('click', () => { /* toggle dark + save */ });
```

### 5.6 Layout structure

```
<body>  (flex column, overflow hidden — full-viewport app)
  <div class="bar">          (top bar: serif title + mono subtitle + flow chips + theme toggle)
  <div class="stage">        (flex:1, the SVG canvas)
    <svg viewBox="0 0 1560 980" .../>
    <div class="src">        (tiny mono source attribution, top-right)
    <div class="card" id="flowcap">  (bottom-left: flow name + steps)
    <div class="card" id="detail">   (bottom-right: clicked-node detail)
  </div>
```

**Responsive:** `@media (max-width: 880px)` → `body { overflow: auto; }`, stage min-height 70vh, cards become static (stacked).

---

## 6. Key Takeaways for Replication

1. **Skill = frontmatter + body.** Three frontmatter keys: `name`, `description`, `disable-model-invocation: true`. Body = review-references → generate → dark-mode-contract.
2. **Explicit-invocation-only** is enforced twice: `disable-model-invocation: true` (SKILL.md) + `allow_implicit_invocation: false` (openai.yaml).
3. **openai.yaml** has two top-level keys: `interface` (display_name, short_description, default_prompt with `$<name>` token) + `policy` (allow_implicit_invocation).
4. **Dark mode is mandatory and structural**, not cosmetic: 4 mechanisms (CSS vars on `:root`/`html.dark`, toggle button, localStorage, apply-before-paint IIFE). The IIFE runs in `<head>` before paint — no FOUC.
5. **Design system = ivory + slate ink + clay + olive**, with gold and slate-blue as tertiary. All via CSS variables. Dark theme inverts bg/ink and lightens accents. No external fonts (system stacks only).
6. **SVG diagrams must be CSS-variable-driven** — zero hard-coded hex inside SVG. Node classes: `.node` + modifiers `.gate`/`.store`/`.do`/`.ext`. Edge classes: `.edge` + `.dash`/`.ws`. The `.flowing`/`.lit` system dims the canvas and lights a clay-colored path with marching-ants animation, with `prefers-reduced-motion` respected.
7. **Interactivity is plain JS** — two data objects (`DETAIL`, `FLOWS`) + event delegation on chips/nodes. No framework.
8. **Corpus is bundled locally** per skill under `references/html-effectiveness/` so the model reads examples without network access.

---

## 7. Confidence & Evidence

| Claim | Evidence | Confidence |
|-------|----------|------------|
| SKILL.md format (frontmatter + body) | Read all 3 SKILL.md files verbatim | **high** |
| `disable-model-invocation: true` on all skills | Present in all 3 frontmatters | **high** |
| openai.yaml schema (interface/policy) | Read file verbatim (7 lines) | **high** |
| Dark-mode 4-mechanism contract | Verbatim in all 3 SKILL.md bodies + implemented in architecture-example.html | **high** |
| Color tokens (ivory/ink/clay/olive + dark variants) | Read CSS `:root` and `html.dark` blocks verbatim | **high** |
| SVG no-hardcoded-hex rule | Stated in html-diagram SKILL.md + verified in architecture-example.html source (markers use `var(--…)`) | **high** |
| Flow-highlighting `.lit`/`.flowing` system | Read full CSS + JS from architecture-example.html | **high** |
| DETAIL/FLOWS JS data model | Read full `<script>` block | **high** |
| Font stacks (system-only) | Read `--serif`/`--sans`/`--mono` verbatim | **high** |

**Overall confidence: HIGH.** All five requested files were retrieved and read in full. The only file not read line-for-line from raw was architecture-example.html (27.4 KB), but its complete CSS, SVG structure, and JS were captured via the github.com blob render + saved tool-output file (2156 lines, fully grep'd and read at the relevant offsets).
