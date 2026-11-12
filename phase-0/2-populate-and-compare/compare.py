"""
Compare all 5 load methods.

    100k rows, methods (1,2,3,4,5) x 5 rounds.
    Goal: compare methods.

Prerequisites:
    - python prerequisites.py
    - pip install pyarrow
    - PostgreSQL v.18 with pg_stat_statements, track_io_timing=on,
      track_wal_io_timing=on, autovacuum=off, synchronous_commit=on,
      fsync=on, data_checksums=on, wal_log_hints=on, full_page_writes=on
    - table trips created; ALTER TABLE trips SET (autovacuum_enabled = false);
    - CREATE EXTENSION pg_walinspect
    - superuser or pg_checkpoint role
    - wal_buffers=4MB, checkpoint_timeout=5min, checkpoint_completion_target=0.9
"""

import json
import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import settings
from common import (
    get_connection, get_cursors,
    read_parquet_data_file, load_parquet_rows, create_subset_csv,
    capture_env, run_method,
    method1, method2, method3, method4, method5,
    summarize,
)

# ---------- CONFIG ----------
N_ROWS = 100_000
METHODS = [1, 2, 3, 4, 5]
REPEATS = 5
WAL_BREAKDOWN = False
BATCH_COMMIT_SIZE = 1000
BATCH_INSERT_SIZE = 3000

CWD = Path(__file__).resolve().parent
RESULTS_FILE = CWD / "2_results.json"
SUMMARY_FILE = CWD / "2_summary.json"

# ---------- DATA ----------
batch = read_parquet_data_file(N_ROWS)
rows, cols = load_parquet_rows(batch)
create_subset_csv(batch)

# ---------- CONNECTION ----------
conn = get_connection(settings)
cur, stats_cur = get_cursors(conn)

# ---------- ENVIRONMENT (one-time) ----------
capture_env(stats_cur)

# ---------- BUILD METHOD LIST ----------
INSERT_SQL = (
    f"INSERT INTO trips ({','.join(cols)}) "
    f"VALUES ({','.join(['%s'] * len(cols))})"
)

ALL_METHODS = {
    1: ("1. Row-by-row (autocommit ON)",
        lambda: method1(conn, cur, rows, INSERT_SQL)),
    2: ("2. Row-by-row (autocommit OFF)",
        lambda: method2(conn, cur, rows, INSERT_SQL)),
    3: ("3. Batched commits (1000/batch)",
        lambda: method3(conn, cur, rows, BATCH_COMMIT_SIZE, INSERT_SQL)),
    4: ("4. Multi-row INSERT (3000/batch)",
        lambda: method4(conn, cur, cols, rows, BATCH_INSERT_SIZE)),
    5: ("5. COPY",
        lambda: method5(conn, cur, f"yellow_tripdata_{N_ROWS}.csv", cols)),
}

METHODS_TO_RUN = [(ALL_METHODS[i][0], ALL_METHODS[i][1]) for i in METHODS]

# ---------- RUN ----------
results = []

for rnd in range(REPEATS):
    print(f"\n----- Round {rnd + 1}/{REPEATS} -----")

    for name, func in METHODS_TO_RUN:
        m = run_method(conn, cur, stats_cur, settings, name, func, WAL_BREAKDOWN)
        m["round"] = rnd + 1
        results.append(m)
        # save after every run so a crash doesn't lose finished runs
        with open(RESULTS_FILE, "w") as f:
            json.dump(results, f, indent=4, default=str)

# ---------- SUMMARY ----------
summarize(results, SUMMARY_FILE)

# ---------- CLEANUP ----------
print(f"\n----- Results saved to {RESULTS_FILE} -----\n")
cur.close()
stats_cur.close()
conn.close()
print("Done.")   