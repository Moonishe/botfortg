# Practitioner: how to use it

## Installing the skill

### skills.sh (general)
```bash
npx skills add plannotator/effective-html
npx skills add plannotator/effective-html --list
npx skills add plannotator/effective-html --skill html-diagram
npx skills add plannotator/effective-html --skill html-plan
```

### Claude Code
```
/plugin marketplace add plannotator/effective-html
/plugin install plannotator-effective-html@effective-html
```

### Codex
```bash
codex plugin marketplace add plannotator/effective-html
codex plugin add plannotator-effective-html@effective-html
```

### OpenCode
OpenCode can consume skills via the same skill format. The repo is not a native OpenCode skill, but the structure (`SKILL.md`, `references/`, and `agents/openai.yaml`) maps closely to OpenCode's `.opencode/skills/` convention.

## Invoking each skill
The `agents/openai.yaml` gives the default prompt form:
- `$html` -> "Create a polished standalone HTML artifact."
- `$html-diagram` -> "Create a polished architecture diagram."
- `$html-plan` -> "Create a pragmatic HTML plan."

In practice, a user would say:
- "Use $html to create a status report for this week."
- "Use $html-diagram to draw the architecture of our bot."
- "Use $html-plan to turn my rough notes into a launch plan."

## Workflow with sharing
1. Agent generates the HTML file.
2. User saves it locally or opens it in the browser.
3. Optional: share with `tot`:
   ```bash
   npm i -g @plannotator/tot
   tot page.html
   # returns https://tot.page/<id>
   ```
4. Optional: review with Plannotator:
   ```
   /plannotator-annotate report.html --render-html
   ```

## For a local project (e.g., TelegramHelper)
The skill files can be copied into an OpenCode-compatible skills directory. The important files are:
- `skills/<skill>/SKILL.md` — the prompt.
- `skills/<skill>/references/html-effectiveness/` — the corpus.
- `skills/<skill>/agents/openai.yaml` — optional interface metadata.

An OpenCode `opencode.json` could reference the skill folder directly. The key is to make the prompt and the reference files available to the model, not to install a runtime package.

## When to use which skill
| Situation | Skill |
|-----------|-------|
| User wants a readable report, deck, or explainer | `html` |
| User wants a system diagram or stack map | `html-diagram` |
| User has rough notes and wants a clean plan page | `html-plan` |
| User wants interactive, editable, or shareable output | `html` + optional `tot`/Plannotator |

## Cost/quality trade-offs
- The skill does not add new dependencies.
- It increases token usage because the model must read the reference corpus.
- Output quality is high on large models; smaller models may struggle with SVG and dark mode.
- Best for one-off artifacts, planning, and communication, not for production UI components.

## Tools used
- `webfetch` on the README install section.
- `webfetch` on the `plannotator/tot` README for sharing workflow.
- `webfetch` on the `backnotprop/plannotator` README for review workflow.
