# Table of Contents: 

1. [Metric List](./1-understand-the-metrics/metrics.md)
2. [Estimates + Expectations](#estimates--expectations)
3. [Code](./1-understand-the-metrics.py)
4. [Observations](./1-understand-the-metrics/observations.md)
5. [Notes](./1-understand-the-metrics/notes.md) 
6. [Inferences](./1-understand-the-metrics/inferences.md)
7. [Conclusions](./1-understand-the-metrics/conclusions.md)

--- 

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

HeapTupleHeaderData = 24 bytes (23 max aligned)
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


