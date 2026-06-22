# Researcher 3 - Devil's Advocate Report
# stitch-design skill (google-labs-code/stitch-skills)

**Date:** 2026-06-22
**Source URL:** https://www.skills.sh/google-labs-code/stitch-skills/stitch-design
**Repo:** https://github.com/google-labs-code/stitch-skills
**Role:** Devil's Advocate - adversarial risk analysis
**Audits reviewed:** Snyk (Warn/MEDIUM), Socket (Pass), Gen Agent Trust Hub (Pass/SAFE with flags)

---

## SUMMARY

The stitch-design skill is a prompt-only agent skill (no compiled code beyond
two helper scripts) that wraps the **Google Stitch MCP cloud service** to
generate, edit, and migrate UI designs. It is **young** (66 commits, 2
releases, first seen Mar 13 2026, ~3 months old), **cloud-locked** (no offline
mode - every core operation round-trips stitch.googleapis.com), and carries a
**Snyk MEDIUM warning** for indirect prompt injection and unverifiable runtime
URL dependencies.

The skill is well-structured and thoughtfully designed (clear workflows,
explicit checkpoints, YAML-frontmatter contracts, rich reference docs), but
from an adversarial perspective it has real, exploitable surfaces:

1. **Architectural fragility** - a Python upload script exists *because the MCP
   protocol itself cannot move binary files* (model output token limit ~16K
   tokens vs. base64 payload of even a 53KB PNG = ~71K chars). This is a
   workaround for a protocol limitation, not a design choice, and it forces API
   keys onto the command line.
2. **Secret exposure** - the skill instructs the agent to read ~/.claude.json,
   ~/.gemini/settings.json, and other MCP config files to extract the Stitch
   API key, then pass it as --api-key on the shell (visible in process lists,
   shell history, agent tool-call logs).
3. **Prompt injection** - confirmed by Snyk (W011, risk 0.80) and Gen Agent
   Trust Hub: the design-synthesis workflow fetches untrusted HTML/screenshots
   via read_url_content and feeds them into the prompt pipeline with NO
   sanitization, NO delimiters, NO instruction-filtering.
4. **Lossy code-to-design** - the migration path strips all script tags,
   flattens conditionals, hardcodes data, and unrolls loops. Dynamic behavior
   is destroyed; only a static visual snapshot survives.
5. **Automation-breaking checkpoints** - at least four mandatory "pause and
   wait for user confirmation" gates make the skill unsuitable for
   unattended/headless agent pipelines.

Net: a useful interactive design assistant with meaningful security and
autonomy trade-offs that must be understood before adoption in any sensitive
or automated context. The project is explicitly "not an officially supported
Google product" and is "not eligible for the Google Open Source Software
Vulnerability Rewards Program."

---

## CRITICAL_ISSUES

### C1. API key exposure - skill reads MCP config files and passes key on CLI
**Severity: HIGH (security)**
**Confidence: HIGH (directly confirmed in SKILL.md + script source)**

The upload-to-stitch skill explicitly instructs the agent to locate and read
MCP server configuration files to extract the API key:

- Antigravity: .gemini/antigravity/mcp_config.json or .gemini/jetski/mcp_config.json
- Gemini CLI: ~/.gemini/settings.json or ~/.gemini/extensions/Stitch/gemini-extension.json
- Claude Code: ~/.claude.json

The agent extracts the key from the X-Goog-Api-Key header or auth argument,
then passes it as a command-line argument to the Python script:

    python3 upload_to_stitch.py --project-id X --file-path Y --api-key <API_KEY>

Problems:
- **CLI argument exposure**: --api-key on the command line is visible to any
  process via ps/tasklist, saved in shell history, and logged in agent
  tool-call transcripts. No use of env var, stdin, or a secrets file.
- **Over-broad file read**: ~/.claude.json contains ALL of Claude Code config,
  not just the Stitch key. The agent is told to read the entire file to find
  one key. Same for ~/.gemini/settings.json.
- **Key in conversation context**: if the key is not found in config files,
  the skill says "you MUST ask the user to provide the Stitch API key" - the
  key then enters the chat context and may be logged/transmitted.
- **Script prints sensitive data**: upload_to_stitch.py prints the API URL,
  project ID, and the first 1000 chars of the API response body to stdout,
  which flows into agent logs.
- **No redaction**: nowhere does the skill or script mention masking,
  redacting, or avoiding logging the key.

**Impact**: API key leakage to logs, process lists, shell history. In a
multi-tenant or shared-agent environment this is a credential exposure
incident waiting to happen.

---

### C2. Indirect prompt injection via untrusted fetched content (Snyk W011 + Trust Hub)
**Severity: HIGH (security)**
**Confidence: HIGH (confirmed by two independent audits + SKILL.md evidence)**

Snyk W011 (MEDIUM, risk 0.80): "The generate-design-md workflow explicitly
instructs the agent to call get_screen and then use read_url_content to fetch
HTML (htmlCode.downloadUrl) and screenshots from project asset URLs and to
analyze those user-generated HTML/screenshots to extract colors, geometry, and
other tokens that will directly drive design decisions."

Gen Agent Trust Hub (PROMPT_INJECTION flag): "instructions lack specific
delimiters or instructions to ignore embedded commands or comments within the
processed HTML content" and "no mention of sanitization, filtering, or
validation of the fetched HTML content prior to its analysis."

The manage-design-system SKILL.md confirms the ingestion chain:
1. get_screen -> screenshot.downloadUrl + htmlCode.downloadUrl
2. read_url_content to fetch the HTML code
3. synthesize into .stitch/DESIGN.md (the "source of truth")
4. DESIGN.md then "controls and enhances" all future generation prompts

A malicious HTML screen (uploaded by a collaborator, or a compromised asset)
containing hidden prompt-injection payloads (HTML comments, invisible text,
data attributes) would be fetched, analyzed, and its content folded into the
DESIGN.md - which then drives every subsequent design decision and prompt.
There is NO boundary marker, NO untrusted-data labeling, NO sanitization step.

**Impact**: poisoned design system, exfiltration via crafted prompts,
persistent corruption of the project's design "source of truth."

---

### C3. Unverifiable external runtime URLs control the agent (Snyk W012)
**Severity: HIGH (security)**
**Confidence: HIGH (Snyk W012, risk 0.90)**

Snyk W012 (MEDIUM, risk 0.90): "Potentially malicious external URL detected.
The skill explicitly fetches runtime assets via screenshot.downloadUrl and
htmlCode.downloadUrl (retrieved and fetched with read_url_content) and ingests
that remote HTML/screenshot content to synthesize the .stitch/DESIGN.md which
is then used to control and enhance prompts, so these runtime download URLs
are a high-confidence external dependency."

The download URLs are returned by the Stitch MCP tool at runtime - they are
not hardcoded or pinned. If the Stitch API is compromised, or a
man-in-the-middle intercepts, or the URL is tampered with, the agent will
fetch and ingest arbitrary content. Combined with C2 (no sanitization), this
is a full prompt-injection delivery path.

Additionally, the generate-design skill instructs the agent to download assets
via curl -o (run_command) from these same URLs into .stitch/designs - giving
the runtime URL control over what files land on disk.

**Impact**: remote code/content injection into the agent's context and local
filesystem via URLs the agent does not verify.

---

### C4. Token-limit workaround reveals MCP protocol fragility
**Severity: MEDIUM (architectural)**
**Confidence: HIGH (confirmed in script docstring + SKILL.md)**

The upload_to_stitch.py script exists because of a fundamental MCP limitation,
documented in its own docstring:

    "The AI model cannot upload files via the MCP tool directly because MCP
    tool call arguments are part of the model's OUTPUT. The model must re-emit
    the entire base64-encoded file as generated text, but its output token
    limit (~16K tokens) is far smaller than a typical file's base64 encoding
    (e.g. a 53KB PNG becomes ~71K chars of base64). The output gets truncated
    mid-string, producing a corrupted payload that the API rejects."

Implications:
- **Any file > ~12KB** (after base64) cannot be uploaded via the MCP tool
  directly. The workaround is a side-channel Python script that bypasses the
  model entirely.
- **Fragility**: if the MCP server changes its REST endpoint shape, auth
  scheme, or payload format, the script breaks silently. The script hardcodes
  the /v1/projects/{id}/screens:batchCreate endpoint and X-Goog-Api-Key auth.
- **Option B in manage-design-system** ("Direct MCP Tool" for DESIGN.md under
  ~5KB) confirms the MCP path only works for tiny payloads - the skill
  explicitly bifurcates based on file size.
- **No retry/backoff**: the script does a single urlopen with a 120s timeout.
  No exponential backoff, no retry on transient failures, no rate-limit
  awareness.

**Impact**: the "official" MCP integration path is broken by design for
real-world files; the skill relies on an undocumented REST side-channel that
is tightly coupled to the current Stitch API version.

---

### C5. Lossy code-to-design - scripts removed, dynamic behavior destroyed
**Severity: MEDIUM (functional fidelity)**
**Confidence: HIGH (confirmed in extract-static-html + code-to-design SKILL.md)**

The code-to-design migration path converts a running web app into a Stitch
design, but the conversion is fundamentally lossy:

1. **extract-static-html (Puppeteer snapshot)**: "Removes script tags, Vite
   overlay, Next.js dev indicators." All JavaScript is stripped. The output is
   a static DOM snapshot with inlined CSS and base64 images - no interactivity,
   no event handlers, no state management, no data fetching.
2. **MockPage.jsx fallback (last resort)**: explicitly requires:
   - "Flatten all conditionals - pick one state, remove all ternaries and &&
     guards"
   - "Hardcode all data - replace {variable} with concrete values, unroll
     .map() loops"
   - "Remove floating elements - cookie banners, chat widgets, feedback buttons"
   This is a manual destruction of all dynamic behavior to produce one frozen
   visual state.
3. **Strategy B (Browser Subagent)**: "Large pages may truncate" - to handle
   truncation, the skill says to "Remove style tags before extraction" and
   "Re-add styles statically" - further fidelity loss.
4. **No behavior preservation**: Stitch designs are static HTML/screenshots.
   There is no mechanism to preserve routing, state transitions, animations
   (beyond visual description), form validation, API calls, or conditional
   rendering. The design is a single-frame photograph of one UI state.

**Impact**: users may believe they are "migrating" their app to Stitch when
they are actually capturing a single static screenshot. The migration is
visual-only; all behavioral logic must be reimplemented. This is documented
but easy to misunderstand from the marketing ("Convert frontend code to a
Stitch Design").

---

### C6. User confirmation checkpoints break automated/headless pipelines
**Severity: MEDIUM (operational)**
**Confidence: HIGH (confirmed - 4 explicit checkpoint gates in SKILL.md)**

The skill contains multiple mandatory "Checkpoint - User Confirmation
Required" gates that halt execution until a human approves:

1. **extract-static-html**: "You MUST ask the user to choose which strategy to
   use before proceeding... wait for explicit approval. Do NOT make the
   decision yourself."
2. **extract-static-html (server start)**: "After starting the local server,
   you MUST pause and ask the user for confirmation before running the snapshot
   script... Do NOT proceed until the user confirms."
3. **upload-to-stitch**: "Before running the upload script, you MUST pause and
   present the file(s) to be uploaded (paths, sizes, and types) to the user and
   wait for explicit approval. Do NOT execute the upload script until the user
   confirms."
4. **manage-design-system**: "Before uploading, you MUST pause and ask the user
   for confirmation. Present a summary of the design system... wait for
   explicit approval. Do NOT upload until the user confirms."

In a non-interactive/headless environment (CI, batch agent, autonomous
pipeline), these gates cause the agent to stall indefinitely - there is no
"auto-approve" flag, no --yes bypass, no config to disable checkpoints. The
code-to-design chain (which orchestrates 3 sub-skills) will hit at least 2 of
these gates, making the full migration pipeline impossible to run unattended.

**Impact**: the skill is interactive-only. Any automation use case requires
forking the skill to remove/disable checkpoints, which defeats the
"install-and-go" value proposition.

---

### C7. No offline mode - hard cloud dependency on stitch.googleapis.com
**Severity: MEDIUM (operational)**
**Confidence: HIGH (confirmed in README + all SKILL.md files + script)**

Every core capability requires the Stitch MCP server (cloud):
- README: "These skills require the Stitch MCP server to be configured and
  running in your agent's environment."
- All 6 design skills declare allowed-tools: ["stitch*:*"] - every tool is a
  Stitch MCP tool.
- generate-design: generate_screen_from_text, edit_screens, generate_variants,
  list_projects, list_screens - all cloud calls.
- upload_to_stitch.py: hardcodes https://stitch.googleapis.com as the default
  API URL; all uploads are HTTP POST to the cloud.
- manage-design-system: create_design_system_from_design_md, apply_design_system
  - cloud only.

The only locally-executable parts are:
- extract-design-md (reads source files - but output is useless without Stitch
  to upload it to)
- extract-static-html (Puppeteer snapshot - but output is useless without
  Stitch to upload it to)

There is no local design-generation engine, no offline cache, no degraded
mode. If stitch.googleapis.com is down, rate-limits you, or your network is
offline, the entire skill is dead.

**Impact**: zero resilience to network/API outages. Rate limits or API key
quota exhaustion halts all work. No air-gapped usage possible.

---

### C8. Young project - 66 commits, 2 releases, 10 open issues, ~3 months old
**Severity: MEDIUM (maturity/supply-chain)**
**Confidence: HIGH (confirmed via GitHub repo page)**

- **Age**: first seen Mar 13 2026; v0.1 released Mar 4 2026; v1.0 released
  May 18 2026. ~3 months old at time of research.
- **Commits**: 66 total - small history, limited battle-testing.
- **Releases**: 2 (v0.1, v1.0). v1.0 is the first "real" release and it
  reorganized the entire structure into 3 plugins - meaning the API/skill
  surface is still churning.
- **Issues**: 10 open, 8 open PRs - non-trivial backlog for a 66-commit repo.
- **Contributors**: primarily one person (JLXIA). Single-maintainer bus factor.
- **Official support**: README explicitly states "This is not an officially
  supported Google product" and "not eligible for the Google Open Source
  Software Vulnerability Rewards Program." Despite the google-labs-code org,
  there is no Google security backing.
- **Installs**: 25.3K (high adoption for a young project = many users exposed
  to the risks above).

**Impact**: high probability of breaking changes, unpatched issues, and
supply-chain risk. No LTS guarantee. Single-maintainer dependency for a tool
that handles API keys and fetches untrusted content.

---

### C9. Additional adversarial observations

**C9a. web_fetch in allowed-tools grants network egress**
All skills include "web_fetch" in allowed-tools, giving the agent general
network egress beyond the Stitch MCP - used for read_url_content on arbitrary
downloadUrls. This expands the attack surface: a crafted downloadUrl could
point to any host, not just stitch.googleapis.com.

**C9b. Inter-skill dependency chain fragility**
code-to-design orchestrates extract-static-html -> extract-design-md ->
upload-to-stitch -> manage-design-system in sequence. A failure at any step
(leaving a half-migrated state) has no documented rollback. The .stitch/
directory may contain a DESIGN.md without the corresponding uploaded screens,
or vice versa.

**C9c. SSL fallback weakness**
upload_to_stitch.py: if certifi is not installed, _SSL_CONTEXT is set to None
and the urlopen call omits the context parameter, falling back to urllib's
default CA bundle. On systems with missing/broken CA bundles (the SKILL.md
itself documents macOS SSL failures), this could silently disable certificate
verification or fail opaquely.

**C9d. No input validation on project IDs or file paths**
The script does no validation on --project-id (could be any string) or
--file-path (no path traversal protection, though it is local). The MIME
detection is extension-based only - a renamed malicious file would be
uploaded with whatever MIME the extension maps to.

**C9e. print() statements leak operational data**
The script prints file path, MIME type, base64 length, project ID, API URL,
and response body to stdout. In agent contexts, stdout is captured and may be
logged, transmitted to model providers, or shown in UI - leaking operational
metadata.

**C9f. Generated-by field is attacker-influenced**
The --generated-by argument is written into the screen's generatedBy field.
Since the agent constructs this from skill names, and the skill chain can be
influenced by prompt injection (C2), an attacker could manipulate the
provenance tracking metadata.

---

## CONFIDENCE

**Overall confidence: HIGH**

All critical issues are backed by direct primary-source evidence:
- SKILL.md files fetched from the canonical GitHub raw URLs (code-to-design,
  generate-design, extract-static-html, extract-design-md,
  manage-design-system, upload-to-stitch)
- upload_to_stitch.py source code read in full
- Snyk audit page (W011, W012 with risk scores)
- Gen Agent Trust Hub audit page (PROMPT_INJECTION, COMMAND_EXECUTION,
  EXTERNAL_DOWNLOADS flags)
- Socket audit page (Pass)
- GitHub repo page (66 commits, 2 releases, 10 issues, 8 PRs, 6.1K stars)
- GitHub releases page (v0.1 Mar 4, v1.0 May 18)

**Evidence gaps (lower confidence items):**
- The main "Stitch Design Expert" SKILL.md shown on skills.sh was truncated
  ("Show more"); the full workflows section was not retrieved. The sub-skill
  SKILL.md files were retrieved in full and are the actual executable
  instructions, so this gap is low-impact.
- The Stitch MCP setup docs page (stitch.withgoogle.com/docs/mcp/setup)
  returned only a title ("Stitch - Design with AI") with no body - the exact
  MCP server configuration requirements could not be independently verified,
  but the SKILL.md files document the config file locations comprehensively.
- Could not retrieve the full repo file tree (GitHub API returned 403 rate
  limit), so there may be additional scripts or resources not inventoried.

**Confidence per issue:**
- C1 (API key exposure): HIGH - direct SKILL.md instruction + script source
- C2 (prompt injection): HIGH - Snyk W011 + Trust Hub + SKILL.md chain
- C3 (unverifiable URLs): HIGH - Snyk W012
- C4 (token-limit workaround): HIGH - script docstring + SKILL.md NOTE
- C5 (lossy code-to-design): HIGH - extract-static-html + MockPage rules
- C6 (checkpoint gates): HIGH - 4 explicit "MUST pause" blocks in SKILL.md
- C7 (no offline mode): HIGH - README + all SKILL.md + script
- C8 (young project): HIGH - GitHub repo/releases pages
- C9 (additional): MEDIUM-HIGH - inferred from script source + tool lists
