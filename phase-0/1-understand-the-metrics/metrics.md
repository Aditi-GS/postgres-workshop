# Metric List
## A. Headline Performance

| Metric | Source |
|--------|--------|
| Wall-clock time | Client timer |
| Throughput (rows/s) | 100 000 / time |

**Dropped:** Client wait vs. server time split.
> *Assumption verified:* localhost (RTT ≈ 0). Network latency is negligible; the split adds no signal.

---

## B. Transaction & Commit

| Metric | Source |
|--------|--------|
| `commits` Δ | `pg_stat_database` |
| `wal_sync` Δ | `pg_stat_wal` |

**Dropped:** `rollbacks` Δ, `wal_write` Δ.
> *Assumption verified:* `synchronous_commit = on`, no errors expected → rollbacks = 0 by definition. `wal_write` is redundant with `wal_sync` when `wal_buffers = 4 MB` (buffer won't overflow, so every write is immediately followed by a sync — the two counts are 1:1).

---

## C. WAL

| Metric | Source |
|--------|--------|
| `wal_records` Δ | `pg_stat_wal` |
| `wal_bytes` Δ | `pg_stat_wal` |

**Dropped:** `wal_fpi` Δ, `wal_buffers_full` Δ, per-statement WAL from `pg_stat_statements`.
> *Assumptions verified:*
> - `wal_fpi`: total WAL ≈ 20 MB vs. `max_wal_size` = 1 GB → no WAL-pressure checkpoint. `checkpoint_timeout = 300 s` → no timed checkpoint (worst-case load ≈ 60 s). No checkpoint = no FPIs. Confirmed by validity gate #13.
> - `wal_buffers_full`: largest single record = 8 KB (one page). `wal_buffers = 4 MB` holds 512 such records. Cannot overflow.
> - Per-statement WAL: aggregate `wal_records` already captures the COPY-vs-INSERT difference (2k vs 100k). Per-statement granularity adds no new insight at this scale.

---

## D. Client / Network

| Metric | Source |
|--------|--------|
| Round-trip count | Driver (count `execute()` calls) |
| Protocol + batch size | **Note** (psycopg2, extended protocol, no pipeline; method 3 batch = N) |

**Dropped:** Total bytes sent, client wait time.
> *Assumption verified:* localhost. Bytes on the wire don't affect timing. Client wait ≈ 0.

---

## E. Executor / CPU

| Metric | Source |
|--------|--------|
| `total_exec_time` Δ per query pattern | `pg_stat_statements` |
| `calls` per query pattern | `pg_stat_statements` |

**Dropped:** `shared_blks_dirtied` per statement, backend CPU via `ps`.
> *Assumptions verified:*
> - `shared_blks_dirtied` per statement: redundant with table-level `blks_dirtied` (category F). The per-statement view would show ~2000/100k per INSERT vs ~2000/20 per multi-row INSERT — same total, no new information.
> - Backend CPU via `ps`: OS-level excluded. `total_exec_time` already captures server-side CPU time.

---

## F. Buffer / I/O

| Metric | Source |
|--------|--------|
| `blks_read` Δ | `pg_stat_user_tables` |
| `blks_dirtied` Δ | `pg_stat_user_tables` |

**Dropped:** `blks_written` Δ, `buffers_backend` / `buffers_clean` / `buffers_checkpoint` / `buffers_backend_fsync` (the full "who flushed what" breakdown).
> *Assumptions verified:*
> - Table = 15.8 MB, 2000 pages. Fits in any `shared_buffers` ≥ 32 MB. No eviction pressure → backend writes its own dirty pages at commit, bgwriter does nothing, checkpointer does nothing. The "who" answer is trivially "the backend, all of it."
> - `blks_written ≈ blks_dirtied` always (no eviction → no partial write-back).
> - `blks_read = 0` guaranteed (empty table, nothing to read). Tracked as a validity gate, not an expected differentiator.

---

## G. Checkpoint

| Metric | Source |
|--------|--------|
| `checkpoints_timed + checkpoints_req` Δ | `pg_stat_bgwriter` |

**Dropped:** Checkpoint reason from `log_checkpoints`, WAL rate prediction, `wal_fpi` spike check.
> *Assumptions verified:*
> - `checkpoint_timeout = 300 s`. Worst-case load (method 1, 100k fsyncs on SSD) ≈ 30–60 s. No timed checkpoint will fire.
> - Total WAL ≈ 20 MB vs. `max_wal_size` = 1 GB. No WAL-pressure checkpoint.
> - If Δ = 0 (expected), there's no checkpoint to explain → reason tracking is moot. `log_checkpoints = on` stays in config as insurance, but it's not a tracked column.

---

## H. Verification / End-State

| Metric | Source |
|--------|--------|
| `COUNT(*)` | `SELECT` |
| `pg_relation_size()` | function |

**Dropped:** `relpages`, `pg_total_relation_size()`, `n_live_tup` / `n_dead_tup`, `tuples_inserted` Δ.
> *Assumptions verified:*
> - `relpages` = `pg_relation_size() / 8192`. Pure arithmetic, redundant.
> - `pg_total_relation_size()` = `pg_relation_size()` (no indexes).
> - `n_dead_tup = 0` guaranteed (empty table, pure INSERT, autovacuum off, no prior data).
> - `tuples_inserted` Δ = `COUNT(*)` by definition (single session, no concurrent writes).

---

## Summary Table (15 metrics + 1 note)

| # | Metric | Source | Category |
|---|--------|--------|----------|
| 1 | Wall-clock time | Client timer | A |
| 2 | Throughput (rows/s) | Derived | A |
| 3 | `commits` Δ | `pg_stat_database` | B |
| 4 | `wal_sync` Δ | `pg_stat_wal` | B |
| 5 | `wal_records` Δ | `pg_stat_wal` | C |
| 6 | `wal_bytes` Δ | `pg_stat_wal` | C |
| 7 | Round-trip count | Driver | D |
| 8 | Protocol + batch size | **Note** | D |
| 9 | `total_exec_time` Δ per pattern | `pg_stat_statements` | E |
| 10 | `calls` per pattern | `pg_stat_statements` | E |
| 11 | `blks_read` Δ | `pg_stat_user_tables` | F |
| 12 | `blks_dirtied` Δ | `pg_stat_user_tables` | F |
| 13 | `checkpoints_timed + req` Δ | `pg_stat_bgwriter` | G |
| 14 | `COUNT(*)` | `SELECT` | H |
| 15 | `pg_relation_size()` | function | H |

---

