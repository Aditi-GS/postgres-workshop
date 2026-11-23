# Extended Query Protocol
The extended protocol exists to separate understanding the query from running it, so you can:
  - Parse once, execute many times (prepared statements)
  - Send parameters in a different format (binary)
  - Pipeline multiple operations before the server responds

- Every message on the wire is: 
  - 1 byte type
  - 4 byte length 
  - payload
- The client `prepares all four outbound messages (P, B, E, S) before any of them are sent`. It already has everything it needs - the SQL text, the parameter values, the intent to execute and commit. It writes them all in a single write() syscall.
- The server prepares all four inbound messages (1, 2, C, Z) sequentially, one per step, as it processes each outbound message.

## Prepare By Client:
All four messages are built back-to-back in the client's loop, then flushed together.

1. Parse (P)
- The client (e.g., psycopg2) has the SQL template string in its source code or a config. It knows the parameter count and types. It serializes: type='P', len, stmt_name="", query_string, n_params, param_type_oid[].

2. Bind (B)
- The application has the row data. It serializes each parameter value as text (or binary if requested): type='B', len, portal_name="", stmt_name="", n_formats, n_params, then for each param: [4-byte length][value bytes].

3. Execute (E)
- No data is passed
- type='E', len=8, portal_name="", max_rows=0. The client is just saying "run the portal you just made."

4. Sync (S)
- Empty message too

## Done by Server: 
Upon recieving all 4 messages: 
- For the P message : 
  - First the server raw parses and converts the string into a raw parse tree (just grammar structure, no semantics)
  - It analyzes and resolves the table names to OIDs, column names to attnos, type-checks expressions and applies implicit casts
  - If any rules present, it applies and rewrites the parse tree
  - The server outputs a Query* prepared statement and returns '1' (ParseComplete) back to the client
- For the B message: 
  - It fetches the Query* prepared statement prepared from Parse
  - The planner prepares a plan for the executor to use with the parameter values binded to the prepared statement
  - A portal is allocated
  - The plan is fixed - new parameter values to the same statement can be re-binded with a new portal but re-uses the same plan
  - Returns '2' (BindComplete)
- For the E message: 
  - It returns 'C' (CommandComplete) post execution
- For the S message: 
  - It returns 'Z' (ReadyForQuery) -> ready for the next statement 
  - It commits the transaction and frees the portal and the prepared statement 

# rd_amcache

`rd_amcache` is a field within the `relcache` used to store access method-specific cached data.  It is primarily designed for `Index Access Methods (AMs)` to cache information like operator classes or cost estimation data across successive operations within a single SQL statement.

Index AMs can allocate memory in and store a pointer in `rd_amcache` for relation-level caching to avoid redundant calculations.

# 1. WAL Records (Resource Manager/Record Type)

## 1. Heap/LOCK
- A Heap/LOCK WAL record is written when a transaction locks a row without modifying its data. 
- Row-level lock information is stored directly in the tuple header on the data page (no memory limit) - because transactions might want to lock large numbers of tuples at any one time, so it's not possible to keep the lock objects in shared memory.
- Since the lock info is stored in the tuple header - the contents of the data page changes - which results in a WAL record being generated.
- The shared-memory lock table is capped by `max_locks_per_transaction`, so it can't hold entries for millions of locked rows. 
- The `xmax` field is repurposed to hold the locking transaction's ID, along with flag bits in `t_infomask` which indicate that the `"xmax is a lock, not a deletion."` Since this modifies bytes on the page, it must be WAL-logged for durability and recovery.
- `t_infomask` bits describe what kind of xmax this is:
    - `HEAP_XMAX_LOCK_ONLY`: it’s a lock, not a delete
    - `HEAP_XMAX_KEYSHR_LOCK`, `HEAP_XMAX_EXCL_LOCK`: lock privilege
    - `HEAP_XMAX_IS_MULTI`: `xmax` is a MultiXact ID
- The record-specific data is stored in the `xl_heap_lock` structure.
```C
typedef struct xl_heap_lock
{
    TransactionId   xmax;       /* the locking transaction's XID */
    OffsetNumber    offnum;     /* row offset within the page */
    uint8           infobits_set; /* which infomask bits were set */
    uint8           flags;
} xl_heap_lock;   
```
The information of the exact tuple to lock is stored in the struct
- The info on relfilenode, fork_num and block_num the targeted tuple lies in is stored in the WAL records' block header. It is retrieved from the temporary per backend registered_buffers[] array. When the page required is loaded into the buffer, the `tag` field in the buffer descriptor stores the RelFileLocator and the BlockNumber - the same `tag`'s info is used to store and assemble which page and block the tuple lies in.
- During crash recovery, the redo function for `XLOG_HEAP_LOCK` takes the page, finds the tuple at `offnum`, and writes the `xmax` and `infobits` into the tuple header. No data columns are touched - only the lock metadata in the header.
- Before v9.3 there were only two lock modes - shared and exclusive lock. This created a deadlock scenario with foreign keys. Hence two new intermediate modes so that "reading a row to verify a FK" and "updating a row's non-key columns" can proceed concurrently.
- The four row lock modes are : 
```
FOR KEY SHARE  <  FOR SHARE  <  FOR NO KEY UPDATE  <  FOR UPDATE
  (weakest)                                           (strongest)   
```
- Since `t_xmax` is a single 32-bit field, it can only hold one ID. When a second transaction wants to lock the same tuple (non conflicting compatible lock modes e.g., one holds FOR SHARE, another wants FOR KEY SHARE) - A new `MultiXactId` is allocated (from a sequence in `pg_multixact`)
- The member list (original XID + new XID, each with its mode flag) is written to `pg_multixact/members`
- `t_xmax` is replaced with the `MultiXactId`, `HEAP_XMAX_IS_MULTI` is set in `t_infomask`
- The individual strength bits in `t_infomask` are cleared (the info is now in the `members`) 
- When the MultiXact is no longer needed (all members committed/aborted), it's truncated.

### FOR KEY SHARE (LockTupleKeyShare): 
- It means that the transaction is reading the row and needs the primary key/unique constraint columns to remain stable. It doesn't care if the non-key columns are changed.
- It protects against: *DELETE* and *UPDATE* operations that modify the key columns.
- It allows concurrently: *FOR NO KEY UPDATE* (updating non-key columns), other *FOR KEY SHARE* holders, *FOR SHARE*.
- It exists for FK check where it only needs to guarantee the parent row won't be deleted or have its key changed while the child insert is in-flight.
- Bit pattern: KEYSHR_LOCK=1, EXCL_LOCK=0, KEYS_UPDATED=0

### FOR SHARE (LockTupleShare): 
- It means that the transaction is reading the row and needs it to remain completely unmodified:
no column change, key or otherwise
- It protects against: *DELETE*, *UPDATE*, *FOR NO KEY UPDATE* and *FOR UPDATE*
- It allows concurrently: other *FOR SHARE* holders and *FOR KEY SHARE* holders
- Bit pattern: KEYSHR_LOCK=1, EXCL_LOCK=1, KEYS_UPDATED=0 (note: HEAP_XMAX_SHR_LOCK is a derived constant = both bits set)

### FOR NO KEY UPDATE (LockTupleNoKeyExclusive)
- It means that the transaction is going to modify the row but NOT the key or unique constraint columns. It needs exclusive access to the rows' non-key data. 
- It protects against: *DELETE*, *FOR UPDATE*, other *FOR NO KEY UPDATE*, *FOR SHARE*
- It allows concurrently: *FOR KEY SHARE* holders
- The executor inspects which columns the UPDATE statement modifies. If any modified column is part of a unique index that can be used as a FK target, it escalates to *FOR UPDATE*. Otherwise it uses *FOR NO KEY UPDATE*.
- This is the lock that makes the FK deadlock go away.
- Bit pattern: KEYSHR_LOCK=0, EXCL_LOCK=1, KEYS_UPDATED=0

### FOR UPDATE (LockTupleExclusive)
- It means that the transaction is going to delete a row or change its key. It needs total exclusive access - nothing else can touch that row. 
- It protects against: All four modes
- It allows concurrently: Nothing (only one holder at a time)
- Deleting a row or changing its key can invalidate FK references from child tables. So it must conflict with even the weakest lock (FOR KEY SHARE), which is the lock a concurrent FK check would hold.
- Bit pattern: KEYSHR_LOCK=0, EXCL_LOCK=1, KEYS_UPDATED=1

| Requested ↓ / Held → | KEY SHARE | SHARE | NO KEY UPDATE | UPDATE |
|---|---|---|---|---|
| **FOR KEY SHARE** | ✓ | ✓ | ✓ | ✗ blocks |
| **FOR SHARE** | ✓ | ✓ | ✗ blocks | ✗ blocks |
| **FOR NO KEY UPDATE** | ✓ | ✗ blocks | ✗ blocks | ✗ blocks |
| **FOR UPDATE** | ✗ blocks | ✗ blocks | ✗ blocks | ✗ blocks |

2. Btree/INSERT_LEAF
-  

3. Transaction/COMMIT
4. Heap/INSERT
5. Sequence/LOG
6. Heap/INSERT+INIT
7. Btree/NEWROOT
8. XLOG/FPI_FOR_HINT
9. CLOG/ZEROPAGE
10. XLOG/CHECKPOINT_REDO
11. Standby/RUNNING_XACTS