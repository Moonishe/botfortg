# Practitioner: how to actually use stitch-design

## 1. First-time setup
1. Install the skill:
   ```bash
   npx skills add https://github.com/google-labs-code/stitch-skills --skill stitch-design
   ```
   Or for a full plugin suite:
   ```bash
   npx plugins add google-labs-code/stitch-skills --scope project --target claude-code
   ```
2. Configure the Stitch MCP server and obtain an API key.
3. Ensure Node.js + `puppeteer` are available if using `extract-static-html` Strategy A.
4. Ensure Python 3 is available for `upload-to-stitch` script.

## 2. Typical workflow: generate a new screen
1. User: "Make a mobile workout app home screen."
2. Agent: check for existing project (`list_projects`) and design system (`list_design_systems`).
3. If no design system, delegate to `manage-design-system` (or create a minimal one first).
4. Enhance the prompt using `design-mappings.md` and `prompt-keywords.md`.
5. Call `generate_screen_from_text` with `deviceType: MOBILE` and the structured prompt.
6. Show `outputComponents` description/suggestions to user.
7. Download `htmlCode.downloadUrl` and `screenshot.downloadUrl` to `.stitch/designs/`.
8. Update `.stitch/metadata.json` with the new screen.

## 3. Typical workflow: edit a screen
1. User: "Change the primary button to blue and add a shadow."
2. Agent: identify screen via `list_screens` / `get_screen`.
3. Build a targeted edit prompt with location, visuals, structure.
4. Call `edit_screens`.
5. Show AI feedback.
6. Re-download assets and overwrite local copies.

## 4. Typical workflow: migrate existing web app to Stitch
1. User: "Upload my existing React dashboard to Stitch."
2. Agent: run `extract-static-html` (Puppeteer snapshot) on the running app.
3. Run `extract-design-md` on the source code to produce `.stitch/DESIGN.md`.
4. Use `upload_to_stitch.py` to upload `DESIGN.md`.
5. Call `create_design_system_from_design_md`.
6. Use `upload_to_stitch.py` again to upload the standalone HTML.
7. Confirm project and design system with user.

## 5. Typical workflow: build a multi-page site autonomously
1. Create `.stitch/SITE.md` with vision, sitemap, roadmap.
2. Create `.stitch/DESIGN.md` (via `design-md` or `taste-design`).
3. Write `.stitch/next-prompt.md` (baton) with the first page prompt.
4. Each iteration:
   - Read baton.
   - Generate screen with Stitch.
   - Download to `.stitch/designs/`.
   - Move HTML to `site/public/`.
   - Update navigation.
   - Update `.stitch/SITE.md` sitemap.
   - Write the next baton.

## 6. Files you will touch
| File | When to touch | Owned by |
|------|---------------|----------|
| `.stitch/DESIGN.md` | Before any multi-screen project | `manage-design-system` / `extract-design-md` |
| `.stitch/metadata.json` | After every project/screen creation | `generate-design`, `stitch-loop` |
| `.stitch/designs/*.html` | After generation | `generate-design` |
| `.stitch/designs/*.png` | After generation | `generate-design` |
| `.stitch/SITE.md` | Multi-page autonomous sites | `stitch-loop` |
| `.stitch/next-prompt.md` | Every loop iteration | `stitch-loop` |

## 7. What works well
- Text-to-screen generation for marketing pages, dashboards, mobile apps.
- Design-system consistency once `.stitch/DESIGN.md` is created and uploaded.
- Code-to-design migration for React/Tailwind apps that can run locally.
- Static asset extraction for sharing or archiving a rendered UI state.

## 8. What is painful
- Manual checkpoints break fully autonomous CI/CD.
- Upload script path depends on where the skill was installed.
- Puppeteer installation can be heavy and flaky in containerized environments.
- Designing from scratch without a design system requires an extra `manage-design-system` step.
- React Native conversion requires careful manual mapping of CSS to StyleSheet.

## 9. Best practices learned from the skill docs
- One focused edit at a time; do not bundle unrelated changes.
- Never specify colors/fonts in generation prompts when a design system exists.
- Use hex codes only in edit prompts.
- Always surface `outputComponents` feedback to the user.
- Keep `.stitch/metadata.json` up to date; it is the only local record of cloud screen IDs.
- Use `upload_to_stitch.py` for any non-trivial file upload; never use the MCP tool directly for base64 payloads.

## 10. Integration with coding agents
- The skill is designed to be invoked by an agent, not used directly by a human developer typing commands.
- The agent must parse the `SKILL.md`, decide which flow to run, and call the right tools.
- Human input is expected at natural checkpoints (confirm design system upload, choose snapshot strategy, confirm file upload).

## 11. Practical checklist before starting
- [ ] Stitch MCP server configured and reachable.
- [ ] API key available in MCP config or user-provided.
- [ ] Node.js + Puppeteer available if extracting static HTML.
- [ ] Python 3 available if uploading files.
- [ ] Project directory clean or `.stitch/` already initialized.
- [ ] User expectations aligned: Stitch generates screens, not functional code.
