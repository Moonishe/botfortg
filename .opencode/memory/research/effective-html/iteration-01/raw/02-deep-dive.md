# Deep Dive: how the skill works

## SKILL.md format
All three skills share a consistent YAML front-matter:
```yaml
---
name: html|html-diagram|html-plan
description: <concise use case>
disable-model-invocation: true
---
```
`disable-model-invocation: true` tells the host that the skill is a prompt/instruction, not a callable tool. The body is a short markdown directive telling the model to review the bundled reference corpus and then emit a single HTML file.

### `html` skill body
- Review `references/html-effectiveness/`.
- Create an HTML file matching the user's description.
- Match alignment, style, density, and tone of the references.
- Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).

### `html-diagram` skill body
- Review SVG diagrams in the reference corpus and `references/architecture-example.html`.
- Output a strictly visual architecture/stack HTML file, light on prose, full-screen diagram.
- Build a high-quality SVG; iterate on the diagram.
- Make it interactive / animate different sequences if it makes sense.
- Always include dark mode with the same CSS-variable technique.
- Style SVG through CSS classes using those variables — never hard-coded hex inside SVG.

### `html-plan` skill body
- Review `references/html-effectiveness/`.
- Create a pragmatic, simple HTML plan.
- Keep writing close to the user's input; clean up grammar without expanding the scope.
- Always include dark mode.

## Reference corpus details
The corpus is `html-effectiveness` by Thariq Shihipar (https://thariqs.github.io/html-effectiveness). It contains 20 standalone HTML files, each a complete page with no build step or dependencies:

1. `01-exploration-code-approaches.html`
2. `02-exploration-visual-designs.html`
3. `03-code-review-pr.html`
4. `04-code-understanding.html`
5. `05-design-system.html`
6. `06-component-variants.html`
7. `07-prototype-animation.html`
8. `08-prototype-interaction.html`
9. `09-slide-deck.html`
10. `10-svg-illustrations.html`
11. `11-status-report.html`
12. `12-incident-report.html`
13. `13-flowchart-diagram.html`
14. `14-research-feature-explainer.html`
15. `15-research-concept-explainer.html`
16. `16-implementation-plan.html`
17. `17-pr-writeup.html`
18. `18-editor-triage-board.html`
19. `19-editor-feature-flags.html`
20. `20-editor-prompt-tuner.html`

Plus `index.html`, `README.md`, `LICENSE` (Apache-2.0), `SECURITY.md`, `CODE_OF_CONDUCT.md`.

## Common style patterns observed
### Palette (warm, low-contrast)
- `--ivory: #FAF9F5` (page background)
- `--slate: #141413` (primary ink)
- `--clay: #D97757` (accent, warnings, CTAs)
- `--oat: #E3DACC` (soft highlights)
- `--olive: #788C5D` (success, secondary accent)
- `--gray-100: #F0EEE6`, `--gray-300: #D1CFC5`, `--gray-500: #87867F`, `--gray-700: #3D3D3A` (neutrals)

### Typography
- `--serif: ui-serif, Georgia, 'Times New Roman', serif` for headings
- `--sans: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif` for body
- `--mono: ui-monospace, 'SF Mono', Menlo, Consolas, monospace` for labels/code

### Layout
- Single `.page` wrapper with `max-width: 860px–1120px`, centered.
- Generous whitespace (`padding: 56px 32px 120px`).
- Cards/boxes with `border: 1.5px solid var(--gray-300)`, `border-radius: 12px`, white background.
- Summary strips, milestone timelines, risk tables, code blocks, and inline SVG diagrams.

### Dark mode
Every reference has a dark-mode script in `<head>` and a theme toggle. The architecture example uses:
```html
<script>
  (function () {
    const saved = localStorage.getItem('theme');
    const dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.classList.toggle('dark', dark);
  })();
</script>
```
CSS variables are flipped under `html.dark`.

### Diagram conventions
- Inline SVG with `viewBox` and `preserveAspectRatio="xMidYMid meet"`.
- Nodes are `<g class="node">` with `<rect>` + text.
- Edges are `<path class="edge">` with `<marker>` arrowheads.
- Zones are dashed rectangles with titles.
- Interaction uses vanilla JS to toggle `.lit` classes and reveal detail cards.

## Agent metadata
Each `agents/openai.yaml` is minimal:
```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."

policy:
  allow_implicit_invocation: false
```

## Plugin manifests
- `.claude-plugin/plugin.json` lists the three skill paths.
- `.claude-plugin/marketplace.json` is the marketplace listing with keywords, category, license.
- `.codex-plugin/plugin.json` adds an `interface` block with `displayName`, `shortDescription`, `capabilities: ["Write"]`, and `defaultPrompt`.
- `.agents/plugins/marketplace.json` is the agents plugin listing.

## Tools used
- `webfetch` on the three `SKILL.md` files and the plugin JSON files.
- `webfetch` on the `architecture-example.html`, `index.html`, `16-implementation-plan.html`, and `11-status-report.html` reference files.
