# Devil's Advocate: risks, limitations, and questionable assumptions

## 1. Dependency on Stitch MCP + Google cloud
- The skill is a thin wrapper; the actual model/ renderer is Google Stitch.
- Requires a configured Stitch MCP server, API key, and network access to `https://stitch.googleapis.com`.
- No fallback if Stitch API is unavailable, rate-limited, or the project is deleted.
- All generated IP lives in Google's cloud unless explicitly downloaded.

## 2. Token-limit workarounds are brittle
- The upload script exists because base64 payloads exceed the model's output-token limit (~16K tokens).
- This means the skill cannot be used with the raw MCP tool for files > ~5KB of markdown or any non-trivial image.
- If the script path is wrong or Python environment is missing, the workflow stalls.
- SSL cert errors on macOS require manual `certifi` setup.

## 3. "User confirmation" checkpoints may break automation
- Multiple skills (`manage-design-system`, `extract-static-html`) require explicit user approval before proceeding.
- This is good for safety but hostile to CI/CD or autonomous loops.
- The `stitch-loop` skill tries to be autonomous, but it still relies on `generate-design` and `upload-to-stitch` checkpoints in some paths.

## 4. Design-system enforcement is only as good as the prompt
- The rule "no colors/fonts in generation prompts" is sound in theory, but:
  - It depends on the agent correctly detecting `list_design_systems`.
  - If no design system exists, the skill says to delegate to `manage-design-system` first — adding a mandatory extra step.
  - Users may still ask for explicit colors, creating conflict with project-level tokens.

## 5. Asset management is download-centric, not sync-centric
- The skill downloads HTML/screenshots to `.stitch/designs` after generation.
- It does not keep a bidirectional sync: edits in Stitch cloud are not auto-reflected locally unless the agent re-downloads.
- Overwriting previous versions is manual guidance, not automatic.

## 6. Code-to-design is lossy
- `extract-static-html` flattens a running app into a single HTML file; JavaScript interactivity is removed (`<script>` tags stripped).
- React/Vue/etc. become static Stitch screens, not functional apps.
- CSS-in-JS is materialized, but dynamic state, animations, and data fetching are lost.

## 7. JSX fallback is high-friction
- The "Static Fallback" for broken apps requires manually flattening React components into `MockPage.jsx` with hardcoded data and unrolled loops.
- This is effectively asking the user to rewrite a snapshot of their app by hand.
- Babel parser helps, but conditional rendering, hooks, and context still require manual intervention.

## 8. Security posture is mixed
- Snapshot scripts have SSRF protection (`isSafeUrl`) and block private IP ranges.
- However, the agent is still instructed to run arbitrary local scripts (`npx tsx`, `python3`) with API keys.
- The skill reads MCP config files (`~/.claude.json`, `.gemini/...`) to extract API keys, which is a sensitive trust boundary.
- Snyk shows "Warn" on skills.sh — not a clean bill of health.

## 9. Platform assumptions
- The documentation assumes Node.js + npm/pnpm ecosystem (Vite, React, Next.js, Tailwind).
- Svelte, Angular, Vue are supported, but the primary examples and tooling are React/Tailwind-first.
- React Native conversion is a separate skill with its own mapping table; it does not share the `.stitch/designs` workflow directly.

## 10. Metrics do not prove quality
- 25.3K installs and 6.1K stars show popularity, not correctness or production readiness.
- The repository is dated Mar 13, 2026 — relatively young. Only 66 commits, 2 releases (v1.0 May 2026).
- 10 issues, 8 PRs at the time of research.

## 11. Maintainability concerns
- Monorepo structure with three plugins and shared scripts means skills have inter-dependencies.
- The Agent Skills standard is still young; skill names, prefixes, and allowed-tools may evolve.
- Hard-coded paths like `.stitch/designs` and `.stitch/DESIGN.md` are conventions, not configurable.

## 12. What is not covered
- No built-in testing of generated HTML/CSS.
- No accessibility audit (a11y) except a brief mention in React Native skill.
- No versioning of design system changes.
- No cost estimation for Stitch API usage.
- No offline mode.
