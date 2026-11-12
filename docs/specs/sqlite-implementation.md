# SQLite implementation

A second persistence layer, shipped in `neorc` as `neorc.sqlite`, with no
extra to install: it uses the standard library's `sqlite3`. It is for a
manager embedded in an application on one machine, where
[Postgres](postgres-implementation.md) is for a deployment. Nothing here is
visible from `neorc-core`: it is one more adapter behind the store and
notifier ports, and it passes the same contract suite.

## Scope

- `SqliteStore`: flows, runs, tasks and events in one file. What it holds
  survives the process: an application that stops and starts again finds its
  runs where they were.
- `SqliteTaskNotifier`: wakes waiters when another process wrote to the file.

Not here yet: a credential store, so a manager on SQLite serves no API tokens
and no sign-in; and the commands, which still start a manager on Postgres
alone. An application builds the `Manager` itself:

```python
from neorc.sqlite import SqliteStore
from neorc_core import Manager
from neorc_core.local import MemoryTaskNotifier

async with SqliteStore("neorc.db") as store:
    manager = Manager(store, tasks=MemoryTaskNotifier(), events=MemoryTaskNotifier())
```

## One write lock

SQLite lets one connection write at a time, across threads and processes
alike. Every store operation is one transaction on the store's one
connection, and an operation that writes begins with `BEGIN IMMEDIATE`: it
holds the file's write lock before its first read, so what it read is still
true when it writes.

That is the memory store's one lock, held by the file. None of the Postgres
store's lock order is needed: no advisory locks, no locked root rows, no
`SKIP LOCKED`. An event's sequence, and a run's and a task's position, are
row ids allocated under the write lock, so they commit in order and a reader
after a sequence misses none.

The journal is a write-ahead log, so readers never wait for a writer. A
writer waits for another process's write for up to `busy_seconds`, 30 by
default.

SQLite's calls block, so the connection lives on one thread of its own and
the event loop is never held. An operation the caller stops waiting for still
runs to its end on that thread: a `receive_task` cancelled at that moment
leaves its task leased until the lease lapses, as a worker lost between its
request and the reply does.

## The tables

They follow the Postgres ones, with SQLite's types:

- ids are `text`, a UUID in its usual form;
- times are `integer`, microseconds since the epoch in UTC, set from the
  process's clock. Every process on the file is on the same machine, so there
  is one clock;
- values are `text` holding compact JSON, as in Postgres and for the same
  reason: numbers read back as they were written.

`SqliteStore.open` creates them if they are not there, in one transaction.
There is no migration: changing the shape of an existing table is out of
scope until there is a released version to migrate from.

## Waking waiters

A manager that is alone on its file, with its scheduler and workers in the
same process, uses `MemoryTaskNotifier`: nobody else writes.

When workers run in other processes, each with a manager and a store of its
own on the same file, a commit in one must wake the waiters of another.
SQLite has no `LISTEN`/`NOTIFY`. It has `PRAGMA data_version`, a number that
changes on a connection whenever another connection commits, in this process
or another, and takes microseconds to read. `SqliteTaskNotifier` reads
it every `poll_seconds`, 50 ms by default and only while somebody waits, and
wakes every waiter when it moved.

It cannot tell a published task from any other commit, so a waiter is woken
more often than it has something to receive. A wakeup is a hint, as
everywhere: the waiter reads the store and waits again. A file it finds
locked whole, as by a backup, counts as changed.

## Leases and restarts

Leases work as in every store: a task received and never finished is ready
again once its lease lapsed. That is also all a restart needs. A task that
was in a worker's hands when the application closed is received again, by
whichever worker asks after the lease's end, and its handler runs again:
delivery is at-least-once here too.

A scheduler keeps its place in the event log in memory, so one that starts
reads the log from its beginning. The log is never trimmed, and neither are
finished runs: a file grows for as long as it is used.

## Requirements

SQLite 3.35 or later, for `RETURNING`; `SqliteStore.open` says so on an older
one. The file must be on a local disk: a write-ahead log does not work across
a network file system.
