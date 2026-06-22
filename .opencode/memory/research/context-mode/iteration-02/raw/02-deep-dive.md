# Deep Dive — mksglu/context-mode (Researcher 2)

> Source: https://github.com/mksglu/context-mode (branch: main)
> Files fetched: src/server.ts, src/store.ts, src/session/db.ts, src/executor.ts,
>   src/search/unified.ts, src/search/flood-guard.ts, src/security.ts,
>   hooks/hooks.json, hooks/pretooluse.mjs, hooks/posttooluse.mjs,
>   hooks/precompact.mjs, hooks/sessionstart.mjs
> Date: 2026-06-22

---

## SUMMARY

context-mode is an MCP server + Claude Code plugin that reduces LLM context
window consumption through three complementary mechanisms:

1. **Sandbox execution (Think-in-Code)** — `ctx_execute` / `ctx_execute_file` /
   `ctx_batch_execute` run code in a polyglot subprocess (12 languages). Only
   `console.log`/`print` output enters the conversation; the raw data the code
   processes stays in the sandbox. Large outputs (>5KB with `intent`, >100KB
   without) are auto-indexed into an FTS5 knowledge base and replaced with
   section titles + previews.

2. **FTS5 knowledge base (ContentStore)** — `ctx_index` / `ctx_fetch_and_index`
   persist content (markdown, JSON, plain text, directories) in a per-project
   SQLite FTS5 store with dual tokenizers (porter + trigram), BM25 ranking,
   Reciprocal Rank Fusion, proximity reranking, and Levenshtein fuzzy
   correction. `ctx_search` queries it with a progressive per-agent throttle.

3. **Session continuity (SessionDB + hooks)** — Claude Code hooks
   (PreToolUse/PostToolUse/PreCompact/SessionStart/UserPromptSubmit/Stop)
   capture 26+ event categories (decisions, errors, blockers, plans, user
   prompts, rejected approaches, tool failures, compaction guides, file edits,
   git commits, latency, redirects) into a per-project SQLite SessionDB.
   PreCompact builds a priority-sorted resume snapshot (<2KB) injected on
   compact/resume.

The system supports 15 platform adapters (Claude Code, Cursor, OpenCode, Codex,
Kiro, Zed, Kilo, OpenClaw, Qwen Code, VSCode Copilot, JetBrains Copilot, OMP,
Pi, Antigravity, Gemini CLI) with per-platform session/content storage
isolation. Security is layered: a deny-only firewall (server-side) + hook-side
routing + sandbox env sanitization (60+ denied env vars) + SSRF guard for
fetches + TOCTOU defense for file reads (fd-bound fstat).

**Total MCP tools registered: 11** — ctx_execute, ctx_execute_file, ctx_index,
ctx_search, ctx_fetch_and_index, ctx_batch_execute, ctx_stats, ctx_doctor,
ctx_upgrade, ctx_purge, ctx_insight.

---

## ARCHITECTURE

### Layer 1 — MCP Server (`src/server.ts`, ~4600+ lines)

The server is built on `@modelcontextprotocol/sdk` (`McpServer` +
`StdioServerTransport`). Key subsystems:

- **Tool registration wrapper**: `server.registerTool` is monkey-patched to
  (a) suppress duplicate tools when the plugin-native host (OpenCode/Kilo)
  already registers them (#623/#637), (b) wrap every handler in
  `wrapToolHandler` for storage-error recovery, (c) push into
  `REGISTERED_CTX_TOOLS` for plugin-native re-registration.

- **Lazy singleton ContentStore**: `getStore()` creates the FTS5 DB on first
  use, wires a deny-checker callback (re-checks Read deny patterns on
  auto-refresh), runs stale-DB cleanup (>14 days), and auto-indexes
  `*-events.md` files written by SessionStart hooks.

- **Session attribution**: `currentAttribution()` resolves `sessionId` from
  AsyncLocalStorage override → `CLAUDE_SESSION_ID` env → most-recent
  `session_id` in the per-project SessionDB (2s cache). Used to FK-tag every
  indexed chunk for per-session stats.

- **Stats tracking**: `trackResponse()` / `trackIndexed()` accumulate
  per-tool byte counts, persist a throttled (500ms) `stats-*.json` sidecar
  for the statusline, and emit sandbox-execute / index-write / cache-hit
  events to SessionDB via `setImmediate` (off hot path).

- **Progressive search throttle** (`FloodGuard`): per-agent-context rolling
  window (60s default). Soft cap at 3 calls → 1 result/query. Hard block at
  8 calls → refuse. Tunable via `CONTEXT_MODE_SEARCH_*` env vars.

- **Strict-client schema compat**: `sanitizeSchemaForStrictClients` rewrites
  `const: X` → `enum: [X]` and drops `additionalProperties` so Gemini
  function-calling (Antigravity/Gemini CLI) doesn't silently drop ctx_* tools.

- **Project dir resolution**: env cascade (platform-specific →
  `CONTEXT_MODE_PROJECT_DIR` → PWD → cwd) with Claude Code transcript
  heuristic (freshness-gated 5min) and Codex `meta.cwd` fallback. Strict
  platform gating prevents cross-platform env leakage (#545).

### Layer 2 — ContentStore (`src/store.ts`, ~1400+ lines)

SQLite FTS5 knowledge base with dual-tokenizer multi-strategy search.

**Schema** (3 tables + 1 vocab):
- `sources` (id, label, chunk_count, code_chunk_count, indexed_at, file_path,
  content_hash)
- `chunks` — FTS5 virtual table, `tokenize='porter unicode61'`, 8 columns
  (title, content indexed; source_id, content_type, source_category,
  session_id, event_id, timestamp UNINDEXED)
- `chunks_trigram` — FTS5 virtual table, `tokenize='trigram'`, same 8 columns
- `vocabulary` (word TEXT PRIMARY KEY) — for fuzzy correction

**Indexing strategies**:
- `index()` — markdown heading-based chunking, code blocks kept intact,
  MAX_CHUNK_BYTES=4096 cap, SHA-256 content hash for staleness detection.
  TOCTOU defense: `openSync` + `fstatSync.isFile()` + `readFileSync(fd)`.
- `indexDirectory()` — bounded recursive walk (#687) with per-file deny check.
- `indexPlainText()` — fixed-size line groups (20 lines default, overlap).
- `indexJSON()` — object-tree walk, key paths as chunk titles.

**Atomic dedup**: single transaction deletes prior chunks for same label,
then inserts new — prevents stale accumulation in iterative workflows (#67).

**FTS5 optimization**: `OPTIMIZE_EVERY=50` inserts triggers `optimize` command
to merge b-tree segments.

**Search pipeline** (`searchWithFallback`):
1. Auto-refresh stale file-backed sources (deny-checked).
2. **RRF fusion** (`#rrfSearch`): porter OR search + trigram OR search →
   merge via Reciprocal Rank Fusion (K=60, Cormack et al. 2009). Score =
   Σ 1/(K + rank_i + 1). Key = `source::title`.
3. **Proximity reranking** (`#applyProximityReranking`):
   - Title-match boost (code: 0.6, prose: 0.3) × (titleHits/terms.length)
   - Proximity boost: `1/(1 + minSpan/contentLength)` — sweep-line min window
   - Phrase-frequency boost: `0.5 × min(1, adjacentPairs/4)` — saturating,
     30-char gap, each right position consumed once
4. **Fuzzy correction** (Layer 3 fallback): if RRF returns 0, run
   `fuzzyCorrect` per word (Levenshtein, length-adaptive maxDist: ≤4→1,
   ≤12→2, >12→3) against vocabulary table, re-run RRF with corrected query.
   LRU cache (256 entries).

**Prepared statements**: 24 cached (write/dedup/search×12 variants/read/
cleanup). 6 content-type-filtered variants (porter/trigram × none/like/exact).
Source filter escapes LIKE metacharacters (#646).

**Stopwords**: ~110 common English + code/changelog terms filtered from BM25
queries to improve ranking. Fallback to unfiltered if all terms are stopwords.

### Layer 3 — SessionDB (`src/session/db.ts`, ~1100+ lines)

Per-project SQLite database for session events, extending `SQLiteBase`.

**Schema** (4 tables):
- `session_events` (id, session_id, type, category, priority, data,
  project_dir, attribution_source, attribution_confidence, bytes_avoided,
  bytes_returned, source_hook, created_at, data_hash)
- `session_meta` (session_id PK, project_dir, started_at, last_event_at,
  event_count, compact_count)
- `session_resume` (id, session_id UNIQUE, snapshot, event_count, created_at,
  consumed)
- `tool_calls` (session_id, tool, calls, bytes_returned, updated_at;
  PK(session_id, tool))

**Event insertion** (`insertEvent` / `bulkInsertEvents`):
- Dedup: SHA-256(data) first 16 hex chars, checked against last
  DEDUP_WINDOW=5 events of same type.
- FIFO eviction: MAX_EVENTS_PER_SESSION=1000, evicts lowest-priority then
  oldest.
- Atomic: dedup + evict + insert in single transaction.
- Bulk path: pre-compute hashes outside transaction, single transaction for
  N events (one WAL commit — critical on Windows NTFS).

**Session rollup** (`getSessionRollup`): single query producing 9 of 12
platform-side fields (tool_calls, errors, unique_tools, unique_files,
has_commit, duration_min, sources_indexed, total_chunks, search_queries) +
separate queries for max_file_edits and latest_commit_message.

**Resume**: `upsertResume` stores snapshot; `claimLatestUnconsumedResume`
atomically claims + marks consumed (race-safe, excludes self-session to
prevent re-injection).

**Path resolution**:
- `hashProjectDirCanonical` — case-fold on macOS/Windows, preserve on Linux.
- `hashProjectDirLegacy` — raw casing (≤ v1.0.111).
- `resolveSessionDbPath` — one-shot legacy→canonical migration (preserves
  history on upgrade, dual-hash safety).
- `getWorktreeSuffix` — git worktree isolation via `git worktree list`,
  memoized, `__<8-hex>` suffix. Canonicalized comparison (#round-5).
- `resolveContentStorePath` — same dual-hash for FTS5 store (no worktree
  suffix; per-project not per-worktree).

**Storage override**: `CONTEXT_MODE_DIR` env overrides session/content/stats
root. `ensureWritableStorageDir` validates + caches writability.

**Schema migration**: `applyMissingSessionEventsColumns` adds post-v1.0.130
columns to legacy DBs (idempotent, PRAGMA-guarded). `ensureSessionEventsSchema`
for lazy migration by the analytics aggregator.

### Layer 4 — PolyglotExecutor (`src/executor.ts`, ~500+ lines)

Sandboxed code execution for 12 languages: JavaScript, TypeScript, Python,
Shell, Ruby, Go, Rust, PHP, Perl, R, Elixir, C#.

- **Temp dir**: `mkdtempSync` under OS-real tmpdir (bypasses TMPDIR override).
  Windows cleanup retries 8× (EBUSY/EPERM race on SQLite WAL handles).
- **Safe env** (`#buildSafeEnv`): 60+ denied env vars (BASH_ENV, NODE_OPTIONS,
  PYTHONSTARTUP, LD_PRELOAD, DYLD_INSERT_LIBRARIES, RUSTC, GIT_SSH_COMMAND,
  CORECLR_PROFILER*, DOTNET_*, COMPlus_* prefix sweep, etc.). Sandbox
  overrides: TMPDIR, HOME, LANG=en_US.UTF-8, PYTHONUTF8=1, NO_COLOR=1.
  Windows: Git usr/bin prepended to PATH, MSYS_NO_PATHCONV deliberately NOT
  set (native git.exe needs path conversion).
- **Hard cap**: 100MB combined stdout+stderr, process killed on exceed.
- **Timeout**: caller-controlled; no server timer when omitted (host RPC
  timeout governs, #406). Antigravity CLI gets 120s default (no RPC timeout).
- **Background mode**: `background:true` detaches on timeout, keeps process
  alive, returns partial output, drains streams without SIGPIPE.
- **Process-tree kill**: Windows `taskkill /F /T`, Unix `kill(-pgid, SIGKILL)`.
- **Windows build tools**: `rewriteWindowsBuildTools` rewrites bare `mvn` →
  `mvn.cmd` in Git Bash (#782).
- **executeFile**: wraps user code with `FILE_CONTENT` variable per language
  (reads file in-sandbox, raw bytes never enter conversation).

### Layer 5 — Hooks (`hooks/`, 6 hook types)

**hooks.json** registers 6 hook types with matchers:

| Hook | Matcher | Script | Purpose |
|------|---------|--------|---------|
| PreToolUse | Bash, WebFetch, Read, Grep, Agent, mcp__ctx_execute*, mcp__ | pretooluse.mjs | Route data-fetching tools to context-mode tools; deny firewall |
| PostToolUse | Bash\|Read\|Write\|Edit\|...\|mcp__ | posttooluse.mjs | Capture 13+ event categories into SessionDB |
| PreCompact | (all) | precompact.mjs | Build resume snapshot, emit compaction events |
| SessionStart | (all) | sessionstart.mjs | Inject routing rules + resume context, cleanup, CLAUDE.md capture |
| UserPromptSubmit | (all) | userpromptsubmit.mjs | Capture user prompts as decisions |
| Stop | (all) | stop.mjs | Turn-end capture |

All hooks wrapped in `runHook` (#414) for crash-resilience — module loads are
dynamic so a missing dep can't hard-fail the hook.

**PreToolUse** (`pretooluse.mjs`):
- Self-heal: renames misnamed plugin cache dirs, updates
  `installed_plugins.json`, rewrites stale hook paths in `settings.json`.
- Routes via `core/routing.mjs` (`routePreToolUse`) — redirects data-fetching
  tools (curl/wget→ctx_execute, WebFetch→ctx_fetch_and_index, large Read→
  ctx_execute_file) to context-mode tools.
- Writes marker files for PostToolUse to pick up:
  - `context-mode-rejected-<sid>.txt` — denied/modified tools
  - `context-mode-redirect-<sid>.txt` — byte-accounting (tool:type:bytes:summary)
  - `context-mode-latency-<sid>-<tool>.txt` — cross-hook timing
- Cannot load SessionDB (native module load breaks hook stdout) → marker
  indirection.

**PostToolUse** (`posttooluse.mjs`):
- Extracts events via `session-loaders.mjs` → `extractEvents` (13 categories:
  file_edit, file_write, file_read, bash_command, web_fetch, decision, error,
  error_resolution, plan, skill, git_commit, external_ref, tool_failure).
- `attributeAndInsertEvents` routes through wire (v1.0.160) for platform
  dashboard visibility.
- Reads + consumes marker files: rejected-approach events, redirect
  byte-accounting events, latency events (>5s threshold).
- Must be <20ms — no network, no LLM, just SQLite writes.

**PreCompact** (`precompact.mjs`):
- Reads all session events, builds priority-sorted resume snapshot (<2KB XML)
  via `buildResumeSnapshot`.
- `upsertResume` + `incrementCompactCount`.
- Emits `compaction_summary` and `snapshot-built` events (with
  `bytes_avoided=snapshot.length`).

**SessionStart** (`sessionstart.mjs`):
- 4 lifecycle modes: `startup` (fresh), `compact` (post-compact), `resume`
  (--continue/--resume), `clear`.
- **startup**: cleanup old sessions (>7 days), orphan event wipe (UUID-shape
  protected), CLAUDE.md capture (3 paths), `session_start` lifecycle event,
  age-gated lazy cleanup of old plugin cache dirs (>1h, with breadcrumb
  symlinks for active sessions #814).
- **compact**: consume resume snapshot, write events to `*-events.md` for
  auto-indexing, build session directive, auto-inject behavioral state.
- **resume**: try live events, fall back to `claimLatestUnconsumedResume`
  snapshot (#413).
- Self-heal: partial install healing, shell snapshot healing (#710 Layer 2).
- Emits `hookSpecificOutput.additionalContext` with routing rules + context.

### Layer 6 — Unified Search (`src/search/unified.ts`)

`searchAllSources` merges 3 sources:
1. **ContentStore** (always, both modes) — `searchWithFallback`
2. **SessionDB** (timeline mode only) — `searchEvents` (LIKE on data + category)
3. **Auto-memory** (timeline mode only) — `searchAutoMemory`

- `sort="relevance"` (default): ContentStore-only BM25.
- `sort="timeline"`: chronological merge of all 3, ISO timestamp normalized.
- **Project scope (#737)**: `projectScope` string → SessionDB resolves
  `project_dir` → set of session_ids → ContentStore post-filter (legacy
  `session_id=''` chunks stay visible). `null` = cross-project, `undefined` =
  no filter.

### Layer 7 — Security (`src/security.ts`)

- **Glob-to-regex**: colon format (`tree:*` → `^tree(\s.*)?$`) and space
  format (`sudo *` → `^sudo .*$`). File globs support `**` globstar.
- **Chained command splitting**: `splitChainedCommands` respects quotes,
  backticks, `$()`, escapes — prevents bypass via `echo ok && sudo rm -rf /`.
- **Subshell extraction**: `extractSubshellCommands` recursively finds `$()`
  and `` `...` `` commands.
- **Deny-only evaluation** (server-side): `evaluateCommandDenyOnly` checks all
  segments + subshells against deny patterns. Server is fail-open (hooks are
  primary enforcement).
- **File path evaluation**: `evaluateFilePath` matches raw + lexical-resolved
  + canonical (realpath) forms — prevents `..` traversal and symlink escape.
- **Shell-escape scanner**: `extractShellCommands` detects `os.system`,
  `subprocess.run`, `execSync`, `Command::new`, `shell_exec`, backticks, etc.
  in non-shell languages → checks embedded commands against deny patterns.

---

## API_SURFACE

### MCP Tools (11 registered)

| Tool | Annotations | Key Params | Returns |
|------|------------|------------|---------|
| `ctx_execute` | destructive, openWorld | language (12 enum), code, timeout?, background?, cwd?, intent? | stdout only; >5KB with intent → indexed + section previews; >100KB → indexed + pointer |
| `ctx_execute_file` | destructive, openWorld | path, language, code, timeout?, intent? | FILE_CONTENT var in-sandbox; same auto-index thresholds |
| `ctx_index` | additive, closedWorld | content? \| path?, source?, include?/exclude?/maxDepth?/maxFiles?/extensions?/respectGitignore?/followSymlinks? (dir-only) | chunk counts + source label + ctx_search call shape |
| `ctx_search` | readOnly, idempotent | queries (string[], preprocessed for double-serial), source?, contentType? ("code"\|"prose"), sort? ("relevance"\|"timeline"), limit?, project? (shared-DB only) | per-query ranked sections with window-extracted snippets + throttle counter |
| `ctx_fetch_and_index` | openWorld | url? \| requests:[{url,source?}], concurrency? (1-8), force?, ttl? (ms, 0=bypass) | per-source preview + indexing metadata; 24h TTL cache; SSRF-guarded |
| `ctx_batch_execute` | destructive, openWorld | commands:[{label,command}], queries:string[], timeout?, concurrency? (1-8), cwd?, query_scope? ("batch"\|"global") | commands inventory + indexed sections + per-query matches (batch or global scope) |
| `ctx_stats` | readOnly, idempotent | (none) | 5-section report: timeline, ladder, receipt, example cost, auto-memory; lifetime + multi-adapter |
| `ctx_doctor` | readOnly, idempotent | (none) | [OK]/[FAIL]/[WARN] diagnostics: runtimes, FTS5, hooks, storage, version |
| `ctx_upgrade` | idempotent, closedWorld | (none) | shell command to run upgrade |
| `ctx_purge` | destructive, idempotent | confirm (bool), sessionId?, scope? ("session"\|"project") | removed rows summary; per-session or per-project wipe |
| `ctx_insight` | — | (none) | (pivoted to hosted dashboard; legacy cache sweep on upgrade) |

### ContentStore Public API

```
class ContentStore {
  constructor(dbPath?: string)
  index({content?, path?, source?, attribution?}): IndexResult
  indexDirectory({path, source?, attribution?, perFileDeny?, ...WalkOptions}): DirResult
  indexPlainText(content, source, linesPerChunk?, attribution?, maxChunkBytes?): IndexResult
  indexJSON(content, source, maxChunkBytes?, attribution?): IndexResult
  search(query, limit?, source?, mode?, contentType?, sourceMatchMode?): SearchResult[]
  searchTrigram(query, limit?, source?, mode?, contentType?, sourceMatchMode?): SearchResult[]
  fuzzyCorrect(query): string | null
  searchWithFallback(query, limit?, source?, contentType?, sourceMatchMode?, sessionIdAllowSet?): SearchResult[]
  setDenyChecker(fn?: (filePath: string) => boolean): void
  getChunksBySource(sourceId): Chunk[]
  getSourceMeta(label): SourceMeta | null
  getDistinctiveTerms(sourceId): string[]
  getStats(): StoreStats
  listSources(): SourceListEntry[]
  cleanupStaleSources(maxAgeDays): number
  cleanup(): void
}
```

### SessionDB Public API

```
class SessionDB extends SQLiteBase {
  insertEvent(sessionId, event, sourceHook?, attribution?, bytes?): void
  bulkInsertEvents(sessionId, events, sourceHook?, attributions?, bytesList?): void
  getEvents(sessionId, opts?: {type?, minPriority?, limit?}): StoredEvent[]
  getEventCount(sessionId): number
  searchEvents(query, limit, projectDir?, category?): SearchResult[]
  ensureSession(sessionId, projectDir): void
  getSessionStats(sessionId): SessionMeta | null
  getSessionRollup(sessionId): SessionRollup
  upsertResume(sessionId, snapshot, eventCount): void
  getResume(sessionId): ResumeRow | null
  markResumeConsumed(sessionId): void
  claimLatestUnconsumedResume(sessionId): {session_id, snapshot} | null
  incrementCompactCount(sessionId): void
  deleteSession(sessionId): void
  cleanupOldSessions(maxAgeDays): void
  getSessionIdsForProject(projectDir): string[]
  close(): void
}
```

### Exported Functions (server.ts)

```
currentAttribution(): {sessionId?} | undefined
resolveSessionIdFromSessionDB(opts?): string | undefined
getProjectDir(): string
withProjectDirOverride(projectDir, fn): Promise<T>
sanitizeSchemaForStrictClients(node): unknown
installStrictClientSchemaCompat(target?): void
shouldSuppressMcpToolsForNativePluginHost(opts?): boolean
emitSuppressionDiagnostic(opts?): void
registerEmptyToolsListHandler(target?): void
classifyIp(rawIp): "block" | "private" | "public"  // SSRF
buildBatchNodeOptionsPrefix(shellPath, preloadPath): string
runBatchCommands(commands, opts, executor): Promise<BatchRunResult>
formatBatchQueryResults(store, queries, source, maxOutput?, scope?): string[]
extractSnippet(content, query, maxLen?, highlighted?): string
buildFetchCode(url, outputPath): string  // SSRF-guarded subprocess code
```

### Exported Functions (session/db.ts)

```
resolveSessionDbPath({projectDir, sessionsDir}): string
resolveContentStorePath({projectDir, contentDir}): string
resolveSessionPath({projectDir, sessionsDir, ext, suffix?}): string
resolveSessionStorageDir(getDefaultDir): ResolvedStorageDir
resolveContentStorageDir(getSessionDir): ResolvedStorageDir
resolveStatsStorageDir(getDefaultSessionDir): ResolvedStorageDir
resolveDefaultSessionDir(opts): string
ensureWritableStorageDir(dir): string  // throws StorageDirectoryError
hashProjectDirCanonical(projectDir): string
hashProjectDirLegacy(projectDir): string
getWorktreeSuffix(projectDir?): string
normalizeWorktreePath(path): string
applyMissingSessionEventsColumns(db): boolean
ensureSessionEventsSchema(dbPath, DatabaseCtor): void
```

### Exported Functions (security.ts)

```
evaluateCommandDenyOnly(command, policies, caseInsensitive?): {decision, matchedPattern?}
evaluateFilePath(filePath, denyGlobs[][], caseInsensitive?, projectRoot?): {denied, matchedPattern?}
readBashPolicies(projectDir?, globalSettingsPath?): SecurityPolicy[]
readToolDenyPatterns(toolName, projectDir?, globalSettingsPath?): string[][]
extractShellCommands(code, language): string[]
splitChainedCommands(command): string[]
extractSubshellCommands(command): string[]
globToRegex(glob, caseInsensitive?): RegExp
fileGlobToRegex(glob, caseInsensitive?): RegExp
```

### Hook Event Categories (26+)

Captured by PostToolUse into `session_events.category`:
file, bash_command, web_fetch, decision, error, error_resolution, plan,
skill, git_commit, external_ref, tool_failure, rejected-approach, redirect,
latency, compaction, session_start, session-resume, rule, user-prompt,
blocker, file_search, file_glob, tool_latency, compaction_summary,
snapshot-built, session_settings_snapshot.

---

## CONFIDENCE

**Overall: 0.92 (High)**

| Area | Confidence | Basis |
|------|-----------|-------|
| Hooks (PreToolUse/PostToolUse/PreCompact/SessionStart) | 0.95 | Full source of all 4 hook scripts + hooks.json |
| Sandbox tools (ctx_execute, ctx_batch_execute) | 0.95 | Full server.ts tool handlers + executor.ts |
| ContentStore (SQLite+FTS5) | 0.93 | Full store.ts (schema, indexing, search pipeline) |
| SessionDB | 0.90 | Full db.ts (schema, path resolution, migration); some methods truncated at end |
| Multi-strategy search (porter+trigram, RRF, proximity, fuzzy) | 0.95 | Full search pipeline code + flood-guard.ts + unified.ts |
| Security (deny firewall, SSRF, shell-escape) | 0.93 | Full security.ts |
| Platform adapters (15) | 0.70 | Referenced extensively in comments; adapter source not fetched |
| Auto-memory search | 0.65 | Referenced in unified.ts; auto-memory.ts not fetched |
| ctx_fetch_and_index SSRF | 0.90 | Full ssrfGuard + classifyIp + buildFetchCode in server.ts |
| Analytics / stats reporting | 0.80 | AnalyticsEngine referenced; analytics.ts not fetched |

**Gaps** (not fetched, would increase confidence):
- `src/session/analytics.ts` (AnalyticsEngine, formatReport, getLifetimeStats)
- `src/search/auto-memory.ts` (searchAutoMemory adapter)
- `src/adapters/detect.ts` + `src/adapters/types.ts` (15 platform adapters)
- `hooks/core/routing.mjs` (routePreToolUse decision logic)
- `src/runPool.ts` (parallel job pool primitive)
- `src/store-directory.ts` (walkDirectoryDetailed)
- `src/session/event-emit.ts` (emitSandboxExecuteEvent etc.)
- `src/db-base.ts` (SQLiteBase, loadDatabase, WAL pragmas)
- `hooks/userpromptsubmit.mjs`, `hooks/stop.mjs` (2 of 6 hook scripts)
- `src/search/ctx-search-schema.ts` (buildCtxSearchInputSchema)

**Methodology**: webfetch of raw GitHub files (main branch). server.ts was
truncated at ~50KB per read — read in 3 segments (lines 1-1199, 1200-2386,
2387-4532) covering ~4600 lines. store.ts read in 2 segments. db.ts read in
1 segment (truncated at end, ~14595 bytes cut — likely remaining public
methods like deleteSession, cleanupOldSessions, getSessionIdsForProject,
searchEvents, close). GitHub tree API returned 403 (rate limited) — file
list inferred from imports in fetched source.
