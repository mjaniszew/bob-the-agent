# Hermes session store fallback — read-only sqlite peek

Recorded 2026-11-12 during the live probes (see `hermes-live-probe.out` /
`hermes-resume-probe.out`). This documents the FALLBACK session-id lookup used
by `harness.adapters.hermes.HermesAdapter.newest_session_id_from_store`, which
only runs when stdout extraction (`Session:` line / `hermes --resume` help
line) fails — for example when the child is killed by its run budget and never
prints the footer block, but has still recorded the session.

## Where the store lives

- Store file: `$HERMES_HOME/profiles/<profile>/state.db`
- In the PoC container (`HERMES_HOME=/opt/data`, see adapter docstring):
  `/opt/data/profiles/<profile>/state.db`
- `state.db` is a standard SQLite database file.

## Query

```sql
SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1
```

- Table: `sessions`
- Columns verified live: `id TEXT`, `source`, `started_at REAL` (epoch
  seconds, per-row `strftime` values observed in the live store), plus further
  profile/bookkeeping columns (the full schema is in the live store and is not
  pinned here — the adapter query touches only `id` and `started_at`).

## How the adapter opens it

Read-only, uri mode — the adapter must never create or mutate the store:

```python
sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
```

- Missing `state.db`, an empty `sessions` table, or any sqlite/OSError → returns
  `None` (the fallback is best-effort by design and never raises).
- Ordering note: `started_at` is a REAL (epoch seconds); ties on equality are
  resolved by SQLite arbitrarily, which is acceptable for a fallback.

## Evidence

Live probe session `20261112_223916_4f2531` (one-shot `simple` profile run,
`--run-budget 60`) appeared both in the `Session:` stdout line and as the
newest row of `/opt/data/profiles/simple/state.db` (`sessions.id`) when peeked
the same way.
