# Slice 9-alt — Sort contracts + real WebSocket E2E — Status Report

**Branch**: `release/astro-migration` (parent `8ba132b` from Slice 8)
**Date**: 2026-09-24
**Scope**: Close parity row 2.10 (sort habits — backend gap surfaced and fixed) and parity row 11.1 (WebSocket fan-out — first real E2E tests).

---

## What changed

### 1. Backend sort query param (`castor/routes/api.py`)
`GET /api/v1/habits` previously ignored any client sort preference. Slice 9-alt adds an optional `?order_by=name|category|manually` query param. Invalid values silently fall back to the persisted `habit_list.order_by`.

**The bug was deeper than I expected**: `HabitListBuilder.build()` (the only consumer of `order_by`) read `self.habit_list.order_by` directly, not `self.order_by` — so even setting `builder.order_by = X` had no effect. Fix in `castor/storage/storage.py:251`: `effective_order_by = self.order_by if ... else self.habit_list.order_by`.

### 2. Backend WebSocket E2E coverage (`tests/test_slice9alt_realtime.py`, new)
The existing `tests/test_realtime.py` only tested the broadcast manager with a FakeWebSocket — no real WebSocket connection was ever tested. Slice 9-alt adds 6 real E2E tests:

  - `test_ws_no_token_closes_with_policy_violation` — server rejects handshake with HTTP 4xx
  - `test_ws_invalid_token_closes` — same with garbage token
  - `test_ws_valid_token_accepts_and_stays_open` — server accepts, ignores unknown frame types
  - `test_ws_push_habit_list_broadcasts_to_other_devices` — device 1's push → device 2 receives habit_list_changed
  - `test_ws_cross_user_isolation` — user A's broadcasts do NOT reach user B
  - `test_http_tick_broadcasts_to_other_websocket` — HTTP tick → tick_changed broadcast to watcher

The WebSocket tests run uvicorn in a daemon thread on a free port and connect with the `websockets` library. This avoids the `starlette.testclient.TestClient` event-loop conflict with our module-level `castor.main` import.

### 3. Backend sort tests (`tests/test_slice9alt_realtime.py::Slice9AltSortTests`, 4 tests)
- `test_default_order_is_creation_order` — no `order_by` → manual (creation order)
- `test_order_by_name_sorts_alphabetically`
- `test_order_by_category_sorts_tagged_first` — Bravo tagged → Bravo first
- `test_invalid_order_by_falls_back_to_default` — garbage `?order_by=garbage` → manual

## Evidence (commands + output)

```bash
$ cd /home/joel/castor-repo && .venv/bin/python -m unittest discover -s tests -p test_slice9alt_realtime.py -v
... 10 tests (4 sort + 6 WebSocket) ...
Ran 10 tests in 13.121s
OK

$ cd /home/joel/castor-repo && .venv/bin/python -m pytest tests/ --ignore=tests/test_batch4_live.py -q
322 passed, 6 skipped, 15 warnings in 208.26s (0:03:28)
# Was 312 → now 322; delta = +10 from Slice 9-alt

$ cd /home/joel/castor-repo/web/concepts && pnpm exec astro check
Result (94 files):
- 0 errors
- 0 warnings
- 59 hints
```

## What changed (file diff)

| File | Change |
|---|---|
| `castor/routes/api.py` | +20 / −2: `get_habits` now accepts `order_by` query param. |
| `castor/storage/storage.py` | +7 / −2: `HabitListBuilder.build()` honours builder's `order_by` override. |
| `tests/test_slice9alt_realtime.py` | new, +358: 4 sort + 6 WebSocket E2E tests + uvicorn-thread helper. |
| `Desktop/migration/parity-matrix.md` | rows 2.10 (❌→✅/🟡), 11.1 (🟡→✅) updated with Slice 9-alt evidence. |

## Slice 9-alt findings worth noting

### Finding #1 — HTTP tick DOES broadcast over WebSocket
The parity matrix row 11.1 said "habit.tick() → publish(HabitListChanged)". I wrote a "known gap" test expecting HTTP ticks to NOT broadcast (assuming only WebSocket push_tick published). **The test failed in the opposite direction**: HTTP ticks DO publish `TickChanged` via `castor/storage/dict.py:226` inside `habit.tick()`. The broadcast path is correct and works for HTTP tick too — the original parity matrix claim was right.

This is the **opposite** of the Slice 4 + Slice 5 + Slice 6 audit-event findings, where the parity matrix claimed events that didn't exist. The lesson: the parity matrix is sometimes correct, sometimes wrong. Always assert at the implementation level.

### Finding #2 — `HabitListBuilder.build()` ignored its own `order_by` attribute
`build()` was reading `self.habit_list.order_by` directly instead of `self.order_by`. This meant no caller could pass a sort override via the builder pattern. The fix is small (5 effective lines) but it's the kind of bug that's invisible until you try to add the API surface — exactly what Slice 9-alt did. The original NiceGUI frontend went through `habit_list.order_by = ...` directly (which still works), so this gap was never noticed.

### Finding #3 — `order_by` enum value vs name
The WebSocket code does `HabitOrder(msg["order_by"])` which requires string **values**. The enum uses `auto()` so values are 1/2/3. The HTTP code uses `HabitOrder[name]` which requires string **names** (NAME/CATEGORY/MANUALLY). I added the HTTP code with a `.upper()` to accept lowercase input from the Astro UI. The two patterns coexist; the WebSocket code is technically broken for any caller using the member names (it expects values 1/2/3 not "MANUALLY"). Out of scope for this slice but worth noting.

### Finding #4 — Test pollution from dispose
First run after adding `db.engine.dispose()` in `asyncTearDown` broke 31 later tests because they were sharing the engine. Removed the dispose calls — let the conftest and the final pytest teardown manage engine lifetime. Documented inline.

### Finding #5 — uvicorn in tests
Started a real uvicorn server on a free port inside `setUpClass` for the WebSocket tests. Pattern works reliably; ~3 seconds to bring up + tear down. Could be reused for any future test that needs a real network stack (HTTP/2, real WebSocket frame parsing, etc.). Pattern documented at the top of the test file.

## Out of scope (still)

- **Astro sort menu UI** — parity row 2.10 backend ✅, UI 🟡. The HabitGrid component still sorts manually. UI work is its own slice (probably Slice 10).
- **`PUT /api/v1/habits/meta` order_by persistence** — parity row 2.10 says "Updates habit_list.order_by" — this is the persistence path. Slice 9-alt adds the read-time override only. A full persistence API would be a separate slice.
- **`HabitOrder[canonical]` vs `HabitOrder(value)`** — the WebSocket code is technically broken for the value-based lookup. Out of scope; flagged for Phase 4.
- **Reconnect-after-disconnect behaviour** — Engine.IO was supposed to handle this (parity row 11.1 evidence); the WebSocket tests don't exercise reconnect. Playwright slice would.
- **Browser-side WebSocket client** — still not implemented in Astro. The /habits page uses BFF GET (no WS). Mobile/native clients would need a separate implementation.

## Next proposed slice

**Slice 10 — Astro sort menu + small UI gaps**:
  - 2.10 Astro UI (sort menu in HabitGrid)
  - 2.13 Daily note (long-press) UI nicety
  - 4.4 `/help` status update (already done in Slice 7 — needs parity matrix fix)
  - 12.1 `/tokens` status update (already done in Slice 7 — needs parity matrix fix)
  - 12.6 habit-display preferences UI
Estimated 3–4 hours.

Alternative: **Slice 10-alt — Backend gap closure**: `/admin` user-list pagination, `/circles` parity rows (10.1-10.5), `/admin` audit-event reconciliation (Slice 5 + 4 + 6.x findings). Estimated 4–6 hours.

## Sign-off request

Per the brief, do not move to Slice 10 until you confirm:
1. Slice 9-alt evidence sufficient (10 tests passing, 2 parity rows improved, 2 backend gaps closed)?
2. The HTTP-tick-broadcasts finding is acceptable (the matrix was correct; the assumption that HTTP didn't broadcast was wrong)?
3. The `HabitListBuilder.build()` fix is safe (small, additive, doesn't change the persistence path)?
4. Slice 10 (Astro UI) or 10-alt (backend gaps)?
