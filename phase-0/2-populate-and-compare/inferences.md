# Populate DB and Compare - Inferences

## Estimate vs Actual 

||estimate|actual|
|-|-------|------|
|relation_size|12 MB|12 MB|
|pages|1429|1539|

`ckpt_req=0` => No WAL-driven checkpoint was triggered so the WAL was being generated at a steady rate and didn't come close to `max_wal_size` for any of the methods. 

## Methods per Metric Comparison

## stmt_calls | writes | fsyncs

| metric | row-by-row ON | row-by-row OFF | batched commits | multi-row insert | copy |
|--------|---------------|----------------|-----------------|------------------|------|
| stmt_calls | 100000 | 100000 | 100000 | 34 | 1 |
| writes | 100013 | 1 | 96 | 2 | 1593 |
| fsyncs | 100003 | 1 | 94 | 2 | 2 |
| xact_commits | 100004 | 9 | 93 | 3 | 3 |

- for xact_commits, SELECT queries executed as a part of before and after snapshots are included as well 
- methods 1, 2, 3 all executed 100_000 statements, i.e., 100_000 INSERT commands. Then 100k round trips were done between client and server
- Since batch size was 3000 for multi-row insert, 3000 x 34 = 102k (any lesser won't cover 100k inserts), 34 statement calls of the type INSERT INTO VALUES were made where multiple rows (3000) were inserted in a single transaction by a single INSERT. 
- Upon parsing the COPY command, a single transaction is started hence only a single statement call is made for COPY. 
- for 100k inserts:
    - method 1 performed ~100k writes + fsyncs + commits as each INSERT statement is considered to be a transaction
    - method 2, since autocommit is OFF, all the 100k INSERT statements are considered as a single transaction - hence a single write and fsync. Based on write and fsync alone - method 2 performs the best. 
    - method 3, even though the inserts were done in batches, the INSERT statement was executed 100k times. Since the batch size was 1000 for batched commits, ~ 100 writes, fsyncs and commits were done

??? make sense of write and fsync numbers for method 4 and 5 ???

### time_sec | total_exec_time_sec | write_time_sec | fsync_time_sec

| metric | row-by-row ON | row-by-row OFF | batched commits | multi-row insert | copy |
|--------|---------------|----------------|-----------------|------------------|------|
| stmt_calls | 100000 | 100000 | 100000 | 34 | 1 |
| total_exec_time_sec | 11.48 | 1.67 | 1.56 | 1.14 | 1.11 |
| write_time_sec | 1.763 | 0.0 | 0.008 | 0.0 | 0.005 |
| fsync_time_sec | 385.216 | 0.008 | 0.493 | 0.009 | 0.009 | 
| time_sec | 435.978 | 6.66 | 6.768 | 3.866 | 1.12 |

- method 1 takes the maximum amount of time and method 5 the least. 
- method 1 - committing row-by-row where each insert counts as a transaction - takes around 7.3 mins to finish executing. 
- methods 2 and 3 took nearly the same amount of time to finish 
- batched commits and row-by-row (autocommit OFF) insert take about the same total time.
- since all 100k INSERTs are treated as a single transaction in method 2, the write and fsync takes the least amount of time out of all methods. 
- Out of 435.978s, method 1 spent: 
    - 2.6% of the time exectuting the statements
    - 88.8% writing + fsyncing WAL form wal buffers to OS cache to the disk
    - Remaining 37.5s (8.6%) is spent on client side processing and server overhead 
- Out of 6.66s, method 2 spent: 
    - 25% of the the time executing the statements 
    - 0.12% of the time fsyncing 
- Out of 6.768s, method 3 spent: 
    - 23% executing the statements in batches
    - 0.12% writing wal from buffers to OS page cache
    - 7.3% fsyncing to pg_wal
- Out of 3.866s, method 4 spent: 
    - ~30% executing the multi row insert statments 34 times
    - 0.23% fsyncing 
    - remaining time might have been spent on parsing, analyzing, planning, server overhead and client side processing 
- Out of 1.12s, method 5 spent:
    - 99.1% streaming the data into the table 
    - 1.25% writing and fsyncing wal into OS cache and then pg_wal on disk 

=> Method 1 spent majority of time fsyncing
=> Both methods 2 and 3 took about the same time to execute the statements 
=> Methods 2, 3 and 4 all took nearly the same amount of time to fsync wal to the disk
=> Method 5 took the least amount of time to execute

- Method 1 took nearly 389 times more time to finish than Method 5 

??? why is write_time_sec 0.0 for methods 2 and 4 ???

### shared_blks_read

```sql
SELECT c.relname, b.relfilenode, relforknumber, relblocknumber FROM 
pg_buffercache b JOIN pg_class c
ON b.relfilenode = c.relfilenode
WHERE isdirty='true';
```

- Common across all methods : 
    - vendor_lookup = 1
    - ratecode_lookup = 1
    - payment_type_lookup = 1
    - taxi_zones_lookup = 3
    - trips_trip_id_seq = 1
    - trips_pkey = 276    

- Row-by-Row (autocommit ON): total = 1832
    - trips = 1549

- Row-by-Row (autocommit OFF) | Batched Commits | Multi-row INSERT: total = 1823
    - trips = 1540

- COPY: total  = 1825 
    - trips = 1542

So the number of pages of the relation 'trips' is the one that differs for different methods. 

??? why ??? 

### wal_records | wal_bytes | wal_fpi

| metric | row-by-row ON | row-by-row OFF | batched commits | multi-row insert | copy |
|--------|---------------|----------------|-----------------|------------------|------|
| wal_records | 797764 | 203583 | 197645 | 203605 | 105307 |
| wal_bytes | 49.8 MB | 21 MB | 19.8 MB | 21 MB | 16.7 MB |
| wal_fpi | 22 | 8 | 12 | 12 | 14 |
| ckpt_timed | 1 | 0 | 0 | 0 | 0 |

- Method 1 took 7.3 mins to finish and `checkpoint_timeout = 5min`. So a timed checkpoint was triggered during the run. FPI are written when the first modification to a page is made after the last checkpoint. This explains why method 1 has the maximum wal_records, wal_bytes and wal_fpi. 

??? other inferences made based on the above metrics ???

??? other inferences and conclusions to make based on all the observations ???