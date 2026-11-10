"""
1-understand-the-metric.md
    method 1 only, 100 rows, 3 runs, verbose WAL output.
    Goal: understand what each metric / WAL record means.

Prerequisites:
    - python prerequisites.py (merge all parquet files into a single dataset)
    - pip install pyarrow

    Settings : 
    - postgresql v.18, pg_stat_statements loaded (shared libraries), track_io_timing = on,
    track_wal_io_timing = on, autovacuum = off, synchronous_commit = on, fsync = on, 
    data_checksums = on, wal_log_hints = on, full_page_writes = on
    - table trips created; ALTER TABLE trips SET (autovacuum_enabled = false);
    - CREATE EXTENSION pg_walinspect  (superuser or pg_read_server_files)
    - superuser or pg_checkpoint role for CHECKPOINT
    - wal_buffers = 4 MB, checkpoint_timeout = 5 min, checkpoint_completion_target = 0.9
"""

from common import *
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

N_ROWS = 100
WAL_BREAKDOWN = True
RESULTS_FILE = Path(__file__).resolve().parent / "1-results.json"

def main():

    # Initialize
    conn = get_connection(settings)
    cur, stats_cur = get_cursors(conn)
    batch = read_parquet_data_file(N_ROWS)
    rows, cols = load_parquet_rows(batch)

    insert_sql = (
        f"INSERT INTO trips ({','.join(cols)}) "
        f"VALUES ({','.join(['%s'] * len(cols))})"
    )

    # ENV settings
    capture_env(stats_cur)

    results = []
    metrics = run_method(
        conn, 
        cur, 
        stats_cur, 
        settings, 
        name="1. Row-by-row (autocommit ON)", 
        load_func=lambda: method1(conn, cur, rows, insert_sql), 
        wal_breakdown=WAL_BREAKDOWN
    )
   
    RESULTS_FILE.write_text(json.dumps(metrics, indent=4, default=str))

    cur.close()
    stats_cur.close()
    conn.close()
    print("Done")

if __name__ == "__main__":
    main()