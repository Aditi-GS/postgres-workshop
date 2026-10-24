"""
NYC Taxi 100k-row load - 5 methods.
Prerequisites:
    - CREATE EXTENSION IF NOT EXISTS pg_walinspect;
    - python prerequisite.py
    - pip install pyarrow
    - postgresql v.18
    - postgresql.conf (or via SET):
        shared_preload_libraries = 'pg_stat_statements'
        track_io_timing = on          
        track_wal_io_timing = on      
        log_checkpoints = on
        logging_collector = on
        autovacuum = off
        synchronous_commit = on
        fsync = on
    - Table trips created (no indexes) [alembic upgrade head > alembic downgrade -1]
    - ALTER TABLE trips SET (autovacuum_enabled = false);
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import sys
import os
from pathlib import Path 
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings   
import pandas as pd
from time import time
import json
import pyarrow.parquet as pq

# --- DATA ---

file_path = "data/yellow_tripdata_2025.parquet"
abs_path = Path(__file__).resolve().parent.parent / file_path

pf = pq.ParquetFile(abs_path)
first_batch = next(pf.iter_batches(batch_size=100))
df = first_batch.to_pandas()

df.columns = [c.lower() for c in df.columns]
df = df.rename(columns={
    "vendorid": "vendor_id",
    "ratecodeid": "ratecode_id",
    "pulocationid": "pu_location_id",
    "dolocationid": "do_location_id",
})

subset = df.head(100)
cols = list(subset.columns)
rows = subset.values.tolist()  # list of tuples - fast iteration

csv_file = "yellow_tripdata_100k.csv"
subset.to_csv(csv_file, index=False)

print(f"Rows, Columns = {df.shape}")

# --- CONNECTION --- 

conn = psycopg2.connect(
    host=settings.db_host,
    database=settings.db_name,
    user=settings.db_username,
    password=settings.db_password,
    port=settings.db_port,
)
conn.autocommit = True  # psycopg2 sets it False by default
cur = conn.cursor()
stats_cur = conn.cursor(cursor_factory=RealDictCursor)

print("DB connection successful!")

# --- STATS - HELPER FUNCS ---

def wal_breakdown(start_lsn, end_lsn):
    stats_cur.execute(
        'SELECT "resource_manager/record_type" AS rm_type, count, '
        "record_size, fpi_size, combined_size "
        "FROM pg_get_wal_stats(%s, %s, true) "
        "WHERE count > 0 ORDER BY count DESC",
        (start_lsn, end_lsn),
    )
    return stats_cur.fetchall()

def snapshot():
    """Capture all metric sources in one dict."""
    s = {}

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

    # Force this backend to flush all pending stats in its local memory to shared memory when this transaction ends
    stats_cur.execute("SELECT pg_stat_force_next_flush()")

    stats_cur.execute("SELECT pg_current_wal_lsn()")
    s["wal_lsn"] = stats_cur.fetchone()["pg_current_wal_lsn"]

    stats_cur.execute("SELECT SUM(fsyncs) AS fsyncs FROM pg_stat_io WHERE object='wal' AND context='normal'")
    s["wal_activity"] = stats_cur.fetchone()

    stats_cur.execute("SELECT * FROM pg_stat_wal")
    s["wal_stats"] = stats_cur.fetchone()

    stats_cur.execute(
        "SELECT * FROM pg_stat_database WHERE datname = %s",
        (settings.db_name,),
    )
    s["db"] = stats_cur.fetchone()

    stats_cur.execute("SELECT * FROM pg_stat_checkpointer")
    s["checkpointer"] = stats_cur.fetchone()

    stats_cur.execute(
        "SELECT calls, total_exec_time, rows, query, "
        "shared_blks_dirtied, shared_blks_read "
        "FROM pg_stat_statements " 
        "WHERE query ILIKE 'INSERT INTO trips%' "
        "   OR query ILIKE 'COPY trips%' "
        "ORDER BY total_exec_time DESC"
    )
    s["stmts"] = stats_cur.fetchall()

    stats_cur.execute("SELECT pg_size_pretty(pg_relation_size('trips'))")
    s["rel_size"] = stats_cur.fetchone()["pg_size_pretty"]
    stats_cur.execute("SELECT pg_relation_size('trips')")
    s["rel_size_bytes"] = stats_cur.fetchone()["pg_relation_size"]

    return s


def compute_metrics(before, after, wall_time):
    """Compute the 15-metric dict from two snapshots."""
    wal_activity_b, wal_activity_a = before["wal_activity"], after["wal_activity"]
    wal_stats_b, wal_stats_a = before["wal_stats"], after["wal_stats"]
    db_b, db_a = before["db"], after["db"]
    ckpt_b, ckpt_a = before["checkpointer"], after["checkpointer"]

    # Executor + Buffer - sum across all matching statements
    rows = sum(r["rows"] for r in after["stmts"])
    exec_time = sum(r["total_exec_time"] for r in after["stmts"])
    calls = sum(r["calls"] for r in after["stmts"])
    blks_dirtied = sum(r["shared_blks_dirtied"] for r in after["stmts"])
    # blks_written = sum(r["shared_blks_written"] for r in after["stmts"]) // blks written per statement -> > 0 in case of eviction
    blks_read = sum(r["shared_blks_read"] for r in after["stmts"])

    return {
        # 1. Headline
        "time_ms": (round(wall_time, 3) * 1000),
        "rows_per_sec": round(rows / wall_time),

        # 2. Transaction & Commit
        "xact_commit": db_a["xact_commit"] - db_b["xact_commit"],
        "fsyncs (wal)": int(wal_activity_a["fsyncs"] - wal_activity_b["fsyncs"]),

        # 3. WAL
        "wal_records": wal_stats_a["wal_records"] - wal_stats_b["wal_records"],
        "wal_bytes": int(wal_stats_a["wal_bytes"] - wal_stats_b["wal_bytes"]),

        # 5. Executor
        "total_exec_time_ms": round(exec_time, 2),
        "stmt_calls": calls,    # round trips (client <-> server)

        # 6. Buffer
        "blks_dirtied": blks_dirtied,
        "blks_read": blks_read,

        # 7. Checkpoint
        "ckpt_timed": ckpt_a["num_timed"] - ckpt_b["num_timed"],
        "ckpt_req":   ckpt_a["num_requested"] - ckpt_b["num_requested"],         

        # 8. Verification
        "relation_size": after["rel_size"],
        "rows": rows,
        "pages": (int(after["rel_size_bytes"])/8192),

        "start_lsn": before["wal_lsn"],
        "end_lsn": after["wal_lsn"],
        "wal_fpi": wal_stats_a["wal_fpi"] - wal_stats_b["wal_fpi"],
        "wal_breakdown": wal_breakdown(before["wal_lsn"], after["wal_lsn"])        
    }


def truncate_and_reset():
    """Reset all stats and clear the table."""
    cur.execute("TRUNCATE TABLE trips RESTART IDENTITY")
    stats_cur.execute("SELECT pg_stat_reset()")
    stats_cur.execute("SELECT pg_stat_statements_reset()")
    # resets archiver, bgwriter, checkpointer, io, recovery_prefetch, slru, wal
    # since target not mentioned
    stats_cur.execute("SELECT pg_stat_reset_shared()")   
    # flush any leftover dirty pages in the buffer pool from prior runs or operations
    # reset the checkpoint counters to a known state so num_timed / num_requested deltas reflect only the load.
    cur.execute("CHECKPOINT")
    conn.commit()


# --- RUN SINGLE METHOD ---

def run_method(name, load_func):
    print(f"Running {name}")
    truncate_and_reset()
    before = snapshot()

    t0 = time()
    load_func()
    t1 = time()

    after = snapshot()

    m = compute_metrics(before, after, t1 - t0)
    m["method"] = name
    return m


# --- 5 POPULATE DB METHODS ---

INSERT_SQL = (
    f"INSERT INTO trips ({','.join(cols)}) "
    f"VALUES ({','.join(['%s'] * len(cols))})"
)


def method1():
    """1. Row-by-row, autocommit ON -> 100k xacts, 100k fsyncs."""
    conn.autocommit = True
    for row in rows:
        cur.execute(INSERT_SQL, row)


def method2():
    """2. Row-by-row, autocommit OFF -> 1 xact, 1 fsync."""
    conn.autocommit = False
    for row in rows:
        cur.execute(INSERT_SQL, row)
    conn.commit()


def method3(batch_size=1000):
    """3. Batched commits (1000/batch) -> 100 xacts, 100 fsyncs."""
    conn.autocommit = False
    for i in range(0, len(rows), batch_size):
        for row in rows[i : i + batch_size]:
            cur.execute(INSERT_SQL, row)
        conn.commit()


def method4(batch_size=5000):
    """4. Multi-row INSERT (5000/batch) -> 20 statements, 1 xact, 1 fsync."""
    conn.autocommit = False
    placeholders = ",".join(["(%s)" * len(cols)] * batch_size)
    sql = f"INSERT INTO trips ({','.join(cols)}) VALUES {placeholders}"
    for i in range(0, len(rows), batch_size):
        flat = [v for row in rows[i : i + batch_size] for v in row]
        cur.execute(sql, flat)
    conn.commit()


def method5():
    """5. COPY -> 1 command, 1 xact, 1 fsync."""
    conn.autocommit = False
    with open(os.path.abspath(csv_file), "r") as f:
        cur.copy_expert(
            f"COPY trips({','.join(cols)}) FROM STDIN WITH CSV HEADER DELIMITER ','",
            f,
        )
    conn.commit()


# ─── Run all 5 

results = [
    run_method("1. Row-by-row (autocommit ON)", method1),
    # run_method("2. Row-by-row (autocommit OFF)", method2),
    # run_method("3. Batched commits (1000/batch)", method3),
    # run_method("4. Multi-row INSERT (5000/batch)", method4),
    # run_method("5. COPY", method5),
]

# Dump full JSON for later analysis
with open("part_1_results.json", "w") as f:
    json.dump(results, f, indent=2, default=str)

# --- CLEANUP --- 
print("\nFull results saved to part_1_results.json")
cur.close()
stats_cur.close()
conn.close()
print("Done.")   


