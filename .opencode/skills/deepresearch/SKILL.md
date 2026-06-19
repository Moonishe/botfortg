---
name: deepresearch
description: '☠️ ULTIMATE DEEP RESEARCH (дипресерч / deep research / дип ресерч): MCP-оргия, 10 итераций углубления, 5 параллельных исследователей + верификатор + синтезатор. Для задач где нужно докопаться до сути любой ценой. Запуск: skill("deepresearch") или просто скажи "дипресерч".'
---

# ☠️ DEEP RESEARCH — ULTIMATE MODE

**Философия:** Одно мнение — не мнение. Одна итерация — разминка.  
Ты не просто исследуешь — ты **выжигаешь тему до основания**, используя каждый доступный MCP-инструмент, каждую перспективу, каждую итерацию.

---

## 0. Конфигурация глубины

**При запуске навыка:**

```
skill("deepresearch")
→ "Сколько итераций углубления? (1-10, default: 5, max: 10)"
→ "Максимальная глубина MCP-поиска? (1-3, default: 3)"
→ "Режим: [нормальный/агрессивный/параноидальный]"
```

- **Нормальный:** 3-5 итераций, стандартные MCP
- **Агрессивный:** 5-8 итераций, все MCP на полную
- **Параноидальный:** 8-10 итераций, каждый факт перекрёстно верифицирован 3+ источниками

Если пользователь не ответил — действуй в **агрессивном** режиме с 5 итерациями.

---

## 1. MCP-арсенал (используй ВСЁ)

Каждый исследователь использует МАКСИМУМ доступных MCP-инструментов:

| MCP | Исследователь | Что даёт |
|-----|--------------|----------|
| **`webfetch`** | Все | Свежая инфа из интернета, docs, статьи |
| **`codegraph_context`** | 2, 4, 5 | Контекст кода: архитектура, зависимости |
| **`codegraph_explore`** | 2, 5 | Глубокий обход связанных символов |
| **`codegraph_search`** | 1, 2 | Быстрый поиск по коду |
| **`serena_find_symbol`** | 2, 4 | Точный LSP-поиск |
| **`serena_get_symbols_overview`** | 2 | Структура файлов |
| **`serena_search_for_pattern`** | 3, 5 | Поиск паттернов (баги, usage) |
| **`serena_find_referencing_symbols`** | 2, 3 | Анализ влияния |
| **`serena_get_diagnostics_for_file`** | 3 | LSP-ошибки |
| **`git_log`** | 4 | История изменений |
| **`git_diff`** | 4 | Что менялось |
| **`memory_search_nodes`** | 4 | Граф знаний проекта |
| **`memory_read_graph`** | 4 | Связи в проекте |
| **`context7_query-docs`** | 1, 2 | Документация библиотек |
| **`grep`** | 3, 5 | Поиск error/bug/security паттернов |
| **`glob`** | 1, 5 | Поиск файлов по шаблонам |

### Матрица приоритетов MCP для каждого исследователя

| Исследователь | Primary MCP | Secondary MCP | Когда |
|--------------|------------|---------------|-------|
| 1. Bird's Eye | webfetch, glob, codegraph_search | grep, context7 | Первичный обзор |
| 2. Deep Dive | codegraph_explore, serena_find_symbol, context7 | serena_get_symbols_overview, codegraph_context | Технический анализ |
| 3. Devil's Advocate | serena_search_for_pattern, grep, serena_get_diagnostics | codegraph_impact, webfetch | Поиск проблем |
| 4. Historian | git_log, memory_search_nodes, git_diff | webfetch, memory_read_graph | Контекст и эволюция |
| 5. Practitioner | serena_search_for_pattern, glob, codegraph_search | serena_find_referencing_symbols, webfetch | Примеры и кейсы |

### Промпт-инъекция MCP для исследователей

Каждый исследователь получает в свой промпт:

```
Доступные MCP-инструменты (используй МАКСИМУМ из них):
- webfetch(url) — загрузить веб-страницу
- codegraph_context(task) — контекст кода
- codegraph_explore(query) — обход символов
- codegraph_search(query) — поиск символов
- serena_find_symbol(name) — LSP-поиск
- serena_get_symbols_overview(file) — структура файла
- serena_search_for_pattern(pattern) — поиск паттернов
- serena_find_referencing_symbols(sym, file) — кто использует
- serena_get_diagnostics_for_file(file) — LSP-ошибки
- git_log — история коммитов
- git_diff — изменения
- memory_search_nodes — поиск в графе знаний
- context7_query-docs(lib, query) — документация библиотек
- grep(pattern) — текстовый поиск
- glob(pattern) — поиск файлов

Правило: НЕ используй только один источник. Каждый finding должен быть подкреплён минимум 2 разными MCP.
```

---

## 2. Фаза 1: Research — 5 параллельных убийц

Запускаются **ОДНОВРЕМЕННО**. Каждый получает уникальный промпт с его перспективой.

### Исследователь 1 — 🦅 Bird's Eye (Обзорный)

```
Ты —首席 аналитик с 20-летним опытом. Твоя задача: дать максимально полную карту территории.

Фокус:
- Что это? (определение, назначение, место в экосистеме)
- Ключевые компоненты и их взаимодействие
- Основные конкуренты/альтернативы (сравнительная таблица)
- Ключевые метрики: звезды, загрузки, adoption, комьюнити
- Кто использует? (известные компании/проекты)

MCP-пайплайн (выполни ВЕСЬ, не пропускай):
1. webfetch — 2-3 лучшие статьи/обзоры по теме
2. glob — найди все связанные файлы в проекте
3. codegraph_search — найди ключевые символы
4. Если тема про библиотеку — context7_query-docs

Формат выдачи:
SUMMARY: <3-5 предложений>
LANDSCAPE_MAP:
- Определение
- Компоненты
- Альтернативы (таблица)
- Метрики
SOURCES:
- <MCP-инструмент>: <что дал>
CONFIDENCE: <high/medium/low>
GAPS: <что не удалось выяснить>
```

### Исследователь 2 — 🔬 Deep Dive (Технический)

```
Ты — staff engineer, senior architect. Твоя задача: понять систему на уровне, достаточном чтобы написать её с нуля.

Фокус:
- Внутренняя архитектура: как устроено под капотом
- Ключевые алгоритмы и структуры данных
- API surface: основные функции, классы, методы (с сигнатурами)
- Потоки данных: от входа до выхода
- Extension points: как кастомизировать/расширять

MCP-пайплайн (ВЕСЬ):
1. codegraph_context — полный контекст темы
2. serena_find_symbol — найди ВСЕ ключевые символы (не 1, а 5-10)
3. serena_get_symbols_overview — для каждого ключевого файла
4. codegraph_explore — обойди связанные символы
5. context7_query-docs — если тема про библиотеку, запроси API docs
6. serena_find_referencing_symbols — для ключевых функций

Формат выдачи:
SUMMARY: <3-5 предложений>
ARCHITECTURE:
- Схема компонентов (text-based diagram)
- Data flow
API_SURFACE:
- Классы/функции с сигнатурами
ALGORITHMS:
- Ключевые алгоритмы
EXTENSION:
- Точки расширения
SOURCES:
- <MCP>: <что дал>
CONFIDENCE: <high/medium/low>
GAPS: <непонятные места>
```

### Исследователь 3 — ☣️ Devil's Advocate (Критический)

```
Ты — red team, QA-lead с паранойей. Твоя задача: найти ВСЁ что может пойти не так.

Фокус:
- Известные баги и проблемы (search: bug, issue, known problem, limitation)
- Security-уязвимости: XSS, injection, path traversal, race conditions
- Performance-узкие места: N+1, утечки, CPU-затратные операции
- Error handling: что если что-то пошло не так? обработано?
- Race conditions: shared state, async locking, overlap
- Anti-patterns: что делать НЕ надо

MCP-пайплайн (ВЕСЬ):
1. serena_search_for_pattern — search: bug, fixme, todo, hack, workaround, XXX
2. serena_get_diagnostics_for_file — для каждого ключевого файла
3. grep — error, exception, raise, bare except
4. codegraph_impact — для критических символов
5. webfetch — поиск known issues / CVE

Формат выдачи:
SUMMARY: <3-5 предложений>
CRITICAL_ISSUES:
- [SEVERITY: CRITICAL/HIGH/MEDIUM/LOW] <проблема> — <где> — <почему>
SECURITY:
- Найденные уязвимости
PERFORMANCE:
- Узкие места
RELIABILITY:
- Пробелы в error handling
ANTI_PATTERNS:
- Что делать не надо
SOURCES:
- <MCP>: <что нашёл>
CONFIDENCE: <high/medium/low>
GAPS: <что не удалось проверить>
```

### Исследователь 4 — 📜 Historian (Контекстуальный)

```
Ты — tech historian, эрудит. Твоя задача: восстановить полную картину эволюции.

Фокус:
- История: когда появилось, кем создано, ключевые вехи
- Почему было создано? (какую проблему решало)
- Эволюция: как менялось от версии к версии
- Тренды: куда движется сейчас, roadmap
- Альтернативы: что было до, что пришло после
- Комьюнити: кто поддерживает, ecosystem, adopters

MCP-пайплайн (ВЕСЬ):
1. git_log — история коммитов по теме
2. git_diff — ключевые изменения
3. memory_search_nodes — поиск в графе знаний
4. memory_read_graph — связи
5. webfetch — поиск истории/трендов
6. codegraph_search — ключевые символы и их эволюция

Формат выдачи:
SUMMARY: <3-5 предложений>
TIMELINE:
- <дата/версия>: <событие>
EVOLUTION:
- Как менялось
TRENDS:
- Куда движется
ALTERNATIVES:
- Что было/есть вместо
ECOSYSTEM:
- Кто использует, комьюнити
SOURCES:
- <MCP>: <что дал>
CONFIDENCE: <high/medium/low>
GAPS: <что не удалось узнать>
```

### Исследователь 5 — ⚡ Practitioner (Прикладной)

```
Ты — solution architect, тот кто реально внедряет. Твоя задача: дать готовые к использованию примеры и конфиги.

Фокус:
- Real-world примеры использования (в коде проекта)
- Best practices и паттерны использования
- Конфигурация: типовые настройки, .env, config файлы
- Интеграция: как подключается к другим системам
- Testing: как тестировать
- Deployment: как деплоить

MCP-пайплайн (ВЕСЬ):
1. serena_search_for_pattern — поиск usage паттернов в коде
2. glob — найди конфиги, примеры, тесты
3. serena_find_referencing_symbols — как используется в проекте
4. codegraph_search — связанные компоненты
5. webfetch — best practices из внешних источников

Формат выдачи:
SUMMARY: <3-5 предложений>
USAGE_PATTERNS:
- <паттерн> — <где в коде> — <как использовать>
CONFIG_EXAMPLES:
- Примеры конфигурации
BEST_PRACTICES:
- Что рекомендуется
INTEGRATION:
- С чем и как интегрируется
TESTING:
- Как тестировать
SOURCES:
- <MCP>: <что дал>
CONFIDENCE: <high/medium/low>
GAPS: <чего не хватает для внедрения>
```

---

## 3. Фаза 2: Cross-Validation (перекрёстная верификация)

**После того как все 5 исследователей вернули результаты**, но ДО синтеза:

### Шаг 2.1 — Fact-checking

Для КАЖДОГО факта из SUMMARY каждого исследователя:
1. Подтверждён ли он ещё хотя бы одним исследователем? → ✅ Consensus
2. Опровергается ли другим исследователем? → ⚠️ Contradiction → **эскалация**
3. Нет подтверждения ни от кого? → ❓ Unverified → **снизить confidence**

### Шаг 2.2 — Contradiction Resolution

При противоречии:
1. Запусти **верификатора** (`task(subagent_type="worker")`) с задачей:
   ```
   "Проверь факт: <спорный факт>. 
    Источник A (<исследователь 1>): <утверждение A>
    Источник B (<исследователь 3>): <утверждение B>
    Используй 3 разных MCP-инструмента для верификации.
    Вердикт: A прав / B прав / оба частично правы / нужны данные"
   ```
2. Результат верификатора → в финальный отчёт

---

## 4. Фаза 3: Synthesis (Многослойный синтез)

После cross-validation ты собираешь **итоговый отчёт**:

```markdown
# ☠️ DEEP RESEARCH: <Тема>
# Конфиг: <N итераций> | <режим> | <MCP-глубина>

## ⚡ Executive Summary
<3-5 предложений, суть>

## ✅ Consensus Map (подтверждено 2+ исследователями)
- Вывод 1 — [источники: 🦅 🔬 ⚡]
- Вывод 2 — [источники: 🔬 📜]

## ⚠️ Contradictions & Open Questions
- <спор> — верификатор сказал: <вердикт>

## 🔍 Findings by Perspective

### 🦅 Bird's Eye (Обзор)
<самое важное от Исследователя 1>

### 🔬 Deep Dive (Технический)
<самое важное от Исследователя 2>

### ☣️ Devil's Advocate (Критический)
<самое важное от Исследователя 3>

### 📜 Historian (Контекст)
<самое важное от Исследователя 4>

### ⚡ Practitioner (Прикладной)
<самое важное от Исследователя 5>

## 📊 Source Map
| MCP-инструмент | Что дал | Кол-во находок |
|---------------|---------|----------------|
| webfetch | <...> | N |
| codegraph_* | <...> | N |
| serena_* | <...> | N |
| git_* | <...> | N |
| memory_* | <...> | N |
| context7 | <...> | N |
| grep/glob | <...> | N |

## 🎯 Confidence Assessment
- Общая достоверность: <high/medium/low>
- Самые надёжные находки: <какие>
- Самые сомнительные: <какие>
- Непроверенные факты: <список>

## ❓ Gaps & Unknowns
- Что не удалось выяснить
- Почему (ограничение MCP / нет данных / тема новая)

## 📈 Evolution Map (для многоитерационных исследований)
| Итерация | Глубина | Ключевые находки | Confidence Delta |
|----------|---------|-----------------|-----------------|
| 1 | Обзор | ... | начальный |
| 2 | Углубление | ... | +N% |
| ... | ... | ... | ... |

## 🔮 Follow-up Questions (для следующих итераций)
Приоритет 1: <вопрос> — почему важно, какой MCP ответит
Приоритет 2: <вопрос>
Приоритет 3: <вопрос>
```

---

## 5. Фаза 4: Iterate (Итеративное углубление)

**Автоматически**, без вопросов пользователю (если не сказано иначе):

### Логика перехода к следующей итерации:

```
ЕСЛИ iteration < max_iterations И
    (есть GAPS с confidence < medium ИЛИ
     есть CONTRADICTIONS ИЛИ
     есть UNANSWERED_QUESTIONS ИЛИ
     confidence общий < high):
  → запустить следующую итерацию
ИНАЧЕ:
  → завершить, вернуть отчёт
```

### Для каждой итерации:

1. **Анализ gaps** из предыдущей итерации
2. **Переформулировка промптов** исследователям:
   - Сфокусироваться на unresolved вопросах
   - Игнорировать уже подтверждённые факты
   - Добавить `[ITERATION N, FOCUS: <...>]` в начало промпта
3. **Обновление MCP-пайплайна:**
   - Добавить конкретные запросы к MCP по unresolved вопросам
   - Исключить уже пройденные MCP-запросы
4. **Выполнить Фазы 1-3 заново** с уточнёнными промптами
5. **Сравнить confidence delta** с предыдущей итерацией:
   - Если confidence не вырос → diminishing returns → завершить
   - Если confidence вырос → продолжать

### Авто-стоп критерии:

```
confidence == high И gaps == 0 → STOP (цель достигнута)
diminishing_returns >= 2 итерации подряд → STOP
iteration >= max_iterations → STOP
```

---

## 6. Режимы работы

| Режим | Итераций | MCP-глубина | Cross-validation | Когда использовать |
|-------|----------|-------------|------------------|-------------------|
| **Нормальный** | 3-5 | 2 | Только consensus | Стандартные задачи |
| **Агрессивный** | 5-8 | 3 | + contradiction check | Сложные темы, архитектура |
| **Параноидальный** | 8-10 | 3 | + верификатор для каждого факта | Security, critical decisions, production |

### Агрессивный режим — особенности:
- Каждый исследователь делает MIN 3 разных MCP-запроса
- Cross-validation обязательна для всех findings
- Fact-checker запускается для любых противоречий
- Авто-стоп: только если confidence high И gaps=0

### Параноидальный режим — дополнения:
- **Каждый факт** подтверждается 2+ разными MCP
- **Double-blind верификация**: 2 независимых worker'а проверяют один факт
- **Источниковая цепочка**: для каждого факта — полный путь от сырых данных до вывода
- **Эскалация**: если confidence после 3 итераций < medium → предупредить пользователя
- **Аудит логов**: все MCP-запросы логируются в `.opencode/memory/research/<topic>/audit.log`

---

## 7. Сохранение результатов

После КАЖДОЙ итерации сохраняй промежуточные результаты:

```
.opencode/memory/research/<topic>/
├── config.json              # Конфигурация исследования
├── audit.log                # Лог всех MCP-запросов
├── iteration-01/
│   ├── raw/                  # Сырые результаты исследователей
│   │   ├── 01-birds-eye.md
│   │   ├── 02-deep-dive.md
│   │   ├── 03-devils-advocate.md
│   │   ├── 04-historian.md
│   │   └── 05-practitioner.md
│   ├── cross-validation.md   # Результаты верификации
│   └── synthesis.md          # Синтезированный отчёт
├── iteration-02/
│   └── ...
├── final-report.md          # Финальный объединённый отчёт
└── summary.md               # Executive Summary (1 страница)
```

---

## 8. Сравнение с max-mode

| Аспект | max-mode | deepresearch (ULTIMATE) |
|--------|----------|------------------------|
| **Цель** | Написать правильный код | Понять тему до уровня эксперта |
| **Параллельность** | 5 propose → judge → 1 replay | 5 исследователей → cross-val → синтез → итерации |
| **Итерации** | 1 проход | До 10 с авто-стоп |
| **MCP** | read + edit/write | **14+ MCP-инструментов** |
| **Верификация** | D5→R5 (code quality) | Cross-validation + fact-checker |
| **Результат** | Изменённый код | Многослойный отчёт + source map |
| **Глубина** | 1 уровень | До 10 уровней углубления |
| **Автономность** | Ждёт пользователя | Сам решает когда углубляться |

---

## 9. Быстрый старт

```markdown
# Минимальный запуск:
skill("deepresearch")
# → ответь на вопросы конфигурации
# → 5 исследователей стартуют
# → через N итераций получаешь отчёт

# Агрессив без вопросов:
skill("deepresearch")
# → "агрессивный, 8 итераций, исследуй тему X"

# Параноидальный для критического:
skill("deepresearch")
# → "параноидальный, 10 итераций, безопасность модуля Y"

# Продолжить предыдущее исследование:
# → загрузи отчёт из .opencode/memory/research/<topic>/final-report.md
# → скажи "углубись по вопросу Z"
```

---

## 10. Чеклист перед запуском

- [ ] Установлена глубина итераций (1-10)
- [ ] Выбран режим (норма/агрессив/параноида)
- [ ] Определена тема исследования
- [ ] Все 5 исследователей получат уникальные MCP-пайплайны
- [ ] Cross-validation запланирована после сбора
- [ ] Сохранение результатов настроено
- [ ] Авто-стоп критерии установлены

---

## 11. Исследование удалённых проектов/репозиториев (Repository Deep Dive)

При исследовании **удалённого репозитория** (GitHub/GitLab/Bitbucket) — выполняй **ПОЛНЫЙ ПАЙПЛАЙН** сбора информации. Ничего не пропускай.

### 11.1 Pre-flight: определение типа проекта

Перед запуском исследователей определи:
- Язык/стек (читай `package.json`, `Cargo.toml`, `pyproject.toml`, `go.mod`, `CMakeLists.txt`)
- Система сборки (pnpm/npm/yarn, cargo, poetry, go build, make)
- Размер проекта (кол-во файлов, строк кода, коммитов)

### 11.2 Обязательные источники данных (выполнить ВСЕ)

| # | Источник | Что даёт | Как получить |
|---|----------|----------|-------------|
| 1 | **README.md** | Общее описание, фичи, быстрый старт | `webfetch(GitHub raw)` |
| 2 | **AGENTS.md / CLAUDE.md / SOUL.md** | Prompt-инжекты, правила разработки, архитектура | `webfetch(GitHub raw)` |
| 3 | **package.json / Cargo.toml / pyproject.toml** | Зависимости, версии, скрипты | `webfetch(GitHub raw)` |
| 4 | **VISION.md / ROADMAP.md / ARCHITECTURE.md** | Философия, планы, архитектура | `webfetch(GitHub raw)` |
| 5 | **CONTRIBUTING.md** | Правила контрибуции | `webfetch(GitHub raw)` |
| 6 | **CHANGELOG.md** | История версий | `webfetch(GitHub raw)` |
| 7 | **LICENSE** | Лицензия | `webfetch(GitHub raw)` |
| 8 | **SECURITY.md** | Политика безопасности | `webfetch(GitHub raw)` |
| 9 | **.github/workflows/** | CI/CD пайплайны | `webfetch(GitHub API)` |
| 10 | **vitest.config / jest.config / pytest.ini** | Тестовая инфра | `webfetch(GitHub raw)` |
| 11 | **docker-compose.yml / Dockerfile** | Инфраструктура | `webfetch(GitHub raw)` |
| 12 | **tsconfig.json / .eslintrc / .prettierrc** | Настройки качества кода | `webfetch(GitHub raw)` |
| 13 | **Топология директорий** | `src/`, `apps/`, `packages/`, `lib/` | `webfetch(GitHub tree)` |
| 14 | **warpgrep_github_search** | Поиск по исходному коду (если репо публичный) | `warpgrep_github_search` |
| 15 | **GitHub API / Issues / PRs** | Активные проблемы, баги, road map | `gh` или `webfetch(GitHub Issues API)` |

### 11.3 Чеклист: исследование репозитория

```
□ README.md прочитан
□ AGENTS.md / CLAUDE.md (если есть) прочитаны
□ package.json / Cargo.toml / pyproject.toml (ключевой конфиг) получен
□ VISION.md / ARCHITECTURE.md (если есть) прочитаны
□ CHANGELOG.md прочитан
□ LICENSE проверена
□ CONTRIBUTING.md прочитан
□ .github/workflows/ проверен
□ docker-compose.yml / Dockerfile (если есть) проверен
□ Структура src/ получена
□ GitHub Issues + PRs (top-10 открытых + top-5 закрытых) проверены
□ warpgrep_github_search запущен (для публичных репо)
□ SECURITY.md прочитан
□ Даты: created_at, last_commit, last_release
□ Метрики: звёзды, форки, контрибьюторы, коммиты
```

### 11.4 Если warpgrep не работает (как с openclaw/openclaw)

Если `warpgrep_github_search` не может найти репозиторий (хоть он публичный):

1. **GitHub API v3 (REST):** `webfetch("https://api.github.com/repos/owner/repo/contents/path")`
2. **GitHub raw content:** `webfetch("https://raw.githubusercontent.com/owner/repo/branch/file")`
3. **GitHub tree API:** `webfetch("https://api.github.com/repos/owner/repo/git/trees/main?recursive=1")`
4. **Клонировать репозиторий (если возможно):** `git clone --depth 1 <url> <temp_dir>` и используй локальные инструменты (codegraph, serena, grep, glob)
5. **Только если всё остальное не работает:** webfetch ключевых файлов по одному через GitHub raw

### 11.5 Полный pipeline "Познать всё" (Total Knowledge)

Когда цель — **полностью изучить проект** (репозиторий/кодовую базу), выполняй:

**Step 1 — Сбор мета-информации (главный агент):**
```
1. Прочитай README → понимание проекта
2. Прочитай AGENTS.md/CLAUDE.md → правила разработки
3. Прочитай package.json/Cargo.toml → зависимости
4. Прочитай VISION.md → философия
5. Получи структуру директорий (glob **/*)
6. Собери метрики: звёзды, коммиты, контрибьюторы
7. Проверь LICENSE, SECURITY.md, CONTRIBUTING.md
8. GitHub API: открытые issues (top-10), последние releases
9. CI/CD: .github/workflows/ (или аналог)
```

**Step 2 — Сбор архитектурной информации (главный агент):**
```
1. Найди все архитектурные документы (*ARCHITECTURE*, *DESIGN*, *ADR*)
2. Определи модульную структуру (src/packages/apps)
3. Найди диаграммы, схемы, draw.io
4. Определи ключевые абстракции (классы, типы, интерфейсы)
5. Найди точку входа (main.ts, index.ts, main.py)
6. Определи систему плагинов/расширений
```

**Step 3 — Запуск 5 исследователей (Фаза 1):**
С полным контекстом из Steps 1-2 каждый исследователь получает:
- Ссылки на все найденные файлы
- Структуру проекта
- Ключевые метрики
- Специфичные MCP-инструменты

**Step 4 — Глубокий анализ кода (Фазы 2-4):**
Каждый исследователь дополнительно:
- Проходит по ключевым модулям один за другим
- Анализирует зависимости между модулями
- Ищет паттерны (singleton, factory, observer, pub/sub)
- Оценивает тестовое покрытие (структура test/, colocated tests, e2e)
- Анализирует CI/CD пайплайн
- Проверяет документацию (inline docs, README модулей, API docs)

**Step 5 — Итоговый синтез:**
Финальный отчёт содержит:
- Полную карту проекта (директории, модули, зависимости)
- Архитектурную схему (text-based)
- Ключевые абстракции и паттерны
- Quality assessment (тесты, CI, код-стайл)
- Security assessment
- Active development areas (issues, PRs, коммиты)
- Рекомендации и open questions

### 11.6 Формат финального отчёта для репозитория

```markdown
# 🔬 DEEP RESEARCH: <owner>/<repo>

## 📊 Quick Stats
- ⭐ Stars: N | 🍴 Forks: N | 👥 Contributors: N
- 📝 Commits: N | 🔀 Open PRs: N | 🐛 Open Issues: N
- 📅 Created: <date> | 🕐 Last Commit: <date> | 🏷️ Latest Release: <version>
- 📦 Stack: TypeScript/Node.js/Python/Rust/Go
- 📜 License: MIT/GPL/Apache 2.0

## 🏗️ Architecture
<text-based diagram / module structure>

## 📁 Project Map
```
owner/repo/
├── src/           # Core source
│   ├── core/      # Core abstractions
│   ├── agents/    # Agent system
│   └── ...
├── apps/          # Applications
├── packages/      # Shared packages
├── extensions/    # Plugins
└── docs/          # Documentation
```

## 🧩 Key Abstractions & Patterns
- <абстракция 1>
- <абстракция 2>

## 📋 Dependencies & Stack
- Runtime: Node 24 / Python 3.13
- Build: pnpm/cargo/poetry
- Test: vitest/pytest
- CI: GitHub Actions

## ✅ Quality Assessment
| Критерий | Оценка | Детали |
|----------|--------|--------|
| Тесты | ✅/⚠️/❌ | <покрытие, структура> |
| CI/CD | ✅/⚠️/❌ | <что автоматизировано> |
| Code Style | ✅/⚠️/❌ | <линтер, форматтер> |
| Документация | ✅/⚠️/❌ | <inline + внешняя> |
| Security | ✅/⚠️/❌ | <SECURITY.md, практики> |

## 🔥 Active Development
- <топ-3 активные темы>
- <топ-3 открытых issue>
- <последние коммиты>

## 🎯 Recommendations
1. <рекомендация>
2. <рекомендация>

## ❓ Open Questions
- <вопросы, требующие дальнейшего исследования>
```

---

## 12. Полный репозиторий-pipeline (одной командой)

```
skill("deepresearch")
→ "исследуй репозиторий https://github.com/owner/repo полностью"
→ "режим: параноидальный, итерации: 5"
→ главный агент выполняет Steps 1-2 (сбор мета-информации)
→ запуск 5 исследователей с полным контекстом
→ cross-validation + синтез
→ финальный отчёт в .opencode/memory/research/<repo>/
```

**Правило:** Никогда не начинай исследование с 5 исследователей без предварительного сбора мета-информации (Steps 1-2). Исследователи должны получать контекст, а не начинать с нуля.

---

*"Исследовать — значит видеть то, что видели все, и думать так, как не думал никто."*
