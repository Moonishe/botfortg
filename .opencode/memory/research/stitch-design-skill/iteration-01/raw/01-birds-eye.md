# Bird's Eye: stitch-design skill overview

## Source metadata
- **Skill page**: https://www.skills.sh/google-labs-code/stitch-skills/stitch-design
- **Repository**: https://github.com/google-labs-code/stitch-skills
- **Plugin**: `plugins/stitch-design/`
- **Version**: 1.0.0 (plugin.json)
- **License**: Apache-2.0
- **Metrics**: 25.3K installs, 6.1K GitHub stars, first seen Mar 13, 2026 (skills.sh)

## One-sentence purpose
Unified design-system entry point that transforms rough UI ideas into structured, Stitch-optimized prompts, synthesizes existing work into `.stitch/DESIGN.md`, routes requests between generation / editing / documentation workflows, and downloads generated HTML/screenshots to `.stitch/designs`.

## Plugin architecture
The repository is a monorepo of three plugins:

| Plugin | Responsibility | Skills included |
|--------|---------------|-------------------|
| `stitch-design` | Core design workflows | `generate-design`, `manage-design-system`, `code-to-design`, `extract-design-md`, `extract-static-html`, `upload-to-stitch` |
| `stitch-build` | Code generation from designs | `react-components`, `react-native`, `remotion`, `shadcn-ui` |
| `stitch-utilities` | Prompt/design-system helpers | `design-md`, `enhance-prompt`, `stitch-loop`, `taste-design` |

Each skill follows the Agent Skills open standard: `SKILL.md` + `scripts/` + `resources/` + `examples/`.

## What the skill actually does (routing level)
1. **Generate** — text or image → high-fidelity Stitch screen (`generate-design`).
2. **Edit** — modify an existing screen (`generate-design` Edit flow).
3. **Variants** — explore alternative layouts/colors (`generate-design` Variants flow).
4. **Design-system synthesis** — create `.stitch/DESIGN.md` from existing code or Stitch projects (`manage-design-system`, `extract-design-md`, `design-md`).
5. **Asset upload/download** — move files between local `.stitch/` and Stitch cloud (`upload-to-stitch`, `extract-static-html`).

## Key conventions
- All generation prompts must describe **layout, content, structure** — never colors/fonts/tokens (those live in the project-level design system).
- Edit prompts can use hex codes for precise changes.
- Generated assets are saved under `.stitch/designs/`; design truth lives in `.stitch/DESIGN.md`; project metadata in `.stitch/metadata.json`.
- Uploads bypass the model's output-token limit by using a local Python script that base64-encodes files in-process.

## Tooling surface
- Requires the **Stitch MCP server** (`stitch*:*` tools).
- Uses `Bash`, `Read`, `Write`, `web_fetch` as auxiliary tools.
- Snapshot scripts use `puppeteer` (Strategy A) or Babel JSX parsing (Strategy B fallback).
- Upload script uses Python + `urllib.request` with optional `certifi` SSL bundle.

## Security / governance notes
- Security audits on skills.sh: Gen Agent Trust Hub Pass, Socket Pass, Snyk Warn.
- Snapshot scripts validate URLs (SSRF protection) and block private/internal networks.
- User confirmation checkpoints before uploading design systems or running snapshot scripts.
- Not an officially supported Google product.

## Top-level fit
This is a prompt-router + asset-manager wrapper around Google Stitch, not a standalone renderer. It is designed to be consumed by coding agents (Claude Code, Cursor, Codex, Gemini CLI, Antigravity) through the Agent Skills standard.
