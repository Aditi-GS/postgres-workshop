# Table of Contents: 

Before running the python files, [run and merge](../prerequisite.py) all the parquet files from data directory into a single year consolidated parquet file named `yellow_tripdata_2025.parquet`.

Prerequisites:
    - pip install pyarrow
    - PostgreSQL v.18 with pg_stat_statements, track_io_timing=on,
      track_wal_io_timing=on, autovacuum=off, synchronous_commit=on,
      fsync=on, data_checksums=on, wal_log_hints=on, full_page_writes=on
    - table trips created; ALTER TABLE trips SET (autovacuum_enabled = false);
    - CREATE EXTENSION pg_walinspect
    - superuser or pg_checkpoint role
    - wal_buffers=4MB, checkpoint_timeout=5min, checkpoint_completion_target=0.9

1. Understand the Metrics
    - [Code](./1-understand-the-metrics/understand.py)
    - [Observations](./1-understand-the-metrics/observations.md)
    - [Notes](./1-understand-the-metrics/notes.md)
    - [Inferences](./1-understand-the-metrics/inferences.md)
2. Populate DB and Compare
    - [Code](./2-populate-and-compare/compare.py)
    - [Observations](./2-populate-and-compare/observations.md)
    - [Inferences](./2-populate-and-compare/inferences.md)
--- 