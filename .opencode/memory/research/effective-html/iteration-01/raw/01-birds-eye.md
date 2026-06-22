# Bird's Eye: effective-html

## What it is
`plannotator/effective-html` is a public agent-skill repository that teaches AI agents to emit self-contained, visually effective HTML artifacts instead of markdown walls. It is published as a skills.sh skill, a Claude Code plugin, a Codex plugin, and an agents marketplace plugin. The repo contains zero runtime code: it is a collection of prompt packages (SKILL.md) plus a bundled reference corpus (`html-effectiveness`) that the model is instructed to study before generating output.

## Tools used
- `webfetch` on the GitHub repo page and README.md.
- `webfetch` on the skills.sh registry JSON (`skills.sh.json`) and the plugin manifest files.

## Top-level facts
- Repo: https://github.com/plannotator/effective-html
- License: MIT (top-level). The bundled `html-effectiveness` reference corpus is Apache-2.0 and explicitly marked "Sample code. Not maintained and not accepting contributions."
- Stars: 1.1k, Forks: 77, Commits: 26 (as of main, 2026-06-22).
- Language: HTML 100% (GitHub classification).
- Owner: `plannotator` (the same org behind `tot` and related to `backnotprop/plannotator`).

## Three skills
| Skill | Trigger / use case |
|-------|---------------------|
| `html` | Generic self-contained HTML artifact: reports, explainers, comparisons, decks, prototypes. |
| `html-diagram` | Full-screen architecture/stack diagrams with high-quality SVG, minimal prose, interactive flows. |
| `html-plan` | Pragmatic visual plan pages in the effective HTML style. |

Each skill has a `SKILL.md` front-matter with `disable-model-invocation: true`, meaning the skill is a prompt wrapper, not a tool call. The `agents/openai.yaml` in each skill maps it to an OpenAI-agent-compatible interface.

## Repository structure
```
effective-html/
├── README.md
├── LICENSE
├── skills.sh.json              # skills.sh registry manifest
├── .claude-plugin/
│   ├── marketplace.json        # Claude Code marketplace listing
│   └── plugin.json             # Claude plugin package manifest
├── .codex-plugin/
│   └── plugin.json             # Codex plugin manifest
├── .agents/plugins/
│   └── marketplace.json        # Agents marketplace manifest
├── star-plannotator.svg
├── use-tot.svg
└── skills/
    ├── html/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   └── references/html-effectiveness/   # 20 example HTML files
    ├── html-diagram/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   ├── references/html-effectiveness/   # same 20 examples
    │   └── references/architecture-example.html
    └── html-plan/
        ├── SKILL.md
        ├── agents/openai.yaml
        └── references/html-effectiveness/   # same 20 examples
```

## Distribution channels
- `npx skills add plannotator/effective-html` (skills.sh)
- Claude Code: `/plugin marketplace add plannotator/effective-html` then `/plugin install plannotator-effective-html@effective-html`
- Codex: `codex plugin marketplace add plannotator/effective-html` then `codex plugin add plannotator-effective-html@effective-html`

## Adjacent projects
- `plannotator/tot` (https://github.com/plannotator/tot): instant share links for HTML/markdown, git-backed, used for sharing generated HTML.
- `backnotprop/plannotator` (https://github.com/backnotprop/plannotator): browser-based review surface for agent plans, diffs, and HTML artifacts. The effective-html skill is explicitly linked from its README.

## Ecosystem positioning
The repo is a content/format skill, not a framework. It competes with ad-hoc "generate a nice HTML report" prompts and complements diagram-as-code tools by preferring inline SVG and hand-rolled CSS. It is designed to be consumed by agent IDEs (Claude Code, Codex, OpenCode, etc.) via their plugin/skill systems.
