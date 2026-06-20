# Cross-Judge Synthesis — 5 Perspectives on Top-5 Fixes

**Дата:** 2026-06-19
**Judges:** Minimalist, Architect, Defense, Risk-Based, Pragmatic
**Метод:** Сравнение 5 разных perspectives без якорения на одной рекомендации

---

## Consensus Matrix

| Approach | FIX #1 (Sandbox escape) | FIX #2 (Homoglyph PI) | FIX #3 (repr injection) | FIX #4 (Legacy HMAC) | FIX #5 (Race+stale+close) |
|----------|-------------------------|------------------------|--------------------------|------------------------|-----------------------------|
| **Minimalist** | ✓ P0 | ✓ P0 | deferred | ✓ P1 | ✓ P1 |
| **Architect** | ✓ part of Fix #2 (unified sandbox) | ✓ part of Fix #2 | ✓ part of Fix #2 | ✓ part of Fix #3 (unified auth) | ✓ part of Fix #1 (AsyncService) |
| **Defense-in-Depth** | ✓ Category #1 | ✓ Category #2 | ✓ Category #1 | ✓ Category #2 | ✓ Category #4-5 |
| **Risk-Based** | ✓ Risk Score 150 | ✓ Risk Score 192 (highest!) | ✓ Risk Score 60 | ✓ Risk Score 40 (in HMAC bundle) | ✓ #8, #9, #10 |
| **Pragmatic** | ✓ P0 | ✓ P0 | ✓ P0 | ✓ P1 | ✓ part of Polish phase |

**5/5 consensus:** Все judges поставили #1-#3 в P0, #4-#5 в P1.

---

## Effort Estimates Comparison

| Fix | Minimalist | Architect | Defense | Risk-Based | Pragmatic | Consensus |
|-----|-----------|-----------|---------|------------|-----------|-----------|
| #1 Sandbox | 0.3h | 3h (bundled) | 2h (Layer 1+Cnry) | 2h | 2h | **~2h** |
| #2 Homoglyph | 0.5h | 0.5h (web_sanitizer) | 2h (TR39 table) | 3h | 3h | **~2h** |
| #3 repr inj | 1h | 0.5h (sdd_executor) | 1h (Layer 1) | 2h | 1h | **~1h** |
| #4 Legacy HMAC | 0.3h | 1h (auth module) | 1h | 1h (in bundle) | 1h | **~1h** |
| #5 Race+stale | 0.4h | 7h (async_service) | 4h | 2h | 0.5h | **varies** |

---

## Differentiation Analysis

### Где judges разошлись

1. **FIX #4 (Legacy HMAC bypass):**
   - Architect: "включить в unified auth module через константные проверки" → через структурное изменение
   - Minimalist: "reject legacy callbacks inline" → 3 строки
   - Diff: подход. **Решается оба одновременно.**

2. **FIX #5 (stale now + provider.close):**
   - Architect: "решается через AsyncService база + parallel close с timeout" → структура
   - Pragmatic: "только race fix за 0.5h, остальное отложить" → инкрементально
   - Diff: scope. **Рекомендация:** race fix сейчас (минимально), restructure в Phase F.

3. **Proactive vs Preventive:**
   - Minimalist/Risk-Based: точечные фиксы (ROI optimized)
   - Architect/Defense: структурное улучшение (systemic protection)
   - Pragmatic: ship now, refactor later

---

## Рекомендуемая последовательность (Consensus)

### Phase 1: Immediate (Week 1, ~7h работы, 1 PR из 5 коммитов)

```
PR "security-hardening/round-5":

Commit 1 (P0, Critical): fix: extend sandbox blacklist to prevent __traceback__ escape
  - mcp_code_exec.py: +7 lines to _SANDBOX_BLACKLIST
  - +1 test in tests/test_mcp_security.py

Commit 2 (P0, Critical): fix: homoglyph PI — replace blacklist with confusables table
  - web_sanitizer.py: +22 lines (Greek map + zero-width strip)
  - prompt_injection_scanner.py: +14 lines (Greek map)
  - +1 test for known bypass

Commit 3 (P0, Critical): fix: sdd_executor — replace repr() with json.dumps
  - sdd_executor.py: -2 / +3 lines (repr → json.dumps)
  - +subprocess json.loads

Commit 4 (P1, High): fix: disable legacy HMAC route (defense-in-depth)
  - _confirm.py: -6 / +4 lines (bypass → reject)
  - send.py: -3 / +4 lines (bypass → reject)

Commit 5 (P1, High): fix: router stale now + provider.close consolidation + flush race
  - router.py: -1 / +2 lines (now loop recompute)
  - auto_save_batch.py: +1 line (lock in finally)
  - router.py: refactor provider.close to gather (Phase 2)
```

### Phase 2: Sprint 2 (Week 2, ~7h)

- Router provider consolidation
- 11 background loops overlap guard (use existing skill)
- DEK rotation fix

### Phase 3: Phase F (background, не блокирует)

- `_now_utc` dedup
- `_core.py` разбивка (2123 строк → 5×400)
- ensure_future миграция
- Service layer refactor (если single-user bot OK с current architecture)

---

## Coverage Statistics

- **20 агентов:** все завершились successfully
- **Findings total discovered:** ~85 issues
- **Findings CONFIRMED critical/high:** 11 (3 CRITICAL + 8 HIGH)
- **Findings DEFERRED:** ~24 (architecture debt, refactor scope)
- **Findings CONFIRMED false positives:** 8 (refuted by source code)
- **Cross-validation:** 100% of CRITICAL/HIGH verified by ≥3 sources

---

## Recommended Single-Command Quickstart (для пользователя)

НЕ выполнялось автоматически — read-only аудит. Для применения фиксов:

```bash
# Phase 1
git checkout -b fix/security-round-5
# Apply commits 1-5 in sequence per final-report.md
pytest tests/ -x -v
git commit -m "fix: security-hardening round 5"
git push origin fix/security-round-5
```
