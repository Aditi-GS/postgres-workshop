# Understand The Metrics - Observations

## Estimates + Expectations 

Tuple Data Section : 

| Category | Columns | Bytes |
|----------|---------|:-----:|
| `bigint` | `trip_id` | 8B |
| `timestamp` | `tpep_pickup_datetime`, `tpep_dropoff_datetime` | 2 x 8B = 16 |
| `smallint` | `vendor_id`, `passenger_count`, `ratecode_id`, `payment_type` | 4 x 2B = 8 |
| `char(1)` | `store_and_fwd_flag` | 1 |
| `integer` | `pu_location_id`, `do_location_id` | 2 x 4B = 8 |
| `real` | 10 amount/distance columns | 10 x 4B = 40 |
| **Total** | | **81** |

Approximations: 

```
Rows = 100

Total size of a page = 8 KB = 8192 bytes
PageHeaderData (fixed) = 24 bytes
ItemData Line Pointer Array = 4 bytes x N rows

HeapTupleHeaderData = 24 bytes (MAX ALIGNED 23 bytes)
Tuple Data Secion = 81 bytes 
Total = 105 bytes
MAX-ALIGNED = 112 bytes

24 + 112 x N + 4 x N = 8192
116 x N = 8168

Rows per page = N ~ 70.4 ~ 70

24 + 116 x 70 = 8144
Remaining space = 8192 - 8144 = 48 bytes

-----------------

Rows = 100
Rows per page ~ 70
Number of pages ~ 2
Relation size ~ 16 kB
```
--- 

Start afresh on a newly created DB. 
Before the rounds are executed, drop all tables/indexes if already present. Then on an empty database run : 

`alembic upgrade head`
& 
`alembic downgrade -1` (to remove indexes)

Don't change the state of the DB after this. 
Execute the rounds as is. 
Inserts performed in the 1st Round will be the first action upon the tables post creation.

---

## Compare Different Non-WAL Metrics : Rounds 1, 2 & 3

|Metric|Round 1|Round 2|Round 3|
|------|-------|-------|-------|
| time_sec | 0.384 | 0.353 | 0.349 |
| rows_per_sec | 260 | 283 | 286 |
| fsync_time_sec | 0.342 | 0.314 | 0.308 |
| shared_blks_dirtied | 15 | 12 | 12 |

## Equal Metrics Across All Rounds

|Metric|Value|
|------|-----|
| relation_size | 16 kB |
| pages | 2 |
| rows | 100 |
| xact_commits | 110 |
| writes | 100 | 
| write_time_sec | 0.001 | 
| total_exec_time_sec | 0.01 |
| fsyncs | 100 |
| stmt_calls | 100 |
| shared_blks_read | 1 |
| ckpt_timed | 0 | 
| ckpt_req | 0 |

## Compare Diff WAL Metrics : Rounds 1, 2 & 3

|Metric|Round 1|Round 2|Round 3|
|------|-------|-------|-------|
| wal_records | 811 | 810 | 810 |
| wal_bytes | 102095 | 85956 | 86016 |
| wal_fpi | 11 | 8 | 8 |

# WAL Breakdown 
## Compare WAL FPI for Rounds 1, 2 & 3

| XLOG/FPI_FOR_HINT | Round 1 | Round 2 | Round 3 |
|---|---------|---------|---------|
| Count | 13 | 10 | 10 |
| Record size <br>(49 bytes header)</br> | 637 | 490 | 490 |
| FPI size | 60320 | 44828 | 45520 |
| Combined size | 60957 | 45318 | 46010 | 


## Equal Records Across All Rounds 

| Record Type | Count | Size (bytes) |
|-------------|-------|------|
| Heap/LOCK | 495 | 26730 |
| Btree/INSERT_LEAF | 100 | 6400 |
| Transaction/COMMIT | 100 | 3400 |
| Heap/INSERT | 98 | 14798 |
| Sequence/LOG | 4 | 396 |
| Heap/INSERT+INIT | 2 | 302 |
| Btree/NEWROOT | 1 | 90 | 

# WAL Full Pages Dump 

## Common to rounds 1, 2 & 3
### pg_waldump -s START_LSN -e END_LSN -p pg_wal

Follows the pattern:

```console
XLOG/FPI_FOR_HINT 1259 (pg_class)
XLOG/FPI_FOR_HINT 2663 (pg_class_oid_index)
XLOG/FPI_FOR_HINT 2662 (pg_class_relname_nsp_index)

Sequence (trips_trip_id_seq)

Heap/INSERT+INIT

Btree/NEWROOT (trips_pkey)
Btree/INSERT_LEAF

XLOG 25261 (vendor lookup)
Heap/LOCK 25261
XLOG 25272 (ratecode_lookup)
Heap/LOCK 25272
XLOG 25282 (payment_type_lookup)
Heap/LOCK 25282
XLOG 25317 (taxi_zones_lookup)
Heap/LOCK 25317 
XLOG 25317 (taxi_zones_lookup)
Heap/LOCK 25317 
Transaction/COMMIT

(x98)
Heap/INSERT
Btree/INSERT_LEAF
Heap/LOCK 25261 (vendor lookup)
Heap/LOCK 25272 (ratecode_lookup)
Heap/LOCK 25282 (payment_type_lookup)
Heap/LOCK 25317 (taxi_zones_lookup)
Heap/LOCK 25317 (taxi_zones_lookup)
Transaction/COMMIT

XLOG 25317 (taxi_zones_lookup)
XLOG fork fsm FPW
```
In between the 98 record sequences, 3 more `Sequence/LOG` and 1 more Heap/INSERT+INIT records were present.

## relfilenode & ::regclass

| relname | relfilenode/regclass |
| 25339 | trips (Round 1) |
| 25342 | trips (Round 2) |
| 25345 | trips (Round 3) | 

```sql 
SELECT relname, relfilenode FROM pg_class WHERE relfilenode IN (2604,2620,2610,25261,25272,25282,25317);
```
| relname | relfilenode | 
|---------|-------------|
| pg_attrdef | 2604 | 
| pg_index | 2610 | 
| pg_trigger | 2620 | 
| vendor_lookup | 25261 | 
| ratecode_lookup | 25272 | 
| payment_type_lookup | 25282 | 
| taxi_zones_lookup | 25317 |

```sql
SELECT 1259::regclass, 2663::regclass, 2662::regclass;
```
|  | regclass |
|--|----------|
| 1259 | pg_class |
| 2662 | pg_class_oid_index |
| 2663 | pg_class_relname_nsp_index |