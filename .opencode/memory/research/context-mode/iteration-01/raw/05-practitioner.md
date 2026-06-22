# 05-practitioner.md — context-mode (Practitioner Perspective)

## Research Methods
- read of configs/opencode/AGENTS.md, configs/opencode/opencode.json
- read of configs/claude-code/CLAUDE.md, configs/gemini-cli/GEMINI.md, configs/cursor/context-mode.mdc, configs/codex/AGENTS.md
- read of skills/context-mode/SKILL.md
- read of README.md install sections for each platform
- review of README.md "Try It" prompts and "Utility Commands"

## How to Integrate (General Pattern)
1. Install `context-mode` globally (`npm install -g context-mode`) or as a plugin.
2. Register the MCP server in the host's config (mcp.json, settings.json, etc.).
3. Add hooks if the platform supports them (preToolUse, postToolUse, sessionStart, preCompact).
4. Copy the platform-specific routing file (CLAUDE.md / AGENTS.md / GEMINI.md / context-mode.mdc / KIRO.md) to the project root for model awareness.
5. Verify with `ctx stats` or `ctx doctor`.

## Example Configs

### OpenCode (native plugin)
`opencode.json`:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["context-mode"]
}
```
Optional routing file: `cp node_modules/context-mode/configs/opencode/AGENTS.md AGENTS.md`

### Claude Code (plugin marketplace)
```
/plugin marketplace add mksglu/context-mode
/plugin install context-mode@context-mode
```
MCP-only fallback:
```
claude mcp add context-mode -- npx -y context-mode
```

### Gemini CLI
`~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "context-mode": { "command": "context-mode" }
  },
  "hooks": {
    "BeforeTool": [
      {
        "matcher": "run_shell_command|read_file|read_many_files|grep_search|search_file_content|web_fetch|activate_skill|mcp__plugin_context-mode|mcp__context-mode|mcp__(?!.*context-mode)",
        "hooks": [{ "type": "command", "command": "context-mode hook gemini-cli beforetool" }]
      }
    ],
    "AfterTool": [{ "matcher": "", "hooks": [{ "type": "command", "command": "context-mode hook gemini-cli aftertool" }] }],
    "PreCompress": [{ "matcher": "", "hooks": [{ "type": "command", "command": "context-mode hook gemini-cli precompress" }] }],
    "SessionStart": [{ "matcher": "", "hooks": [{ "type": "command", "command": "context-mode hook gemini-cli sessionstart" }] }]
  }
}
```

### VS Code Copilot
`.vscode/mcp.json`:
```json
{
  "servers": {
    "context-mode": { "command": "context-mode" }
  }
}
```
`.github/hooks/context-mode.json`:
```json
{
  "hooks": {
    "PreToolUse": [{ "type": "command", "command": "context-mode hook vscode-copilot pretooluse" }],
    "PostToolUse": [{ "type": "command", "command": "context-mode hook vscode-copilot posttooluse" }],
    "SessionStart": [{ "type": "command", "command": "context-mode hook vscode-copilot sessionstart" }]
  }
}
```

### Cursor
`.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "context-mode": { "command": "context-mode" }
  }
}
```
`.cursor/hooks.json`:
```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [{ "command": "context-mode hook cursor pretooluse", "matcher": "Shell|Read|Grep|WebFetch|Task|MCP:ctx_execute|MCP:ctx_execute_file|MCP:ctx_batch_execute" }],
    "postToolUse": [{ "command": "context-mode hook cursor posttooluse" }],
    "stop": [{ "command": "context-mode hook cursor stop" }]
  }
}
```
Copy `.cursor/rules/context-mode.mdc` from `configs/cursor/context-mode.mdc`.

## Usage Patterns

### Pattern 1: Deep repo research (one-shot gather + search)
```
ctx_batch_execute(
  commands: [
    {label: "prs", command: "gh pr list --repo modelcontextprotocol/servers --json number,title,state"},
    {label: "issues", command: "gh issue list --repo modelcontextprotocol/servers --json number,title,labels"},
    {label: "contributors", command: "gh api repos/modelcontextprotocol/servers/contributors --jq '.[] | .login' | head -20"}
  ],
  queries: ["architecture", "top contributors", "open issues", "recent activity"],
  concurrency: 4
)
```

### Pattern 2: Analyze a large file
```
ctx_execute_file(
  path: "huge.log",
  language: "javascript",
  code: "const errs = FILE_CONTENT.split('\\n').filter(l => /ERROR|FATAL/.test(l)); console.log(`${errs.length} errors`); console.log(errs.slice(-5).join('\\n'))"
)
```

### Pattern 3: Fetch and query docs
```
ctx_fetch_and_index(
  requests: [
    {url: "https://raw.githubusercontent.com/reactjs/react.dev/main/src/content/reference/react/useEffect.md", source: "react-useeffect"}
  ],
  concurrency: 1
)
ctx_search(queries: ["useEffect cleanup pattern"], source: "react-useeffect", contentType: "code", limit: 5)
```

### Pattern 4: Session continuity after compaction
- After compaction, `ctx_search(sort: "timeline")` retrieves prior decisions, errors, blockers.
- `ctx_search(source: "compaction", queries: ["summary"])` for the session guide.
- `ctx_search(source: "user-prompt", queries: ["prompt"])` for the last request.

### Pattern 5: Stats & diagnostics
- `ctx stats` — savings report
- `ctx doctor` — run diagnostics
- `ctx upgrade` — fix hooks/config
- `ctx purge(confirm: true, scope: "project")` — wipe knowledge base

## Skills
- `skills/context-mode/SKILL.md` — default skill for large outputs, with Bash whitelist and decision tree.
- `.claude/skills/context-mode-ops/` — TDD, triage, PR review, release, validation, agent-teams.

## Operational Tips
- Use `concurrency: 4-8` for I/O-bound batches, `concurrency: 1` for CPU-bound/shared-state.
- GitHub API rate limit: cap at 4 for `gh` calls.
- Always use `source` labels when indexing; partial match works.
- Batch all `ctx_search` queries in one array.
- Use `intent` on `ctx_execute`/`ctx_execute_file` when output may be large.
- After `/clear` or `/compact`, knowledge base persists; use `ctx purge` to start fresh.

## Sources
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\opencode\AGENTS.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\opencode\opencode.json`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\claude-code\CLAUDE.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\gemini-cli\GEMINI.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\cursor\context-mode.mdc`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\configs\codex\AGENTS.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\skills\context-mode\SKILL.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\README.md` (Install, Try It, Utility Commands sections)
