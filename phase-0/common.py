import os
import sys
from pathlib import Path
# adds a specified directory to the module search path list
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings
import psycopg2
from psycopg2.extras import RealDictCursor
import pyarrow.parquet as pq
import pyarrow.csv as pa_csv
import statistics 
from time import time
import json

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ALL_DATA_PARQUET_FILE = DATA_DIR / "yellow_tripdata_2025.parquet"

RENAME_COLS_MAP = {
    "vendorid": "vendor_id",
    "ratecodeid": "ratecode_id",
    "pulocationid": "pu_location_id",
    "dolocationid": "do_location_id",
} 

GUC_LIST = [
    "server_version", 
    "data_checksums", 
    "full_page_writes",
    "synchronous_commit", 
    "fsync", 
    "wal_buffers",
    "autovacuum",
    "checkpoint_timeout", 
    "checkpoint_completion_target",
    "track_io_timing", 
    "track_wal_io_timing",
]

# ----- CONNECTION + CURSORS -----

def get_connection(settings):
    conn = psycopg2.connect(
        host=settings.db_host,
        database=settings.db_name,
        user=settings.db_username,
        password=settings.db_password,
        port=settings.db_port,        
    )

    conn.autocommit = True
    print("----- DB Connection Successful -----")
    return conn

def get_cursors(conn):
    cur = conn.cursor()
    stats_cur = conn.cursor(cursor_factory=RealDictCursor)
    return cur, stats_cur

# ----- READ/LOAD & WRITE DATA -----

def read_parquet_data_file(num_rows):
    pf = pq.ParquetFile(ALL_DATA_PARQUET_FILE)
    '''
        ->  pf.iter_batches(batch_size=num_rows) returns a lazy iterator that yields pyarrow.RecordBatch objects, 
            each containing up to num_rows rows.
        ->  next(...) pulls the first batch from that iterator,
            so only one batch (<= num_rows rows) is actually read from disk.
        ->  it avoids reading the whole file when you only need a small preview.
    '''    
    batch = next(pf.iter_batches(batch_size=num_rows))
    if batch.num_rows < num_rows:
        raise RuntimeError(f"Only got {batch.num_rows} rows instead of {num_rows}")
    return batch

def _rename_parquet_columns(pq_batch):
    batch = pq_batch.rename_columns(
        [RENAME_COLS_MAP.get(col.lower(), col.lower()) for col in pq_batch.column_names]
    )
    return batch

def create_subset_csv(pq_batch):
    batch = _rename_parquet_columns(pq_batch)
    csv_file = f"yellow_tripdata_{batch.num_rows}.csv"
    pa_csv.write_csv(batch, csv_file)

def load_parquet_rows(pq_batch): 
    batch = _rename_parquet_columns(pq_batch)
    cols = batch.column_names
    # Convert the RecordBatch to a list of rows / dictionaries.
    rows = batch.to_pylist()
    rows = [tuple(r[c] for c in cols) for r in rows]
    return rows, cols

# ----- ENVIRONMENT -----

def capture_env(stats_cur):
    meta = {}
    for setting in GUC_LIST: 
        stats_cur.execute(f"SHOW {setting}")
        meta[setting] = stats_cur.fetchone()[setting]
    print(meta)
    stats_cur.execute("CREATE EXTENSION IF NOT EXISTS pg_walinspect")

# ----- STATS -----

def snapshot(conn, stats_cur, settings, phase):
    '''
    Unlike pg_stat_statments which uses statement-completion hook, the cumulative stats/info
    are throttled by `PGSTAT_MIN_INTERVAL` (1s) or `stats_flush_interval` (10s). If the run finishes before 
    the relevant interval then the stats views won't display the updated stats because it wasn't flushed from 
    the backend's local memory in the first place.
    '''
    '''
    For autocommit ON: force flush works (each stmt is its own txn) ---> including the force flush stmt
    For autocommit OFF: data is already flushed by load_func's commit (pgstat_flush() gets called implicitly at explicit commits)
    so there's (nothing pending to flush) ---> force flush redundant (harmless)
    '''

    """Capture metrics. phase='before' or 'after' controls LSN position."""
    
    snapshot = {}

    # For "after": capture LSN FIRST
    if phase == "after":
        stats_cur.execute("SELECT pg_current_wal_lsn() AS lsn")
        snapshot["lsn"] = stats_cur.fetchone()["lsn"]
 
    # Force this backend to flush all pending stats in its local memory to shared memory when this transaction ends 
    stats_cur.execute("SELECT pg_stat_force_next_flush()")

    # WAL I/O (fsync count + time)
    stats_cur.execute(
        "SELECT SUM(fsyncs) AS fsyncs, SUM(fsync_time) AS fsync_time, SUM(writes) as writes, SUM(write_time) AS write_time "
        "FROM pg_stat_io WHERE object='wal' AND context='normal' AND backend_type='client backend'"
    )
    snapshot["wal_io"] = stats_cur.fetchone()

    stats_cur.execute("SELECT * FROM pg_stat_wal")
    snapshot["wal_stats"] = stats_cur.fetchone()

    stats_cur.execute("SELECT * FROM pg_stat_database WHERE datname = %s", (settings.db_name,))
    snapshot["db"] = stats_cur.fetchone()

    stats_cur.execute("SELECT * FROM pg_stat_checkpointer")
    snapshot["checkpointer"] = stats_cur.fetchone()

    stats_cur.execute(
        "SELECT calls, total_exec_time, rows, query, "
        "shared_blks_dirtied, shared_blks_read "
        "FROM pg_stat_statements "
        "WHERE query ILIKE 'INSERT INTO trips%%' "
        "   OR query ILIKE 'COPY trips%%' "
        "ORDER BY total_exec_time DESC"
    )
    snapshot["stmts"] = stats_cur.fetchall()

    stats_cur.execute("SELECT pg_size_pretty(pg_relation_size('trips'))")
    snapshot["rel_size"] = stats_cur.fetchone()["pg_size_pretty"]
    stats_cur.execute("SELECT pg_relation_size('trips')")
    snapshot["rel_size_bytes"] = stats_cur.fetchone()["pg_relation_size"]

    # For "before": capture LSN LAST
    if phase == "before":
        stats_cur.execute("SELECT pg_current_wal_flush_lsn() AS lsn")
        snapshot["lsn"] = stats_cur.fetchone()["lsn"]

    conn.commit() # Refer to 'notes' at the bottom of the file
    return snapshot    

def wal_breakdown(conn, stats_cur, start_lsn, end_lsn):
    try:
        stats_cur.execute(
            'SELECT "resource_manager/record_type" AS rm_type, count, '
            "record_size, fpi_size, combined_size "
            "FROM pg_get_wal_stats(%s, %s, true) "
            "WHERE count > 0 ORDER BY count DESC",
            (start_lsn, end_lsn),
        )
        return stats_cur.fetchall()
    except psycopg2.Error as e:
        # e.g. old WAL segments already recycled by a checkpoint during a long run
        conn.rollback()
        return f"unavailable: {e.pgerror or e}"
    
def compute_metrics(conn, stats_cur, before, after, wall_time, do_wal_breakdown):
    wal_io_b, wal_io_a = before["wal_io"], after["wal_io"]
    wal_stats_b, wal_stats_a = before["wal_stats"], after["wal_stats"]
    db_b, db_a = before["db"], after["db"]
    ckpt_b, ckpt_a = before["checkpointer"], after["checkpointer"]

    rows = sum(r["rows"] for r in after["stmts"])
    exec_time = sum(r["total_exec_time"] for r in after["stmts"])
    calls = sum(r["calls"] for r in after["stmts"])
    shared_blks_dirtied = sum(r["shared_blks_dirtied"] for r in after["stmts"])
    shared_blks_read = sum(r["shared_blks_read"] for r in after["stmts"])

    metrics = {
        "time_sec": round(wall_time , 3),
        "rows_per_sec": round(rows / wall_time),

        "xact_commits": db_a["xact_commit"] - db_b["xact_commit"],
        "fsyncs": int(wal_io_a["fsyncs"] - wal_io_b["fsyncs"]),
        "fsync_time_sec": round((wal_io_a["fsync_time"] - wal_io_b["fsync_time"]) / 1000, 6),
        "writes": int(wal_io_a["writes"] - wal_io_b["writes"]),
        "write_time_sec": round((wal_io_a["write_time"] - wal_io_b["write_time"]) / 1000, 6),

        "wal_records": wal_stats_a["wal_records"] - wal_stats_b["wal_records"],
        "wal_bytes": int(wal_stats_a["wal_bytes"] - wal_stats_b["wal_bytes"]),
        "wal_fpi": wal_stats_a["wal_fpi"] - wal_stats_b["wal_fpi"],

        "total_exec_time_sec": round(exec_time / 1000, 3),
        "stmt_calls": calls,

        "shared_blks_dirtied": shared_blks_dirtied,
        "shared_blks_read": shared_blks_read,

        "ckpt_timed": ckpt_a["num_timed"] - ckpt_b["num_timed"],
        "ckpt_req": ckpt_a["num_requested"] - ckpt_b["num_requested"],

        "relation_size": after["rel_size"],
        "rows": rows,
        "pages": int(after["rel_size_bytes"]) // 8192,
    }

    # WAL breakdown only when configured (expensive scan)
    if do_wal_breakdown:
        metrics["start_lsn"] = before["lsn"]
        metrics["end_lsn"] = after["lsn"]
        metrics["wal_breakdown"] = wal_breakdown(conn, stats_cur, before["lsn"], after["lsn"])

    return metrics

# ----- RESET -----
def truncate_and_reset(conn, cur, stats_cur):
    """Reset all stats and clear the table (child table only)."""
    cur.execute("TRUNCATE TABLE trips RESTART IDENTITY")        
    stats_cur.execute("SELECT pg_stat_reset()")
    stats_cur.execute("SELECT pg_stat_statements_reset()")
    stats_cur.execute("SELECT pg_stat_reset_shared()")
    conn.commit()
    cur.execute("CHECKPOINT")
    stats_cur.execute("SELECT relfilenode FROM pg_class WHERE relname = 'trips'")
    trips_filenode = stats_cur.fetchone()["relfilenode"]
    return trips_filenode

# ---------- RUN SINGLE METHOD ----------

def run_method(
    conn, cur, stats_cur, settings, name, 
    load_func, wal_breakdown
):
    # flush=True : force flush the print() output buffer immediately
    print(f"Running {name}", flush=True)
    trips_relfilenode = truncate_and_reset(conn, cur, stats_cur)
    print(f"Trips Relfilenode = {trips_relfilenode}")
    before = snapshot(conn, stats_cur, settings, "before")

    t0 = time()
    try:
        load_func()
    except Exception as e:
        conn.rollback()
        print(f"----- ERROR: {name} failed: {e} -----", flush=True)
        return {"method": name, "error": str(e)}
    t1 = time()

    after = snapshot(conn, stats_cur, settings, "after")

    metrics = compute_metrics(conn, stats_cur, before, after, t1 - t0, wal_breakdown)
    metrics["method"] = name
    if "start_lsn" in metrics: 
        print(f"----- Start LSN = {metrics["start_lsn"]} End LSN = {metrics["end_lsn"]} -----")

    return metrics

# ---------- 5 POPULATE DB METHODS ----------

def method1(conn, cur, rows, insert_sql):
    """1. Row-by-row, autocommit ON -> one xact + one commit flush per row."""
    conn.autocommit = True
    for row in rows:
        cur.execute(insert_sql, row)


def method2(conn, cur, rows, insert_sql):
    """2. Row-by-row, autocommit OFF -> 1 xact, 1 commit flush."""
    conn.autocommit = False
    for row in rows:
        cur.execute(insert_sql, row)
    conn.commit()


def method3(conn, cur, rows, batch_size, insert_sql):
    """3. Batched commits (batch_size rows/commit)."""
    conn.autocommit = False
    for i in range(0, len(rows), batch_size):
        for row in rows[i : i + batch_size]:
            cur.execute(insert_sql, row)
        conn.commit()


# PG limits relevant to method 4 (multi-row INSERT with bind params):
#   - Query string: 1 GB (MaxAllocSize) - not a concern here
#   - Query parameters: 65,535 (hard protocol limit) -> 
#     batch_insert_size must be ≤ 65535 // len(cols)
#   - track_activity_query_size (default 1024 B) only truncates
#     pg_stat_activity.display, does not limit execution     

def method4(conn, cur, cols, rows, batch_size):
    """4. Multi-row INSERT (batch_size rows per statement), 1 xact."""
    conn.autocommit = False
    row_ph = "(" + ",".join(["%s"] * len(cols)) + ")"
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        sql = (f"INSERT INTO trips ({','.join(cols)}) VALUES "
               + ",".join([row_ph] * len(chunk)))
        cur.execute(sql, [v for row in chunk for v in row])
    conn.commit()


def method5(conn, cur, csv_file, cols):
    """5. COPY -> 1 command, 1 xact."""
    conn.autocommit = False
    with open(os.path.abspath(csv_file), "r") as f:
        cur.copy_expert(
            f"COPY trips({','.join(cols)}) FROM STDIN WITH CSV HEADER DELIMITER ','",
            f,
        )
    conn.commit()

# ----- SUMMARY ----- 

def summarize(results, summary_file):
    by_method = {}
    for r in results:
        if not r.get("warmup") and "error" not in r:
            by_method.setdefault(r["method"], []).append(r)

    show = ["time_sec", "rows_per_sec", "wal_records", "wal_bytes", "wal_fpi", "fsyncs"]
    must_match = ["stmt_calls", "rows", "xact_commits"]

    summary = {}
    for method, runs in by_method.items():
        entry = {"rounds": len(runs)}
        for k in show:
            if k not in runs[0]:
                continue
            vals = [r[k] for r in runs]
            entry[k] = {
                "mean": round(statistics.mean(vals), 3),
                "median": statistics.median(vals),
                "min": min(vals),
                "max": max(vals),
            }
        for k in must_match:
            vals = {r[k] for r in runs}
            if len(vals) > 1:
                entry[f"{k}_mismatch"] = sorted(vals)
        summary[method] = entry

    Path(summary_file).write_text(json.dumps(summary, indent=4, default=str))   

### notes : 
"""
ROUND 1 - method1 (autocommit ON)
    conn.autocommit = True
  
ROUND 1 - method2 (autocommit OFF)
───────────────────────────────────

    conn.autocommit = True          ← run_method resets (fix)
    truncate_and_reset()            ← runs in autocommit mode ✓
    snapshot("before")              ← autocommit, no txn ✓
    ┌─────────────────────────────┐
    │ method2:                    │
    │   conn.autocommit = False   │ ← starts implicit txn
    │   INSERT x 100k             │
    │   conn.commit()             │ ← txn ends, stats flushed
    └─────────────────────────────┘
    snapshot("after")               ← autocommit still False!
                                        stats_cur queries open a NEW
                                        implicit txn (never committed)
  ─────────────────────────────────
    conn state at end of round: autocommit=False, txn OPEN

ROUND 2 - method3
───────────────────────────────────

    conn.autocommit = True          ←  ERROR HERE
                                        "set_session cannot be used
                                        inside a transaction"
                                        (the txn from snapshot("after")
                                        is still open)   
"""