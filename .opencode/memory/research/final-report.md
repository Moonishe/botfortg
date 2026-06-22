# DEEP RESEARCH FINAL REPORT
# 11 Repositories × 5 Researchers = 55 Reports, Iteration-02
# Target: TelegramHelper v2.0 Recommendations
# Date: 2026-06-22

## Executive Summary

Проведено глубокое исследование 11 проектов (55 исследователей, 2 итерации). Изучены
архитектура, паттерны, инновации и риски каждого. Сопоставлено с существующим кодом
TelegramHelper (grep по src/, проверка memory.md AD-001..AD-020).

**Результат:** TelegramHelper — зрелый проект с 20 архитектурными решениями, 12 навыками,
Zero-Risk Pipeline, Max Mode, Ponytail, Goal Judge. Многие «инновации» исследованных
проектов у нас УЖЕ ЕСТЬ. Выявлено 12 конкретных gap'ов и 18 рекомендаций.

---

## Что УЖЕ ЕСТЬ в TelegramHelper (confirmed via grep)

| Возможность | Файл в TH | Аналог в исследованных проектах |
|-------------|-----------|-------------------------------|
| ToolRegistry singleton | `tool_registry.py` | hermes-agent ToolRegistry |
| CommandRegistry | `command_registry.py` | hermes-agent CommandDef |
| Pairing/allowlist security | `pairing.py` | openclaw dmPolicy |
| Security audit command | `audit.py` + `audit_cmd.py` | openclaw security audit |
| Memory correction/feedback | `memory_correction.py` + `smart_reply.py` | MemOS SimpleMemFeedback |
| Session snapshots | `session_snapshot.py` | context-mode PreCompact |
| Streaming preview edits | `streaming_edit_interval` in config.py | hermes-agent streaming scrubber |
| Sandbox execute (Docker) | `sdd_executor.py` | hermes-agent terminal backends |
| MCP shell allowlist | `mcp_shell.py` | context-mode deny-only firewall |
| MemoryProvider | `memory_provider.py` | hermes-agent MemoryProvider ABC |
| Skills system | `.opencode/skills/` (12 skills) | hermes-agent/open-design skills |
| Hybrid RRF search | AD-010 (RRF search) | MemOS/context-mode hybrid retrieval |
| Goal Judge | AD-014 | karpathy Goal-Driven Execution |
| Max Mode | AD (Max Mode) | hermes-agent skill_manage |
| Ponytail (YAGNI) | Ponytail plugin (full mode) | karpathy Simplicity First |
| Zero-Risk Pipeline (D5→R5) | Zero-Risk Pipeline | karpathy Goal-Driven + loop |
| Approval Kernel (HMAC) | AD-017 | open-design HMAC folder-import gate |
| Cron Panel | AD-018 | openclaw cron + hermes-agent cronjob |
| Overlap guard | AD-008 | hermes-agent background subagents |
| LazyModule | AD-013 | hermes-agent lazy provider extras |
| Self-management tools | AD-015 | hermes-agent agent skill creation |
| MCP Expose auto-discovery | AD-016 | hermes-agent MCP auto-discovery |

**Вывод:** ~70% «инноваций» исследованных проектов уже реализовано в TelegramHelper.
Это подтверждает зрелость архитектуры проекта.

---

## 12 GAP'ов: что ЕЩЁ НЕТ в TelegramHelper

### GAP-1: Group/Topic Mention Gating
**Откуда:** openclaw (groupPolicy + groups allowlist + per-topic agentId routing)
**Суть:** В openclaw групповая политика отделена от DM-политики. Каждый супергруппа
может иметь свой agentId для маршрутизации к разным агентам. Per-topic routing
позволяет разным топикам в одной группе направляться к разным конфигурациям.
**У нас:** `pairing.py` обрабатывает DM pairing/allowlist, но НЕТ отдельной
group-политики с per-topic маршрутизацией.

### GAP-2: Toolsets Composition (resolve_toolset)
**Откуда:** hermes-agent (TOOLSETS dict + resolve_toolset() recursive with cycle detection)
**Суть:** Именованные группы инструментов (`hermes-telegram`, `hermes-cli`, `web`,
`terminal`, `coding`) с композицией через `includes`. `resolve_toolset()` рекурсивно
разрешает зависимости с детекцией циклов.
**У нас:** `tool_registry.py` регистрирует инструменты, но НЕТ именованных toolsets
с композицией и зависимостями. Есть route profiles, но не декларативные toolsets.

### GAP-3: "Think in Code" Paradigm
**Откуда:** context-mode (ctx_execute — LLM пишет скрипты вместо чтения данных)
**Суть:** Вместо 47× Read() = 700KB контекста, LLM пишет ОДИН ctx_execute() скрипт
(3.6KB). Код выполняется в изолированном subprocess, только stdout входит в контекст.
98% экономии контекста.
**У нас:** НЕТ. Инструменты читают данные напрямую в контекст.

### GAP-4: Context Compression (PreCompact snapshots)
**Откуда:** context-mode (priority-tiered <2KB snapshots перед compaction)
**Суть:** Перед сжатием контекста система делает приоритетный снапшот (<2KB) с
ключевыми событиями (файлы, задачи, git, ошибки, решения, блокеры). После сжатия
снапшот инъектируется как "Session Guide" с 15 категориями.
**У нас:** Есть checkpoint-writer (§1-§11), но НЕТ автоматического PreCompact-хука.
DCP делает сжатие, но без priority-tiered snapshot в момент сжатия.

### GAP-5: DESIGN.md for Telegram UI
**Откуда:** open-design (9-section DESIGN.md), design.md (YAML tokens + markdown),
effective-html (design system: ivory/slate/clay palette)
**Суть:** Единый файл контракта для визуального дизайна. У open-design 9 секций
(color, typography, spacing, layout, components, motion, voice, brand, anti-patterns).
У design.md YAML-токены + markdown-рационале. У effective-html — конкретная палитра.
**У нас:** Есть `visual_tokens.py`, `rich_messages.py`, `smart_keyboard.py` — но
РАЗРОЗНЕННО. НЕТ единого документа контракта для Telegram UI.

### GAP-6: craft/ Pattern (Universal Brand-Agnostic Rules)
**Откуда:** open-design (craft/ — третий axis помимо skills и design-systems)
**Суть:** Brand-agnostic универсальные правила (typography.md, color.md,
anti-ai-slop.md, accessibility-baseline.md). Skills opt in через `od.craft.requires`.
Pipeline injects craft sections BETWEEN design-system и skill body.
**У нас:** НЕТ. Правила разбросаны по AGENTS.md, rules.md, constitution.

### GAP-7: Prompt Enhancement Pipeline
**Откуда:** stitch-design (4-step: Analyze Context → Refine Terminology →
Structure Prompt → Present AI Insights)
**Суть:** Vague пользовательский запрос → структурированный spec. Использует
design-mappings.md + prompt-keywords.md для терминологической трансляции.
**У нас:** НЕТ. Пользовательские запросы идут напрямую к LLM без препроцессинга.

### GAP-8: Think Before Coding Rule
**Откуда:** karpathy-skills (Principle 1)
**Суть:** Перед кодированием: state assumptions explicitly, present multiple
interpretations if 2+ exist, push back if simpler approach exists, STOP and ask
if unclear.
**У нас:** НЕТ явно. Explorer agent частично покрывает, но нет правила "state
assumptions before coding" в system prompt.

### GAP-9: Surgical Changes Rule
**Откуда:** karpathy-skills (Principle 3)
**Суть:** Touch only what you must. Match existing style. Mention dead code —
don't delete. Every changed line should trace to user request. Diff self-review
before "done".
**У нас:** Ponytail "shortest working diff wins" частично покрывает (~30%), но
НЕТ правила "every changed line traces to request" и diff self-review checklist.

### GAP-10: HTML Report/Diagram Skills
**Откуда:** effective-html (html, html-diagram, html-plan skills)
**Суть:** Генерация self-contained HTML для отчётов, диаграмм архитектуры,
планов. Dark mode support. SVG via CSS classes.
**У нас:** НЕТ. Отчёты в Telegram — текст/markdown. НЕТ генерации HTML-отчётов
или архитектурных диаграмм.

### GAP-11: Pluggable MemoryProvider ABC
**Откуда:** hermes-agent (MemoryProvider ABC with lifecycle hooks)
**Суть:** ABC с методами: is_available, initialize, prefetch, sync_turn,
on_turn_start, on_pre_compress, on_session_switch, on_memory_write,
on_delegation, get_config_schema, save_config, backup_paths. At most ONE
external provider. Background ThreadPoolExecutor for sync.
**У нас:** `memory_provider.py` — конкретный класс, НЕ ABC. НЕТ lifecycle hooks
(on_pre_compress, on_session_switch, on_delegation). НЕТ background sync thread.

### GAP-12: Tool Routing Whitelist for Bash
**Откуда:** context-mode (deny-only firewall + shell-escape scanner for 8 langs)
**Суть:** deny-only evaluation: deny wins, project overrides global, chained
commands split, subshell extraction, shell-escape scanner.
**У нас:** `mcp_shell.py` имеет allowlist, но НЕТ shell-escape scanner и
subshell extraction.

---

## 18 Рекомендаций (ранжированы по приоритету)

### ПРИОРИТЕТ 1 — Высокая ценность, низкие усилия

#### R1: Добавить Think Before Coding правило в AGENTS.md
**Источник:** karpathy-skills Principle 1
**Gap:** GAP-8
**Усилия:** ~30 мин (редактирование AGENTS.md)
**Ценность:** Средняя — ловит 20-30% багов на этапе формулирования
**Что делать:**
Добавить секцию в AGENTS.md после "Coding Constraints":
```markdown
### Think Before Coding (для Moderate+ задач)
Перед имплементацией нетривиальной задачи:
1. State assumptions explicitly — запиши все допущения
2. If 2+ interpretations exist — enumerate with effort estimates, STOP for user choice
3. If simpler approach exists — name it, push back
4. If unclear — STOP and ask, не угадывай
```
**Конфликты:** НЕТ. Дополняет Explorer agent (Explorer ищет, Think формулирует).
**Ponytail:** Совместимо — Think для Moderate+, Tiny/Simple skip.

#### R2: Добавить Surgical Changes + Diff Self-Review в R5
**Источник:** karpathy-skills Principle 3
**Gap:** GAP-9
**Усилия:** ~1 час (обновление review-maintainability.md)
**Ценность:** Высокая — ловит drive-by edits, style drift, unrequested changes
**Что делать:**
Добавить в review-maintainability.md обязательный checklist:
```markdown
### Diff Self-Review (обязателен перед "done")
Для каждой изменённой строки:
- Traceable to user request? → если нет и drive-by → REVERT
- Style match? (quotes, type hints, docstrings, whitespace) → если drift → REVERT
- Orphan from YOUR change? → keep (remove only your orphans)
- Pre-existing dead code touched? → MENTION in RISKS, don't delete
```
**Конфликты:** "Don't delete dead code" конфликтует с Ponytail "deletion over addition".
**Разрешение:** Ponytail побеждает для Tiny/Simple. Surgical побеждает для Moderate+
(не трогай чужой код, но можешь удалить СВОЁ). Добавить комментарий в AGENTS.md.

#### R3: Создать docs/DESIGN.md для Telegram UI
**Источник:** open-design (9-section schema), design.md (YAML tokens), effective-html (palette)
**Gap:** GAP-5
**Усилия:** ~2 часа (консолидация существующих файлов)
**Ценность:** Средняя — единый контракт для UI решений
**Что делать:**
Создать `docs/DESIGN.md` консолидирующий:
- Emoji vocabulary из `visual_tokens.py` (токены → названия)
- Message structure patterns из `rich_messages.py` (лимиты, форматирование)
- Keyboard conventions из `smart_keyboard.py` (паттерны кнопок)
- Formatting rules (markdown limits per Telegram client)
- Anti-patterns (что НЕ делать в Telegram UI)
Структура по образцу open-design (9 секций, адаптированных для Telegram):
1. Visual Theme & Atmosphere (tone бота)
2. Emoji & Icon Vocabulary (токены)
3. Typography & Formatting (markdown limits)
4. Message Structure (patterns)
5. Keyboard Patterns (conventions)
6. Component Stylings (cards, lists, alerts)
7. Layout Principles (message length, chunking)
8. Do's and Don'ts
9. Agent Prompt Guide (для LLM-генерации сообщений)

### ПРИОРИТЕТ 2 — Высокая ценность, средние усилия

#### R4: Добавить Group/Topic Mention Gating
**Источник:** openclaw (groupPolicy + per-topic agentId routing)
**Gap:** GAP-1
**Усилия:** ~4-6 часов
**Ценность:** Высокая — безопасность в группах, маршрутизация к разным конфигам
**Что делать:**
1. Расширить `pairing.py` отдельной group-политикой:
   - `group_policy`: pairing/allowlist/open/disabled (отдельно от DM)
   - `groups_allowlist`: список разрешённых supergroup IDs
   - Per-topic `agent_id`: маршрутизация к разным конфигам
2. Добавить в config.py: `group_policy`, `groups_allowlist`, `topic_routing`
3. В handler'ах групповых сообщений проверять group_policy BEFORE processing
4. Тесты: group sender auth does NOT inherit DM pairing (security boundary)

#### R5: Добавить Toolsets Composition
**Источник:** hermes-agent (TOOLSETS + resolve_toolset)
**Gap:** GAP-2
**Усилия:** ~3-4 часа
**Ценность:** Средняя — декларативная конфигурация инструментов
**Что делать:**
1. Создать `toolsets.py` с TOOLSETS dict:
   ```python
   TOOLSETS = {
       "core": {"tools": ["search", "memory", "clarify"]},
       "telegram": {"includes": ["core"], "tools": ["send_message", "send_keyboard"]},
       "admin": {"includes": ["core"], "tools": ["audit", "config", "cron"]},
       "all": {"includes": ["telegram", "admin"]},
   }
   ```
2. `resolve_toolset(name, visited)` — рекурсивно с cycle detection
3. В config.py: `enabled_toolsets: list[str]` вместо `enabled_tools: list[str]`
4. ToolRegistry.get_definitions(tool_names) — уже есть, использовать

#### R6: Добавить Surgical Changes в D5 Debugger 5 (Integration & Cleanliness)
**Источник:** karpathy-skills Principle 3 + наш D5
**Gap:** GAP-9 (дополнение к R2)
**Усилия:** ~30 мин (обновление промпта debugger 5)
**Ценность:** Высокая — ловит drive-by edits на этапе D5
**Что делать:**
В промпт Debugger 5 добавить:
```
Surgical Changes Check:
- Для каждой изменённой строки в diff: traceable to user request?
- Drive-by style changes? (quotes, type hints, whitespace) → flag
- Pre-existing dead code touched? → flag, don't delete
- Orphaned imports from YOUR change removed? → verify
```

#### R7: Сделать MemoryProvider настоящим ABC
**Источник:** hermes-agent MemoryProvider ABC
**Gap:** GAP-11
**Усилия:** ~4-6 часов
**Ценность:** Средняя — расширяемость memory layer
**Что делать:**
1. Рефакторить `memory_provider.py` → ABC с lifecycle hooks:
   - `is_available() -> bool` (abstract)
   - `initialize(session_id, **kwargs)` (abstract)
   - `prefetch(query) -> str`
   - `sync_turn(user, assistant, messages)` 
   - `on_turn_start(turn_number, message)`
   - `on_pre_compress(messages) -> str` ← НОВОЕ
   - `on_session_switch(new_session_id, reset)` ← НОВОЕ
   - `on_delegation(task, result, child_session_id)` ← НОВОЕ
   - `get_config_schema() -> list[dict]`
   - `save_config(values)`
   - `backup_paths() -> list[str]`
2. Текущая реализация → `QdrantMemoryProvider(MemoryProvider)`
3. Background sync через `asyncio.to_thread` (не ThreadPoolExecutor — у нас async)
4. At most ONE external provider (second rejected with warning)

### ПРИОРИТЕТ 3 — Средняя ценность, средние усилия

#### R8: Добавить PreCompact Hook для Context Compression
**Источник:** context-mode (priority-tiered <2KB snapshots)
**Gap:** GAP-4
**Усилия:** ~3-4 часа
**Ценность:** Средняя — лучше реконструкция контекста после DCP
**Что делать:**
1. Добавить opencode.json hook: `experimental.session.compacting` → trigger
2. Перед DCP-сжатием генерировать priority-tiered snapshot (<2KB):
   - Files touched (top-5)
   - Active tasks (from todowrite)
   - Key decisions (last 3)
   - Errors/blockers (last 2)
   - Git state (branch, uncommitted)
3. Snapshot сохраняется в `.opencode/memory/compact-snapshot.json`
4. После сжатия инъектируется как "Session Guide" в system prompt
**Связь:** Дополняет checkpoint-writer (checkpoint = полный §1-§11, snapshot = priority <2KB)

#### R9: Добавить Prompt Enhancement для пользовательских запросов
**Источник:** stitch-design (4-step pipeline)
**Gap:** GAP-7
**Усилия:** ~3-4 часа
**Ценность:** Средняя — лучшие результаты от LLM
**Что делать:**
1. Создать `prompt_enhancer.py`:
   - Анализ контекста (что уже известно)
   - Терминологическая трансляция (vague → structured)
   - Структурирование (PLATFORM/STRUCTURE/CONTENT template)
2. Для Moderate+ задач: enhance перед отправкой к LLM
3. Для Tiny/Simple: skip (Ponytail)
**Адаптация для Telegram:** mapping web-terms → Telegram-terms
(navigation bar → inline keyboard, card grid → numbered list, form → FSM state)

#### R10: Добавить Trivial Task Bypass в Progressive Complexity Router
**Источник:** karpathy-skills (CLAUDE.md: "for trivial tasks, use judgment")
**Gap:** дополнение к existing router
**Усилия:** ~15 мин
**Ценность:** Низкая, но улучшает UX — не применять heavy pipeline к опечаткам
**Что делать:**
В main.md §0 (Progressive Complexity Router) под Tiny добавить:
```markdown
### Tiny Task Bypass
Для Tiny задач (1 файл, <50 строк):
- SKIP: Think Before Coding enumeration
- SKIP: Simplicity First rewrite check
- KEEP: Surgical Changes (всегда)
- KEEP: Goal-Driven (тест + typecheck)
- KEEP: Diff self-review
```

#### R11: Добавить Dead-Code Flagging в OUTPUT CONTRACT
**Источник:** karpathy-skills (Surgical Changes: "mention don't delete")
**Gap:** дополнение к OUTPUT CONTRACT
**Усилия:** ~10 мин
**Ценность:** Низкая, но систематизирует техдолг
**Что делать:**
В OUTPUT CONTRACT формат добавить в RISKS:
```
RISKS:
- Dead code: <file>:<line> <symbol> — unused, not deleted (Surgical policy)
```

#### R12: Cross-reference Ponytail ↔ Karpathy Simplicity
**Источник:** karpathy-skills Principle 2 ≈ Ponytail
**Gap:** устранение дублирования
**Усилия:** ~10 мин
**Ценность:** Низкая — документация
**Что делать:**
В AGENTS.md под Ponytail секцией добавить:
```markdown
**Примечание:** Simplicity First (Karpathy §2) ≈ Ponytail YAGNI (~90% overlap).
Оба источника валидны для R5 reviewers.
```

#### R13: Cross-reference Zero-Risk Pipeline ↔ Karpathy Goal-Driven
**Источник:** karpathy-skills Principle 4 ≈ Zero-Risk Pipeline
**Gap:** устранение дублирования
**Усилия:** ~10 мин
**Ценность:** Низкая — документация
**Что делать:**
В AGENTS.md под Zero-Risk Pipeline добавить:
```markdown
**Примечание:** Goal-Driven Execution (Karpathy §4) ≈ Zero-Risk Pipeline + Goal Judge
(~70% overlap). Karpathy добавляет: "state verifiable success criterion BEFORE
implementation". Добавлено в Goal Judge prompt (R3).
```

### ПРИОРИТЕТ 4 — Низкая ценность или высокие усилия

#### R14: "Think in Code" Paradigm (ОТЛОЖЕНО)
**Источник:** context-mode (ctx_execute)
**Gap:** GAP-3
**Усилия:** ~20+ часов (новая инфраструктура)
**Ценность:** Высокая, но слишком масштабно для сейчас
**Что делать:** ОТЛОЖИТЬ. Требует sandbox executor, FTS5 indexing, session DB.
Запустить как отдельный epic, если контекст станет узким местом.
**Ponytail:** YAGNI — пока DCP + checkpoint справляются, не нужно.

#### R15: HTML Report/Diagram Skills (ОПЦИОНАЛЬНО)
**Источник:** effective-html (html, html-diagram, html-plan)
**Gap:** GAP-10
**Усилия:** ~4-6 часов на skill
**Ценность:** Средняя — красивые отчёты, но Telegram = текст/markdown
**Что делать:**
Опционально — создать `.opencode/skills/html-report/SKILL.md` для генерации
HTML-отчётов (статус, аудит, метрики). HTML → Playwright PNG → send_photo.
**Ponytail:** Нужна ли это? Telegram-клиенты плохо рендерят HTML. Markdown-таблицы
непоследовательны. Если отчёты нужны — сделать. Если нет — YAGNI.

#### R16: craft/ Pattern (ОТЛОЖЕНО)
**Источник:** open-design (craft/ universal rules)
**Gap:** GAP-6
**Усилия:** ~8+ часов
**Ценность:** Низкая — у нас есть AGENTS.md + rules.md + constitution
**Что делать:** ОТЛОЖЕНО. У нас уже трёхуровневая система правил
(constitution > AGENTS.md > rules.md). craft/ — четвёртый уровень, избыточен.

#### R17: Tool Routing Whitelist Enhancement (ОПЦИОНАЛЬНО)
**Источник:** context-mode (shell-escape scanner)
**Gap:** GAP-12
**Усилия:** ~4 часа
**Ценность:** Средняя — security enhancement
**Что делать:**
Опционально — добавить shell-escape scanner в `mcp_shell.py`:
- Subshell extraction (`$(...)`, backticks)
- Chained command splitting (`;`, `&&`, `||`, `|`)
- Shell-escape patterns для 8 языков
**Приоритет:** Повысить если будут security-инценденты.

#### R18: Skill Bundles (ОПЦИОНАЛЬНО)
**Источник:** hermes-agent (skill bundles — group multiple skills)
**Gap:** нет в списке (new)
**Усилия:** ~2-3 часа
**Ценность:** Низкая — у нас 12 skills, группировка избыточна
**Что делать:** ОТЛОЖЕНО. Когда skills станет 20+ — пересмотреть.

---

## Anti-Recommendations (что НЕ внедрять)

### A1: НЕ добавлять Stitch MCP
**Причина:** Medium mismatch. Stitch генерирует web HTML screens. TelegramHelper
работает в Telegram (текст, markdown, inline keyboards, photos). HTML screens
не применимы к Telegram UI.
**Исключение:** Если добавить HTML→PNG→send_photo pipeline для дашбордов.

### A2: НЕ копировать openclaw shared-secret auth
**Причина:** All-or-nothing security (x-openclaw-scopes silently ignored).
У нас уже есть pairing + allowlist + HMAC Approval Kernel (AD-017) — лучше.

### A3: НЕ копировать MemOS auth (точнее, отсутствие auth)
**Причина:** MemOS имеет ZERO auth на всех endpoints. Это CRITICAL vulnerability.
У нас уже есть security model — не копируем их ошибки.

### A4: НЕ копировать karpathy EXAMPLES.md (удаление type hints)
**Причина:** EXAMPLES.md показывает удаление type hints как "correct" Surgical
Changes. Это КОНФЛИКТУЕТ с AGENTS.md: "Type annotations on ALL public functions"
(pyright strict mode). НЕ ПРИМЕНЯТЬ.

### A5: НЕ копировать context-mode "sandbox"
**Причина:** "Sandbox" = arbitrary code execution with full host access (не
настоящий sandbox). У нас уже есть `sdd_executor.py` с Docker — лучше.

### A6: НЕ копировать frontend-design skill напрямую
**Причина:** Pure web UI skill. TelegramHelper — Telegram бот, не web app.
Принципы (plan-first, anti-AI-slop, token system) — можно адаптировать (R3),
но не устанавливать upstream.

### A7: НЕ копировать MemOS sync handlers + blocking I/O
**Причина:** MemOS использует sync `def` handlers с blocking LLM/vector I/O.
У нас ВСЁ async/await (protected invariant). НЕ нарушать.

### A8: НЕ копировать open-design permission bypass
**Причина:** open-design настраивает Devin `--permission-mode dangerous`,
Trae `--yolo`, DeepSeek `--auto`. У нас Zero-Risk Pipeline + D5→R5 — не отключать.

---

## Anti-Patterns выявленные у исследованных проектов (для нашего R5)

| Anti-pattern | Где найдено | У нас |
|--------------|-------------|-------|
| God-file 850KB | hermes-agent gateway/run.py | НЕ допустить — D5 Debugger 5 проверяет |
| Fail-open security | hermes-agent credential blocklist, context-mode hooks | У нас fail-closed (pairing, allowlist) |
| Zero auth на API | MemOS (все 19 endpoints) | У нас есть auth |
| Hardcoded credentials | MemOS (neo4j/12345678) | У нас .env + pydantic-settings |
| Sync I/O in async framework | MemOS sync handlers | У нас async/await (protected) |
| Permission bypass flags | open-design (dangerous/yolo/auto) | У нас Zero-Risk Pipeline |
| "Sandbox" = full host access | context-mode ctx_execute | У нас Docker sandbox |
| Silent data loss in export | design.md (drops custom tokens) | N/A (не экспортируем токены) |
| No LICENSE despite claiming MIT | karpathy-skills | У нас есть LICENSE |
| Removing type hints as "correct" | karpathy EXAMPLES.md | ЗАПРЕЩЕНО в AGENTS.md |

---

## Source Map (55 researchers, 11 repos)

| Репозиторий | R1 Bird's Eye | R2 Deep Dive | R3 Devil's Advocate | R4 Historian | R5 Practitioner |
|-------------|---------------|--------------|---------------------|--------------|-----------------|
| hermes-agent | ✅ high | ✅ high | ✅ med-high | ✅ high | ✅ high |
| openclaw | ✅ high | ✅ high | ✅ high | ✅ high | ✅ high |
| MemOS | ✅ high | ✅ high | ✅ high | ✅ high | ✅ high |
| effective-html | ✅ high | ✅ high | ✅ high | ✅ high | ✅ high |
| karpathy-skills | ✅ high | ✅ high | ✅ high | ✅ high | ✅ high |
| context-mode | ✅ high | ✅ 0.92 | ✅ high | ✅ high | ✅ 0.90 |
| open-design | ✅ 0.85 | ✅ high | ✅ high | ✅ high | ✅ high |
| design.md | ✅ high | ✅ 0.92 | ✅ high | ✅ high | ✅ high |
| frontend-design | ✅ 0.88 | ✅ high | ✅ 0.85 | ✅ high | ✅ high |
| stitch-design-md | ✅ 0.90 | ✅ 0.95 | ✅ 0.82 | ✅ high | ✅ 0.85 |
| stitch-design | ✅ 0.88 | ✅ high | ✅ high | ✅ high | ✅ high |

**Общая confidence:** HIGH. Все 55 отчётов основаны на первичных источниках
(GitHub raw, API, live pages). Все сырые данные сохранены в
`.opencode/memory/research/<repo>/iteration-02/raw/`.

---

## Confidence Assessment

- **Самые надёжные находки:** R1 (Think Before Coding), R2 (Surgical Changes),
  R3 (DESIGN.md), R4 (Group gating), R5 (Toolsets) — основаны на прямом чтении
  исходного кода и документации.
- **Самые сомнительные:** R14 (Think in Code — оценена как слишком масштабная),
  R15 (HTML skills — medium mismatch), R16 (craft/ — избыточно).
- **Непроверенные факты:** Точные усилия оценены приблизительно.
  Рекомендуется начать с R1-R3 (быстрые победы) и измерить фактическое время.

---

## Gaps & Unknowns

1. **Точное время имплементации** — оценено приблизительно
2. **Влияние R4 (Group gating) на существующие handler'ы** — нужен Explorer
3. **Совместимость R7 (MemoryProvider ABC) с существующим кодом** — нужен Debugger
4. **Реальная потребность в R15 (HTML skills)** — вопрос к пользователю

---

## Follow-up Questions

1. **Нужны ли HTML-отчёты в Telegram?** (R15) — если да, какой формат?
2. **Есть ли группы с топиками?** (R4) — если нет, отложить group gating
3. **Контекст становится узким местом?** (R14) — если да, Think in Code первой очереди
4. **Сколько внешних memory providers планируется?** (R7) — если один, ABC избыточен

---

## Summary Matrix

| # | Рекомендация | Приоритет | Усилия | Gap | Источник |
|---|-------------|-----------|--------|-----|----------|
| R1 | Think Before Coding правило | P1 | 30м | GAP-8 | karpathy |
| R2 | Surgical Changes + Diff Self-Review | P1 | 1ч | GAP-9 | karpathy |
| R3 | docs/DESIGN.md для Telegram UI | P1 | 2ч | GAP-5 | open-design/design.md |
| R4 | Group/Topic Mention Gating | P2 | 4-6ч | GAP-1 | openclaw |
| R5 | Toolsets Composition | P2 | 3-4ч | GAP-2 | hermes-agent |
| R6 | Surgical Changes в D5 Debugger 5 | P2 | 30м | GAP-9 | karpathy |
| R7 | MemoryProvider ABC | P2 | 4-6ч | GAP-11 | hermes-agent |
| R8 | PreCompact Hook | P3 | 3-4ч | GAP-4 | context-mode |
| R9 | Prompt Enhancement | P3 | 3-4ч | GAP-7 | stitch-design |
| R10 | Trivial Task Bypass | P3 | 15м | — | karpathy |
| R11 | Dead-Code Flagging в OUTPUT CONTRACT | P3 | 10м | — | karpathy |
| R12 | Cross-ref Ponytail ↔ Karpathy | P3 | 10м | — | karpathy |
| R13 | Cross-ref Zero-Risk ↔ Karpathy | P3 | 10м | — | karpathy |
| R14 | "Think in Code" Paradigm | P4 | 20ч+ | GAP-3 | context-mode |
| R15 | HTML Report/Diagram Skills | P4 | 4-6ч | GAP-10 | effective-html |
| R16 | craft/ Pattern | P4 | 8ч+ | GAP-6 | open-design |
| R17 | Tool Routing Whitelist Enhancement | P4 | 4ч | GAP-12 | context-mode |
| R18 | Skill Bundles | P4 | 2-3ч | — | hermes-agent |

**Итого:** 18 рекомендаций, 4 приоритета.
- P1 (быстрые победы): R1, R2, R3 — ~3.5 часа
- P2 (высокая ценность): R4, R5, R6, R7 — ~12-17 часов
- P3 (средняя ценность): R8-R13 — ~8-10 часов
- P4 (отложено/опционально): R14-R18 — ~38+ часов

**Рекомендация:** Начать с R1-R3 (3.5 часа → быстрые победы), затем R4-R7
по мере необходимости.
