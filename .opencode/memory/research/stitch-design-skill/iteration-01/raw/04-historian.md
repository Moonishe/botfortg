# Historian: evolution, context, and ecosystem placement

## 1. Timeline
- **First seen**: Mar 13, 2026 (skills.sh)
- **Repository creation**: around the same period (Google Labs / `google-labs-code` org)
- **Latest release**: v1.0, May 18, 2026
- **Commits**: 66 at time of research
- **Stars / forks**: 6.1K stars, 739 forks
- **Open issues / PRs**: 10 issues, 8 pull requests

## 2. Ecosystem context
- Built for **Google Stitch** (stitch.withgoogle.com), a generative UI design tool announced by Google Labs.
- Follows the **Agent Skills** open standard (agentskills.io), designed for compatibility across coding agents: Antigravity, Gemini CLI, Claude Code, Cursor, Codex.
- Hosted on **Vercel's skills.sh** directory, a marketplace/discovery layer for agent skills.
- Not an officially supported Google product.

## 3. Design lineage
The skill inherits ideas from several recent AI/UI movements:
- **Prompt engineering for image generation** → translated into UI/UX keyword mappings.
- **Design tokens / design systems** (Material 3, Tailwind, style-dictionary) → captured in `.stitch/DESIGN.md` YAML frontmatter.
- **Anti-AI-slop design taste** (taste-design skill) → explicit bans on Inter, generic serifs, neon glows, fake metrics, etc.
- **Static site extraction** (Puppeteer, single-file HTML) → precedents in tools like `single-file-cli`, `monolith`, but tailored for Stitch upload.
- **Autonomous agent loops** (stitch-loop) → continuation/baton pattern similar to Voyager, MetaGPT, or SWE-agent loops.

## 4. Repository structure evolution
Initial layout follows the Agent Skills standard:

```
plugins/
├── stitch-design/       # Core design workflows
├── stitch-build/        # Code generation
└── stitch-utilities/    # Prompt & design-system helpers
```

Each skill:
```
skills/<skill-name>/
├── SKILL.md            # Mission Control
├── scripts/            # Validation & Networking
├── resources/          # Checklists & Style Guides
└── examples/           # Gold-standard references
```

Notable files observed:
- `plugins/stitch-design/skills/generate-design/SKILL.md` — the central routing skill.
- `plugins/stitch-design/skills/extract-static-html/scripts/snapshot.ts` — most complex helper (Puppeteer, 600+ lines).
- `plugins/stitch-design/skills/upload-to-stitch/scripts/upload_to_stitch.py` — token-limit bypass script.
- `plugins/stitch-design/skills/manage-design-system/reference/tool-schema.md` — Stitch MCP schema reference.
- `plugins/stitch-utilities/skills/taste-design/SKILL.md` — premium/anti-generic design opinion.

## 5. Standards and compatibility
- **Agent Skills standard**: skill metadata via YAML frontmatter in `SKILL.md`, `plugin.json` for plugin metadata.
- **Allowed tools**: each skill declares `allowed-tools` including `stitch*:*`, `Bash`, `Read`, `Write`, `web_fetch`.
- **MCP namespace discovery**: agents must call `list_tools` to find the Stitch prefix (e.g., `mcp_stitch:`).
- **Installation**: `npx skills add https://github.com/google-labs-code/stitch-skills --skill stitch-design` or `npx plugins add` for Codex/Claude/Cursor.

## 6. Relationship to other skills
- `generate-design` is the user-facing router.
- `manage-design-system` is the backend for design-token persistence.
- `extract-design-md` / `design-md` are the upstream producers of `DESIGN.md`.
- `upload-to-stitch` is the shared networking layer for file upload.
- `extract-static-html` is the upstream producer of self-contained HTML for `code-to-design` and `upload-to-stitch`.
- `stitch-build` skills consume the `.stitch/designs` output.
- `stitch-utilities` skills (especially `taste-design`) shape the content of `DESIGN.md`.
- `stitch-loop` orchestrates repeated generation using `.stitch/next-prompt.md` as a baton.

## 7. Community signals
- High install count (25.3K) likely driven by skills.sh discovery and Google Labs branding.
- GitHub activity is moderate (6.1K stars, 739 forks) but issue/PR velocity is low (10 issues, 8 PRs).
- The project is clearly a Google Labs experiment, not a long-term supported product.

## 8. Historical parallels
- Similar to earlier "agent skill" libraries (e.g., Vercel's own skills registry, Anthropic's computer use recipes, OpenAI's evals).
- The `.stitch/` directory convention mirrors `.vscode/`, `.cursor/`, `.claude/` — project-local agent context.
- The baton pattern in `stitch-loop` echoes earlier autonomous coding agents that use file-based state machines.

## 9. Gaps in historical record
- No public changelog or detailed release notes beyond the two GitHub releases.
- No published evaluation of how well the prompt-enhancement pipeline improves Stitch output.
- No documented migration path from earlier versions or competing tools.
- No post-mortems or incident reports from the 10 open issues.
