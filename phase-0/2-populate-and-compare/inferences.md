# Populate DB and Compare - Inferences

## Estimate vs Actual 

||estimate|actual|
|-|-------|------|
|relation_size|12 MB|12 MB|
|pages|1539|1539|

`ckpt_req=0` => No WAL-driven checkpoint was triggered so the WAL was being generated at a steady rate and didn't come close to `max_wal_size` for any of the methods. 

### Questions 
1. Why does COPY result in 1552 pages when all other methods with 1539 pages ? 

2. For method 1, shouldn't shared_blks_read be >1 ? After a checkpoint, page that was being written to can be loaded again if it has free space right ?

=> The timed checkpoint only flushes the dirty buffers to the disk, not evict them. Hence the page being written still is present in the shared buffers even after checkpoint is performed. No eviction took place - neither by checkpoint nor by clock-sweep algorithm as shared_buffers is 128 MB and the relation is 12 MB - it easily fits in.

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
    - method 2, since autocommit is OFF, all the 100k INSERT statements are considered as a single transaction - hence a single write and fsync. Based on write and fsync alone - method 2 performs the best - the client backend did the least WAL I/O itself. 
    - method 3, even though the inserts were done in batches, the INSERT statement was executed 100k times. Since the batch size was 1000 for batched commits, ~ 100 writes, fsyncs and commits were done
- method 4 has 2 writes and fsyncs for a multi-row insert that was performed 34 times. This is because autocommit was disabled hence all 34 inserts are treated as a part of a single transaction - leading to 1 write and fsync for flushing data, +1 for the commit record.

### Questions
1. method 3 - batched commits - had 100 transactions executed - but the wirtes, fsyncs and xact_commits fall short of 100. Why ?

2. for method 5, what does the 1593 writes for single COPY command indicate ? We are collecting writes from `pg_stat_io` for `object='wal' and backend_type='client backend'`. Which means that the client/application had to evict dirty WAL buffers to write to OS cache 1593 times itself. Does that mean walwriter was not able to keep up with the rate ?

3. Let's take method 2 for example. WAL buffers are 4 MB. The WAL generated is 21 MB. That means the WAL get's full ~ 5 times. But write is only 1. Does this mean wal writer was clearing it up in the background and we only calculated client forced writes and fsyncs ? 

## time_sec | total_exec_time_sec | write_time_sec | fsync_time_sec

| metric | row-by-row ON | row-by-row OFF | batched commits | multi-row insert | copy |
|--------|---------------|----------------|-----------------|------------------|------|
| stmt_calls | 100000 | 100000 | 100000 | 34 | 1 |
| total_exec_time_sec | 11.48 | 1.67 | 1.56 | 1.14 | 1.11 |
| write_time_sec | 1.763 | 0.0 | 0.008 | 0.0 | 0.005 |
| fsync_time_sec | 385.216 | 0.008 | 0.493 | 0.009 | 0.009 | 
| time_sec | 435.978 | 6.66 | 6.768 | 3.866 | 1.12 |

- method 1 takes the maximum amount of time and method 5 the least. 
- method 1 - committing row-by-row where each insert counts as a transaction - takes around 7.3 mins to finish executing i.e., 100k of insertions + 100k of round trips
- methods 2 and 3 took nearly the same amount of time to finish
    - method 3 took maximum amount of time in fsyncing (~0.5s more) than method 2
    - both took nearly the same time to execute (each INSERT statement has to be executed regardless of the transaction boundaries in method 3)
    - rather than I/O, majority of the time was taken in round trips for both 
- the wall-clock time per statement on an average is (wall clock time/stmt_calls):
    - method 1 ≈ 4 ms
    - method 2 ≈ 66.6 µs
    - method 3 ≈ 67.68 µs
    - method 4 ≈ 113 ms
    - method 5 = 1.12 s
- method 4 took more time because :
    - 
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

- Method 1's fsync time (385s) is ~43,000x Method 2's fsync time (0.008s). It proves that transaction boundary placement is the dominant performance factor for write-heavy workloads - more than WAL volume, more than I/O method; but we would lose durability

=> Method 1 spent majority of time fsyncing
=> Both methods 2 and 3 took about the same time to execute the statements 
=> Methods 2 and 4 all took nearly the same amount of time to fsync wal to the disk
=> Method 5 took the least amount of time to execute

> Method 1 took nearly 389 times more time to finish than Method 5 

### Questions
1. Why is `write_time_sec=0.0` for methods 2 and 4 ?

## shared_blks_dirtied

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

### Questions
1. The number of pages of the relation 'trips' is the one that differs for different methods. Why ?

## wal_records | wal_bytes | wal_fpi

| metric | row-by-row ON | row-by-row OFF | batched commits | multi-row insert | copy |
|--------|---------------|----------------|-----------------|------------------|------|
| wal_records | 797764 | 203583 | 197645 | 203605 | 105307 |
| wal_bytes | 49.8 MB | 21 MB | 19.8 MB | 21 MB | 16.7 MB |
| wal_fpi | 22 | 8 | 12 | 12 | 14 |
| ckpt_timed | 1 | 0 | 0 | 0 | 0 |

- method 1 took 7.3 mins to finish and `checkpoint_timeout = 5min`. So a timed checkpoint was triggered during the run. FPI are written when the first modification to a page is made after the last checkpoint. This explains why method 1 has the maximum wal_records, wal_bytes and wal_fpi. 
- method 2 and 4 generate the same amount of WAL but method is 4 is faster due to it reducing per-row index maintenance overhead.
- method 2 generated 21 MB of WAL. 21 MB through a 4 MB `wal_buffers` means the buffers were written out several times, mostly not by the client backend.
- COPY generated the least amount of WAL because COPY uses heap_multi_insert, which writes about one heap record per page instead of one per row.
- the least amount of WAL is generated by method 5 - this is because WAL is generated per heap page not per row. 

## WAL Records Breakdown

|methods =>|m1|m2|m3|m4|m5|
|----------|--|--|--|--|--|
|observed| 797764 | 203583 | 197645 | 203605 | 105307 |
|heap|100,000|100,000|100,000|100,000| ~ 1.5k (one per page = heap_multi_insert)|
|btree|100,000|100,000|100,000|100,000|100,000|
|sequence (/32)|3125|3125|3125|3125|3125|
|commit|100,000|1|100|1|1|
|remaining|494639|457|(-)5580|479|681|

- there are 4 lookup tables and 5 FKs
- taxi_zones_lookup alone consists of 265 rows
- the remaining rows might consist of FK-row heap lock records
- method 1 residual is 495k - those are FK lookups performed per row.

### Questions
1. But this model fails for method 3 where the remaining records is in negative ? Why ? How to do it the right way ? Even the xact commit should be 100 but instead it is 89-93 ?

2. Why is COPY faster than Multi-row insert ?
- Multi-row insert took 3.87s and COPY took 1.12s 
- Multi-row insert batches 3000 rows in a single transaction and COPY uses `heap_multi_insert` which flushes in 1000-tuple batches, so shouldn't method 4 be faster than method 5 ?
- COPY bypasses the per-row parse/analyze/plan/execute pipeline entirely
- This isn't about 1 statment vs 34 statements
- The total execution time and fsync time are also almost similar too(method 4 = 1.14s;0.009s and method 5 = 1.11s;0.009s)

