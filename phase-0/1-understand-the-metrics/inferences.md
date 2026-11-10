# Understand The Metrics - Inferences 

All the metrics which were expected ([Estimates & Expections](../1-understand-the-metrics.md#estimates--expectations)) to be constant across all rounds matched : 

| | |
|------|-----|
| relation_size | 16 kB |
| pages | 2 |
| rows | 100 |
| writes | 100 | 
| fsyncs | 100 |
| stmt_calls | 100 |

As for : 

| | |
|------|-----|
| xact_commits | 110 |
| write_time_sec | 0.001 | 
| total_exec_time_sec | 0.01 |
| shared_blks_read | 1 |
| ckpt_timed | 0 | 
| ckpt_req | 0 |

- `xact_commits = 110` out of which 100 are obviously 100 insert commits.
- PostgreSQL treats every SQL statement as a transaction if not mentioned explicitly - that includes SELECT statements as well.
- `Remaining 10 xact_commits` are in the [code](../common.py) where 10 SELECT statements are executed while capturing the snapshot.
- Both the time taken to execute 100 inserts and write them to OS cache took the same time across all the rounds .
- Neither WAL-forced nor timed checkpoints were triggered during execution and committing.
- `shared_blks_read` is 1 for all rounds 
```sql
SELECT relname, heap_blks_read, idx_blks_read FROM pg_statio_user_tables
WHERE heap_blks_read > 0 OR idx_blks_read > 0;
-- trips | heap_blks_read=0 | idx_blks_read=1 
SELECT relname, indexrelname, idx_blks_read FROM pg_statio_all_indexes where relname='trips';
-- trips | trips_pkey | 1 
```
- Every btree index has a fixed first page (block 0) that acts as a header. It stores:
    - `btm_magic` — a magic number that identifies the page as a valid btree metapage
    - `btm_root` — which block number is the current root
    - `btm_level` — the level of the root
- When a table is truncated, all it's indexes are trunacated as well. The index file goes to 0 bytes, and the cached metapage data in `rd_amcache` is reset to NULL. The buffer for block 0 is evicted from `shared_buffers`.
- On the first insert after TRUNCATE, the btree code needs to know where the root is and if the index is empty. Since the `rd_amcache` is set to NULL, it must read block 0 from disk (or rather, extend the relation with a zero-filled page and read it). That's the 1 `idx_blks_read`.
- Why 1 and not 100 ?
    - After that first read, `rd_amcache` is populated with the metapage contents.

## Round 1: 

Round 1 was run right after dropping all tables if they already exist and indexes and then adding back all table but no indexes to the database. 

- It took `384 ms` to finish at the rate of `260 rows per sec`.
- The total spent fsyncing the inserts is `342 ms` - around 89% of the total time is spent in fsyncing.
- The simple query protocol sends one SQL string - server does parse + plan + execute in one shot. The extended protocol splits this into discrete messages so the client can pipeline, reuse plans, and batch prepared statements. 

```
Total time = Total Exectution Time + Total Write Time + Total Fsync Time + Server/Client side overhead
```
- `15` blocks are dirited while inserting 100 rows.
```sql
SELECT calls, shared_blks_dirtied, shared_blks_written
FROM pg_stat_statements
WHERE shared_blks_dirtied > 0 AND
query ILIKE 'INSERT INTO trips%'
ORDER BY shared_blks_dirtied DESC;
-- 100 | 15 | 6
```
- FSM is not used for page allocation when the table has =< 4 pages - the insert path uses a local in-memory map instead. The file exists but is effectively dormant.
```console
rmgr: XLOG        len (rec/tot):     49/  8241, tx:     740341, lsn: 0/498FF980, prev 0/498FF958, desc: FPI_FOR_HINT , blkref #0: rel 1663/16388/25339 fork fsm blk 2 FPW
``` 

### WAL
```
Approx number of segments = wal_bytes / 16 MB = 102095 / 16 MB = 0.097 MB < 1 
```
- So the records didn't fill up even a single record - we can say that only filled about 0.6% of a single segment.
- All 811 records fit into a single segment.

## Round 2 and 3: 

Round 2 and 3 were run after truncating the existing `trips` table. All the other lookup tables are present as is from Round 1's recreation of dropped tables. 

- `12` blocks are dirited while inserting 100 rows:
```sql
SELECT calls, shared_blks_dirtied, shared_blks_written
FROM pg_stat_statements
WHERE shared_blks_dirtied > 0 AND
query ILIKE 'INSERT INTO trips%'
ORDER BY shared_blks_dirtied DESC;
-- 100 | 12 | 6
```
- What does this mean or indicate ???


### [WAL Records (Resource Manager/Record Type)](./notes.md#1-wal-records-resource-managerrecord-type)

- There are a total of 4 lookup tables to which foreign key references are made by the child table "trips".
- The FKs are: 
    - vendor_id (vendor_lookup)
    - ratecode_id (ratecode_lookup)
    - payment_type (payment_type_lookup)
    - pu_location_id (taxi_zones_lookup)
    - do_location_id (taxi_zones_lookup)

- For every insert, an FK lookup is performed for each parent table. For this `FOR KEY SHARE` locks are aquired for the relevant tuple in the parent tables. 
- Since there are 5 FKs, the total number of `Heap/LOCK` records should 100x5=500 but we got 495 instead.
- In the `pg_waldump`, there were 100 `Heap/LOCK` records for FKs in vendor_lookup (25261), ratecode_lookup (25272) and payment_type_lookup (25282) tables each. And 195 records for taxi_zone_lookup (25317) table.
- Upon checking: 
```sql
select * from trips where do_location_id=pu_location_id;
```
There were 5 tuples where the criteria was met. That's why 495.

- For 100 inserts, 1 `Btree/NEWROOT`and 100 `Btree/INSERT_LEAF`, `Transaction/COMMIT` records each are present.
- Since the relation has 2 pages, 2 `Heap/INSERT+INIT` records are present along with 98 `Heap/INSERT` records.
- `TRUNCATE` was performed with `RESTART IDENTITY` so the sequence for `trips_pk = trip_id` is reset every run. Though we have 100 inserts only 4 `Sequence/LOG` are present.
```sql
SELECT * FROM pg_sequences WHERE sequencename ILIKE 'trips%';
```
| sequencename | start_value | increment_by | cache_size | last_value |
|--------------|-------------|---|---|---|
| trips_trip_id_seq | 1 | 1 | 1 | 100 |

- This is expected behavior due to PostgreSQL's `sequence WAL pre-logging mechanism`. It does not write a WAL record for every `nextval()` call - it writes one every 32 calls (the hardcoded constant `SEQ_LOG_VALS` = 32), logging a value 32 ahead of the current one.

## Round 1 vs 2 vs 3

- The `total time` taken + `rate` (rows per sec) as well as the `fsync time` reduces from Round 1 to Round 3
    - Total Time: 
        - Round 1 - 2 = 31 ms ~ 8% drop
        - Round 2 - 3 = 4 ms ~ 1% drop
    - Rate:
        - Round 2 - 1 = 23 rows/sec ~ 9% increase 
        - Round 3 - 2 = 3 rows/sec ~ 1%
    - Fsync Time: 
        - Round 1 - 2 = 28 ms ~ 8% drop
        - Round 2 - 3 = 6 ms ~ 2% drop

- `shared_blks_dirtied` decreased from 15 to 12. The common 12 blocks are: 

```sql
select pg_size_pretty(pg_relation_size('vendor_lookup')); -- 8192 bytes
select pg_size_pretty(pg_relation_size('ratecode_lookup')); -- 8192 bytes
select pg_size_pretty(pg_relation_size('payment_type_lookup')); -- 8192 bytes
select pg_size_pretty(pg_relation_size('taxi_zones_lookup')); -- 24 kB

select pg_size_pretty(pg_relation_size('trips_pkey')); -- 16 kB
select pg_size_pretty(pg_relation_size('trips_trip_id_seq')); -- 8192 bytes
select pg_relation_size('trips', 'fsm')/8192; -- 3 pages
```

```sql 
SELECT relname, b.relfilenode, relblocknumber, relforknumber
FROM pg_buffercache b
JOIN pg_class c ON b.relfilenode = c.relfilenode
WHERE isdirty = true
ORDER BY relname, relblocknumber;
-- relforknumber: 0=main, 1=fsm, 2=vm
```
| SI.no | relname | relfilenode | relblocknumber | relforknumber |
|-------|---------|-------------|----------------|---------------|
| 1 | payment_type_lookup | 25282 | 0 | 0 
| 2 | ratecode_lookup | 25272 | 0 | 0 |
| 3 | taxi_zones_lookup | 25317 | 0 | 0 |
| 4 | taxi_zones_lookup | 25317 | 1 | 0 |
| 5 | taxi_zones_lookup | 25317 | 2 | 0 |
| 6 | trips | 25339 | 0 | 0 |
| 7 | trips | 25339 | 1 | 0 |
| 8 | trips | 25339 | 2 | 1 |
| 9 | trips_pkey | 25340 | 0 | 0 |
| 10 | trips_pkey | 25340 | 1 | 0 |
| 11 | trips_trip_id_seq | 25341 | 0 | 0 |
| 12 | vendor_lookup | 25261 | 0 | 0 |

- Based on WAL breakdown total wal bytes is  :
    - Round 1 : 102095 bytes
    - Round 2 : 85956 bytes
    - Round 3 : 86016 bytes
- Round 1 has 3 more `XLOG/FPI_FOR_HINT` records compared to Rounds 2 and 3. These are accounted for : 
| relname | relfilenode | 
|---------|-------------|
| pg_attrdef | 2604 | 
| pg_index | 2610 | 
| pg_trigger | 2620 | 

## Questions: 
1. Why is Round 1: `wal_fpi = 11`, Round 2 & 3: `wal_fpi = 8` and there were `13 XLOG/FPI_FOR_HINT records` and `10 XLOG/FP_FOR_HINT` records respectively ? The delta between Round 1 and 2 & 3 are the same but the values aren't ? Where is the discrepancy coming from ?