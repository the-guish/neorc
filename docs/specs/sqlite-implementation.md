# SQLite implementation

The store for one machine, in `neorc.sqlite`. It keeps a manager's flows, runs,
tasks, events and queues in one file, on the standard library's `sqlite3`.
It needs no extra.

## Shape

- One process opens the file. A manager on SQLite runs inside that process.
- The manager tells waiting workers and schedulers about new work in memory,
  with `MemoryTaskNotifier`. SQLite has no notifications across processes.
- The store passes the store contract, `StoreContract`, as the memory and
  Postgres stores do.
- The store keeps no credentials. oneberth's store keeps them, in the file
  `oneberth.db` beside neorc's. The manager's process opens both files.

## The event loop

- One connection and one thread serve the store.
- Each operation is a plain function that runs as one transaction on that
  thread. The event loop awaits it and stays free.
- The thread runs one operation at a time. The operations are atomic towards
  each other, as under the memory store's one lock.
- A writing operation begins with `BEGIN IMMEDIATE`. It takes the file's write
  lock first and waits for another process's lock without failing half way.

## The file

- The file is created with its tables at the first open.
- The file holds neorc's `application_id` and the schema version in
  `user_version`. The store refuses a file of another version, or of another
  application, before it changes anything in it.
- The journal is the write-ahead log. A crash leaves the last commit whole,
  and the next open recovers it.
- Every commit is synced to disk, `synchronous = FULL`. A task that a worker
  was told is finished stays finished after a power cut.
- A statement waits up to five seconds for a lock another connection holds.
  This covers a command such as `neorc queues declare` that opens the file
  beside a running manager.

## Tables

- The tables mirror the Postgres ones and hold values in the same text forms.
- Ids are UUIDs as text.
- Times are integers: microseconds since the Unix epoch, in UTC.
- Runs, tasks and events are ordered by an `AUTOINCREMENT` key. One connection
  writes, so the order of the keys is the order of the commits.
- The `CHECK` constraints are written from the enums of `neorc_core`.

## Commands

```
neorc manager start --database-file neorc.db --local
neorc queues declare default --kind worker --database-file neorc.db
neorc queues list --database-file neorc.db
neorc tokens create worker-1 --role neorc/worker --resource default \
    --database-file neorc.db
```

`--local` is oneberth's local mode, for one person at the machine. It needs
no sign-in configuration and prints the address that signs that person in.
`--config` names a sign-in configuration instead.

`neorc tokens`, `neorc sessions` and `neorc devices` open `oneberth.db` beside
the file, and `neorc queues` reads the tokens there too.
