# Historian: lineage and context

## Origin
The skill is built on top of the reference corpus "The unreasonable effectiveness of HTML" by Thariq Shihipar (https://thariqs.github.io/html-effectiveness). The original corpus was a blog post + 20 standalone HTML examples demonstrating that agents can produce readable, visual artifacts instead of markdown walls.

## Evolution into a skill
`plannotator` (the org) took the corpus and packaged it as three narrow skills:
- generic HTML artifacts
- architecture diagrams
- plan pages

This packaging fits the skill/plugin marketplaces of Claude Code, Codex, and skills.sh. The README explicitly states the goal: "Focused skills for generating self-contained HTML deliverables with a strong visual bias."

## Related timeline clues
- The bundled corpus references Anthropic PBC copyright in its HTML files ("Copyright 2026 Anthropic PBC · SPDX-License-Identifier: Apache-2.0").
- The top-level repo is MIT, copyright 2026 plannotator.
- The original example demo video is credited to `Fable 5`, a design AI tool, suggesting the visual quality target is AI-generated UI, not hand-coded production components.

## Ecosystem timeline
- `backnotprop/plannotator` is the original, larger project (6.4k stars, 729 commits, 117 releases) for reviewing agent plans and code diffs.
- `plannotator/tot` (11 stars, 16 commits) is a newer git-backed publishing tool for HTML/markdown.
- `plannotator/effective-html` (1.1k stars, 26 commits) is the skill package that feeds HTML artifacts into the Plannotator review surface.
- The README cross-promotes `plannotator` for annotation, `tot` for sharing, and points back to the original `html-effectiveness` corpus.

## Design lineage
The warm palette and editorial typography echo the "Anthropic artifact" style seen in Claude Artifacts and the Plannotator review UI. The core insight — that HTML is a better output format than markdown for spatial/visual information — is explicitly stated in the corpus README.

## Tools used
- `webfetch` on the original `thariqs.github.io/html-effectiveness` site.
- `webfetch` on the `backnotprop/plannotator` and `plannotator/tot` repos to trace the ecosystem.
