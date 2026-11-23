# Populate DB and Compare - Observations

## Estimates + Expectations 

Tuple Data Section : 

| Category | Columns | Bytes |
|----------|---------|:-----:|
| `bigint` | `trip_id` | 8B |
| `timestamp` | `tpep_pickup_datetime`, `tpep_dropoff_datetime` | 2 x 8B = 16 |
| `smallint` | `vendor_id`, `passenger_count`, `ratecode_id`, `payment_type` | 4 x 2B = 8 |
| `char(1)` | `store_and_fwd_flag` | 2 |
| `integer` | `pu_location_id`, `do_location_id` | 2 x 4B = 8 |
| `real` | 10 amount/distance columns | 10 x 4B = 40 |
| **Total** | | **82** |

Approximations: 

```
Rows = 100_000

Total size of a page = 8 KB = 8192 bytes
PageHeaderData (fixed) = 24 bytes
ItemData Line Pointer Array = 4 bytes x N rows

null bit map for each column = 20 bits = 3 bytes
HeapTupleHeaderData = 23 bytes
HeapTupleHeaderData + null bit map = 26 = 32 MAX ALIGNED
Tuple Data Secion = 82 bytes 
Total = 114 bytes
MAX-ALIGNED = 120 bytes

24 + 120 x N + 4 x N = 8192
124 x N = 8168

Rows per page = N ~ 65

24 + 124 x 65 = 8084
Remaining space = 8192 - 8084 = 108 bytes

-----------------

Rows = 100_000
Rows per page ~ 65
Number of pages = 100_000/65 ≈ 1539
Relation size ~ 12 MB
```
--- 
## Selecting Representative Round for Comparison

Median round will be selected if: 
1. Median ≈ Mean => median is close to mean (unskewed)
2. Max/Min ≈ 1 => spread is less

What should have been equal across all rounds per method (i.e., median=mean=min=max every round per method) : 
- wal_records
- wal_fpi
- fsyncs
- xact_commits

### Method 1 : Row-by-row (autocommit ON)

| metric | ~ median ÷ mean | ~ max ÷ min |
|--------|--------------------|-----------|
| time_sec |  0.99 | 1.01 |
| rows_per_sec (rate) | 1 | 1.01 |
| wal_bytes | 1 | 1 |
| wal_records | 1 | 1 |
| wal_fpi | 1.03 | 1.28
| fsyncs | 1 | 1 | 

Mismatches: 
(max - min) = range
- xact_commits
    - Round 1 = 100010
    - Rounds 2, 3, 4, 5 = 100004 
    - range = 6
- wal_records (mean != median != min != max)
    - range = 27
- wal_fpi (mean != median != min != max)
    - range = 5
- fsyncs (mean != median != min != max)
    - range = 2

### Method 2. Row-by-row (autocommit OFF)

| metric | ~ median ÷ mean | ~ max ÷ min |
|--------|--------------------|-----------|
| time_sec |  1.01 | 1.08 |
| rows_per_sec (rate) | 1 | 1.08 |
| wal_bytes | 1 | 1 |

wal_records, wal_fpi, fsyncs = (mean == median == min == max)

### Method 3. Batched commits (1000/batch)

| metric | ~ median ÷ mean | ~ max ÷ min |
|--------|--------------------|-----------|
| time_sec |  1.01 | 1.05 |
| rows_per_sec (rate) | 0.99 | 1.05 |
| wal_bytes | 0.99 | 1.05 |
| wal_records | 0.99 | 1.05 |
| fsyncs | 0.99 | 1.06 |

wal_fpi = (mean == median == max == min)

Mismatches: 
- xact_commits [89, 90, 91, 93]
    - range = 4
- wal_records, range = 8694
- fsyncs, range = 5

### Method 4. Multi-row INSERT (3000/batch)

| metric | ~ median ÷ mean | ~ max ÷ min |
|--------|--------------------|-----------|
| time_sec |  1 | 1.05 |
| rows_per_sec (rate) | 1 | 1.05 |
| wal_bytes | 1 | 1 |

wal_records, wal_fpi, fsyncs = (mean == median == min == max)

### Method 5. COPY

| metric | ~ median ÷ mean | ~ max ÷ min |
|--------|--------------------|-----------|
| time_sec |  1 | 1.04 |
| rows_per_sec (rate) | 0.99 | 1.04 |
| wal_bytes | 1 | 1 |

---

## Comparison of Methods (Representative Round = Median = 3rd Round)

Common across all methods : 

|||
|---------------|-----|
| relation_size |12 MB|
| rows |100000|
| shared_blks_read |1|
| ckpt_req |0|

---

| Metric | Row-by-Row <br>autocommit ON | Row-by-Row <br>autocommit OFF | Batched Commits | Multi-row INSERT | COPY |
|---|---|---|---|---|---|
| time_sec | 435.978 | 6.66 | 6.768 | 3.866 | 1.12 |
| rows_per_sec<br>100k/time | 229 | 15,015 | 14,775 | 25,866 | 89,274 |
| xact_commits | 100004 | 9 | 93 | 3 | 3 |
| fsyncs | 100003 | 1 | 94 | 2 | 2 |
| fsync_time_sec | 385.216 | 0.008 | 0.493 | 0.009 | 0.009 |
| writes | 100013 | 1 | 96 | 2 | 1593 |
| write_time_sec | 1.763 | 0.0 | 0.008 | 0.0 | 0.005 |
| wal_records | 797764 | 203583 | 197645 | 203605 | 105307 |
| wal_bytes | 49.8 MB | 21 MB | 19.8 MB | 21 MB | 16.7 MB |
| wal_fpi | 22 | 8 | 12 | 12 | 14 |
| total_exec_time_sec | 11.48 | 1.67 | 1.56 | 1.14 | 1.11 |
| stmt_calls | 100000 | 100000 | 100000 | 34 | 1 |
| shared_blks_dirtied | 1832 | 1823 | 1823 | 1823 | 1825 |
| ckpt_timed | 1 | 0 | 0 | 0 | 0 |
| pages | 1539 | 1539 | 1539 | 1539 | 1552 | 

