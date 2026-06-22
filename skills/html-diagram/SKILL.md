---
name: html-diagram
description: >
  Generate SVG architecture diagrams as standalone .html files, sent via Telegram.
  Use when user asks for a diagram, architecture overview, flow chart, or visual
  explanation of system structure. Produces clean, dark-theme SVG embedded in HTML.
version: 1.0
category: visual
risk: low
---

# HTML Diagram Skill

Generate SVG architecture diagrams as standalone HTML files for Telegram delivery.

## When to use

- User asks "покажи архитектуру", "нарисуй диаграмму", "как это работает визуально"
- Need to explain system structure with boxes and arrows
- Flow charts, component diagrams, data flow visualizations

## Output format

Produce a single HTML file with embedded SVG:

```html
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body { margin:0; background:#1a1a2e; display:flex; justify-content:center; }
svg { font-family:system-ui,sans-serif; }
.box { fill:#16213e; stroke:#0f3460; stroke-width:2; rx:8; }
.label { fill:#e0e0e0; font-size:14px; text-anchor:middle; }
.arrow { stroke:#0f3460; stroke-width:2; fill:none; marker-end:url(#arrow); }
.title { fill:#00d4ff; font-size:18px; font-weight:bold; text-anchor:middle; }
</style></head><body>
<svg viewBox="0 0 800 600" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3"
  orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#0f3460"/></marker></defs>
<!-- boxes, arrows, labels here -->
</svg></body></html>
```

## Rules

1. Dark theme: background `#1a1a2e`, boxes `#16213e`, arrows `#0f3460`, text `#e0e0e0`
2. Max 10 boxes per diagram — keep it readable on mobile
3. Use `viewBox` for responsive scaling
4. Save as `.html` file, send via Telegram as document
5. Labels in Russian (project language)
6. No external dependencies — pure SVG + HTML
7. Arrows via `<path class="arrow" d="M x1,y1 L x2,y2"/>`
8. Boxes via `<rect class="box" x="..." y="..." width="..." height="..."/>`
9. Labels via `<text class="label" x="..." y="...">text</text>`

## Example: System Architecture

```
[User] → [Telegram Bot] → [Maestro Router]
                                ↓
                    [LLM Provider] ← [Memory]
                                ↓
                    [Tool Registry] → [Sandbox]
```

Generate the SVG programmatically based on this structure.
