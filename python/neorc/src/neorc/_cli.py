# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc run examples/hello --flow a
    neorc manager start --config oneberth.toml
    neorc manager start --local --database-file neorc.db
    neorc tokens create worker-1 --role neorc/worker --resource default
    neorc flows upload examples/hello/flows --manager-address 127.0.0.1:8420
    neorc scheduler start --manager-address 127.0.0.1:8420
    neorc worker start --manager-address 127.0.0.1:8420 --code-location examples/hello

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.

The commands that reach a manager send the token in ``NEORC_API_TOKEN``,
never taken from the command line, which every user of the host can read. The
token is oneberth's: a service token from ``neorc tokens create``, or a
person's own token from the UI.

``store``, ``tokens``, ``sessions`` and ``devices`` are oneberth's admin
scripts, shown here with neorc's roles (``neorc._admin``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from neorc._admin import add_database_options
from neorc_core import (
    AccessError,
    FlowDefinitionError,
    HandlerError,
    InvalidValueError,
    ManagerUnavailableError,
    NeorcError,
    QueueKind,
    RunStatus,
    Scheduler,
    Worker,
    _values,
)
from neorc_core.flows import DEFAULT_QUEUE, is_queue_name, read_flows, read_queues
from neorc_core.local import run_local

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"
API_TOKEN_ENV = "NEORC_API_TOKEN"
ALLOW_INSECURE_HTTP_ENV = "NEORC_ALLOW_INSECURE_HTTP"
DATABASE_URL_ENV = "NEORC_DATABASE_URL"
CONFIG_ENV = "ONEBERTH_CONFIG"

QUEUE_KINDS = tuple(kind.value for kind in QueueKind)

DEFAULT_HOST = "0.0.0.0"  # a manager serves workers on other hosts
DEFAULT_PORT = 8420

if TYPE_CHECKING:
    from oneberth.config import Config

_log = logging.getLogger("neorc")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``neorc`` argument parser and its subcommands."""
    parser = argparse.ArgumentParser(prog="neorc", description="Orchestrate tasks.")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("debug", "info", "warning", "error"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    manager = commands.add_parser("manager", help="run the manager service")
    manager_commands = manager.add_subparsers(dest="subcommand", required=True)
    manager_start = manager_commands.add_parser("start", help="serve the manager")
    manager_start.add_argument(
        "--host",
        default=None,
        help=f"{DEFAULT_HOST} by default; 127.0.0.1 with --local, which takes a "
        "loopback address alone",
    )
    manager_start.add_argument("--port", type=int, default=DEFAULT_PORT)
    database = manager_start.add_mutually_exclusive_group()
    database.add_argument(
        "--database-url",
        default=None,
        help="a PostgreSQL database; defaults to $NEORC_DATABASE_URL",
    )
    database.add_argument(
        "--database-file",
        type=Path,
        default=None,
        help="a SQLite file instead, created if it is not there; oneberth.db, "
        "the store of sign-ins and tokens, is created beside it. One manager "
        "process serves a file: another one's long polls would not be woken",
    )
    manager_start.add_argument(
        "--create-schema",
        action="store_true",
        help="create the tables at startup if they are not there yet, oneberth's "
        "included",
    )
    manager_start.add_argument(
        "--no-ui",
        action="store_true",
        help="serve the API alone, without the web UI at /ui/",
    )
    signing_in = manager_start.add_mutually_exclusive_group()
    signing_in.add_argument(
        "--config",
        type=Path,
        default=None,
        help="the deployment's sign-in configuration, oneberth's TOML file, with "
        f"the manager's public URL under [apps.neorc]; defaults to ${CONFIG_ENV}",
    )
    signing_in.add_argument(
        "--local",
        action="store_true",
        help="oneberth's local mode, for one person at this machine: no sign-in "
        "configuration; it prints the address that signs that person in",
    )
    manager_start.set_defaults(handler=manager_start_command)

    from oneberth.admin import add_admin_commands

    from neorc._admin import NeorcConnector

    add_admin_commands(commands, NeorcConnector())

    queues = commands.add_parser(
        "queues", help="declare what kind the queues are, on the manager's database"
    )
    queues_commands = queues.add_subparsers(dest="subcommand", required=True)
    queues_declare = queues_commands.add_parser(
        "declare",
        help="declare a queue and its kind, before a flow names it",
    )
    queues_declare.add_argument("name", help="letters, digits, _ and -")
    queues_declare.add_argument(
        "--kind",
        required=True,
        choices=QUEUE_KINDS,
        help="worker: workers receive its tasks; external: its tasks wait for a "
        "responder to complete them",
    )
    add_database_options(queues_declare)
    queues_declare.set_defaults(handler=queues_declare_command)
    queues_list = queues_commands.add_parser("list", help="list the declared queues")
    add_database_options(queues_list)
    queues_list.set_defaults(handler=queues_list_command)

    flows = commands.add_parser("flows", help="manage the flows a manager holds")
    flows_commands = flows.add_subparsers(dest="subcommand", required=True)
    flows_upload = flows_commands.add_parser(
        "upload", help="upload a directory of flow files as one set, as CI/CD would"
    )
    flows_upload.add_argument(
        "directory", type=Path, help="the flow files, *.yaml and *.yml"
    )
    _manager_address_option(flows_upload)
    flows_upload.set_defaults(handler=flows_upload_command)

    scheduler = commands.add_parser("scheduler", help="run the scheduler")
    scheduler_commands = scheduler.add_subparsers(dest="subcommand", required=True)
    scheduler_start = scheduler_commands.add_parser(
        "start", help="move runs forward as their events arrive"
    )
    _manager_address_option(scheduler_start)
    scheduler_start.add_argument("--poll-timeout", type=float, default=30.0)
    scheduler_start.set_defaults(handler=scheduler_start_command)

    worker = commands.add_parser("worker", help="run a worker")
    worker_commands = worker.add_subparsers(dest="subcommand", required=True)
    worker_start = worker_commands.add_parser(
        "start", help="receive, claim and run tasks"
    )
    _manager_address_option(worker_start)
    worker_start.add_argument(
        "--code-location",
        type=Path,
        required=True,
        help="the handlers' code, imported from there first",
    )
    worker_start.add_argument(
        "--queue", default=DEFAULT_QUEUE, help="the queue to serve"
    )
    worker_start.add_argument("--poll-timeout", type=float, default=30.0)
    worker_start.add_argument("--lease-seconds", type=float, default=60.0)
    worker_start.set_defaults(handler=worker_start_command)

    run = commands.add_parser(
        "run", help="run a flow to its end in this process, with nothing to deploy"
    )
    run.add_argument(
        "directory",
        type=Path,
        help="the handlers' code, with the flow files in its flows/ directory",
    )
    run.add_argument("--flow", required=True, help="the flow to run")
    run.add_argument(
        "--inputs",
        default="{}",
        help='the run\'s inputs, a JSON object; datetimes as {"$datetime": "..."}',
    )
    run.add_argument(
        "--flows-dir",
        type=Path,
        default=None,
        help="where the flow files are; defaults to DIRECTORY/flows",
    )
    run.add_argument(
        "--timeout", type=float, default=None, help="give up after this many seconds"
    )
    run.set_defaults(handler=run_command)

    return parser


def _manager_address_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--manager-address",
        default=None,
        help=f"defaults to ${MANAGER_ADDRESS_ENV}",
    )


def _client_options() -> dict[str, object]:
    """The token, and whether plain http may carry it, from the environment."""
    options: dict[str, object] = {}
    token = os.environ.get(API_TOKEN_ENV)
    if token:
        options["token"] = token
    if os.environ.get(ALLOW_INSECURE_HTTP_ENV) == "1":
        options["allow_insecure"] = True
    return options


def _refused(exc: AccessError) -> str:
    where = (
        f"the token in ${API_TOKEN_ENV}"
        if os.environ.get(API_TOKEN_ENV)
        else f"a request with no token (set ${API_TOKEN_ENV})"
    )
    return f"the manager refused {where}: {exc}"


def _manager_address(args: argparse.Namespace, what: str) -> str:
    address: str | None = args.manager_address or os.environ.get(MANAGER_ADDRESS_ENV)
    if not address:
        raise SystemExit(
            f"no manager to {what}: pass --manager-address or set "
            f"${MANAGER_ADDRESS_ENV}"
        )
    return address


def run_command(args: argparse.Namespace) -> int:
    """Run a flow on ``LocalCluster`` and print its output as JSON.

    Exits 0 when the run succeeds, and 1, saying why, when it fails, is
    cancelled, cannot start or times out; 130 on Ctrl-C.
    """
    try:
        _values.ensure_json_depth(args.inputs)
        inputs = json.loads(args.inputs)
    except ValueError as exc:
        raise SystemExit(f"--inputs is not valid JSON: {exc}") from exc
    if not isinstance(inputs, dict):
        raise SystemExit("--inputs must be a JSON object of input names to values")
    flows_dir: Path = args.flows_dir or args.directory / "flows"
    if not flows_dir.is_dir():
        raise SystemExit(f"no flows directory at {str(flows_dir)!r}")

    # A loop and thread pool of our own: however the run ends, a plain handler
    # still running in a thread must not hold the command, as asyncio.run would.
    threads = f"{_HANDLER_THREADS}-{uuid.uuid4().hex[:8]}"  # this run's only
    executor = ThreadPoolExecutor(thread_name_prefix=threads)
    loop = asyncio.new_event_loop()
    loop.set_default_executor(executor)
    try:
        run = loop.run_until_complete(
            run_local(
                flows_dir,
                args.flow,
                inputs,
                code_location=args.directory,
                timeout=args.timeout,
            )
        )
    except TimeoutError:  # before OSError, which it is a kind of
        print(
            f"{args.flow!r} did not finish within {args.timeout} seconds",
            file=sys.stderr,
        )
        return _finish(loop, executor, threads, 1)
    except (NeorcError, ValueError, OSError) as exc:
        print(f"cannot run {args.flow!r}: {exc}", file=sys.stderr)
        return _finish(loop, executor, threads, 1)
    except KeyboardInterrupt:
        return _finish(loop, executor, threads, 130)
    except Exception as exc:  # a crashed loop: still never wait on a handler
        _log.exception("running %r crashed", args.flow)
        print(f"cannot run {args.flow!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return _finish(loop, executor, threads, 1)

    if run.status is RunStatus.SUCCEEDED:
        print(json.dumps(run.output))
        return _finish(loop, executor, threads, 0)
    print(f"run {run.id} {run.status.value}: {run.reason}", file=sys.stderr)
    return _finish(loop, executor, threads, 1)


_HANDLER_THREADS = "neorc-handler"


def _finish(
    loop: asyncio.AbstractEventLoop,
    executor: ThreadPoolExecutor,
    threads: str,
    exit_code: int,
) -> int:
    """Close the loop without waiting for handlers still running in a thread.

    Python joins those threads on its way out, so if one is still busy the
    process exits here and now instead, with ``exit_code``.
    """
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
    executor.shutdown(wait=False, cancel_futures=True)
    busy = False
    for thread in threading.enumerate():
        if thread.name.startswith(threads):
            thread.join(timeout=0.1)  # idle threads leave as the pool shuts down
            busy = busy or thread.is_alive()
    if busy:
        sys.stdout.flush()
        sys.stderr.flush()
        _exit_now(exit_code)
    return exit_code


def _exit_now(exit_code: int) -> None:
    os._exit(exit_code)


def manager_start_command(args: argparse.Namespace) -> int:
    """Compose standalone neorc and serve it. Needs the ``manager`` extra."""
    database_errors: tuple[type[BaseException], ...]
    if args.database_file is not None:
        import sqlite3

        with _needs("manager"):
            from neorc.manager import build_app, run
        database_errors = (sqlite3.Error,)
    else:
        with _needs("manager", "postgres"):
            from neorc.manager import build_app, run
            from neorc.postgres._driver import CONNECTION_ERRORS
        database_errors = CONNECTION_ERRORS

    local = args.local
    host = args.host or (LOCAL_HOST if local else DEFAULT_HOST)
    config = _local_config(host, args.port) if local else _config(args)
    try:
        if args.database_file is not None:
            # Checked again when the backend starts; here, so the command can
            # say so and exit rather than leave it to the server's startup.
            from neorc.sqlite import check_database

            check_database(args.database_file)
        app = build_app(
            args.database_url,
            config=config,
            create_schema=args.create_schema,
            database_file=args.database_file,
            ui=not args.no_ui,
            local=local,
        )
    except FileNotFoundError as exc:  # no built UI to serve: say how to get one
        raise SystemExit(f"neorc manager start: {exc}, or pass --no-ui") from None
    except (RuntimeError, ValueError) as exc:  # no database, a wrong file or config
        raise SystemExit(f"neorc manager start: {exc}") from None
    except database_errors as exc:
        raise SystemExit(
            f"neorc manager start: cannot reach the database: {exc}"
        ) from None
    run(app, host, args.port)
    return 0


LOCAL_HOST = "127.0.0.1"
"""Where the local mode listens by default: its one person is at this machine."""


def _local_config(host: str, port: int) -> Config:
    """The configuration of oneberth's local mode: the manager on a loopback address."""
    with _needs("manager"):
        from oneberth.config import ConfigError, local_config
    try:
        return local_config("neorc", port=port, host=host)
    except ConfigError as exc:
        raise SystemExit(
            "neorc manager start: --local listens on a loopback address alone: "
            + "; ".join(exc.problems)
        ) from None


def _config(args: argparse.Namespace) -> Config:
    """The sign-in configuration named, read and checked."""
    with _needs("manager"):
        from oneberth.config import ConfigError, load_config
    path: Path | None = args.config
    if path is None and not os.environ.get(CONFIG_ENV):
        raise SystemExit(
            "neorc manager start: no sign-in configuration: pass --config or set "
            f"${CONFIG_ENV} to oneberth's TOML file, which names the manager's "
            "public URL under [apps.neorc]; or pass --local, for one person at "
            "this machine"
        )
    try:
        config = load_config(path)
    except ConfigError as exc:
        raise SystemExit(
            "neorc manager start: the sign-in configuration cannot be used:\n"
            + "\n".join(f"  {problem}" for problem in exc.problems)
        ) from None
    if "neorc" not in config.apps:
        raise SystemExit(
            "neorc manager start: the sign-in configuration names no public URL "
            "for the manager: add [apps.neorc] with public_url"
        )
    if not config.apps["neorc"].secure:
        _log.warning(
            "the public URL %s is http: browsers share its cookies with every other "
            "server on this machine, so sign in here only where those are trusted",
            config.apps["neorc"].public_url,
        )
    return config


def queues_declare_command(args: argparse.Namespace) -> int:
    """Declare a queue's kind; exits 1, saying why, when it is refused."""
    from neorc_core import QueueDeclarations

    async def declare(declarations: QueueDeclarations) -> int:
        try:
            await declarations.declare(args.name, QueueKind(args.kind))
        except InvalidValueError as exc:
            raise SystemExit(f"cannot declare the queue: {exc}") from None
        print(f"declared queue {args.name!r} as {args.kind}")
        return 0

    return _on_queues(args, declare)


def queues_list_command(args: argparse.Namespace) -> int:
    """One line per declared queue: name, kind."""
    from neorc_core import QueueDeclarations

    async def listing(declarations: QueueDeclarations) -> int:
        for queue in await declarations.queues():
            print(f"{queue.name}\t{queue.kind.value}")
        return 0

    return _on_queues(args, listing)


def _on_queues(args: argparse.Namespace, act: Callable[..., Awaitable[int]]) -> int:
    """Run ``act`` on the queue declarations of the manager's database.

    The tokens bound to a queue are oneberth's, in its store beside the
    manager's records: the schema ``oneberth`` of the PostgreSQL database, or
    ``oneberth.db`` beside the SQLite file. These commands create no tables,
    so a mistyped database is not given a set of empty ones.
    """
    import sqlite3

    from oneberth.admin import ConnectError, StoreAdmin
    from oneberth.identity import IdentityError

    from neorc._admin import AdminTokenGrants, opened_stores
    from neorc._roles import registry
    from neorc.sqlite import SchemaVersionError
    from neorc_core import QueueDeclarations

    database_errors: tuple[type[BaseException], ...] = (sqlite3.Error,)
    if args.database_file is None:
        with _needs("postgres"):
            from neorc.postgres._driver import CONNECTION_ERRORS
            from neorc.postgres._schema import QUEUES_TABLE, ensure_tables
        database_errors = CONNECTION_ERRORS

    async def main() -> int:
        if args.database_file is None:
            dsn = args.database_url or os.environ.get(DATABASE_URL_ENV)
            if dsn:
                await ensure_tables(dsn, (QUEUES_TABLE,))
        async with opened_stores(args) as (identity, store):
            grants = AdminTokenGrants(StoreAdmin(identity, registry()))
            return await act(QueueDeclarations(store, grants))

    try:
        return asyncio.run(main())
    except (ConnectError, IdentityError, SchemaVersionError) as exc:
        raise SystemExit(str(exc)) from None
    except RuntimeError as exc:  # the tables are missing: the message says how
        raise SystemExit(str(exc)) from None
    except database_errors as exc:
        raise SystemExit(f"cannot reach the database: {exc}") from None


def flows_upload_command(args: argparse.Namespace) -> int:
    """Declare the queues of a directory's ``queues.toml``, then upload its flow
    files as one set, and say what was declared and stored.

    The files are read and validated as ``neorc run`` reads them; the manager
    checks the set and the version rules. A queue declared already keeps its
    kind. Exits 1 when the manager refuses.
    """
    with _needs("http"):
        from neorc.http import HttpManagerClient

    address = _manager_address(args, "upload to")
    if not args.directory.is_dir():
        raise SystemExit(f"no such directory: {str(args.directory)!r}")
    try:
        queues = read_queues(args.directory)
        contents = read_flows(args.directory)
    except FlowDefinitionError as exc:
        raise SystemExit(
            "invalid flow files:\n" + "\n".join(f"  {p}" for p in exc.problems)
        ) from exc
    except OSError as exc:
        raise SystemExit(f"cannot read {str(args.directory)!r}: {exc}") from exc
    if not contents:
        raise SystemExit(f"no flow files in {str(args.directory)!r}")

    async def upload() -> tuple[list[bool], list[bool]]:
        async with _client(HttpManagerClient, address) as client:
            declared = [
                await client.declare_queue(name, kind) for name, kind in queues.items()
            ]
            return declared, await client.upload_flows(contents)

    try:
        declared, stored = asyncio.run(upload())
    except AccessError as exc:
        print(_refused(exc), file=sys.stderr)
        return 1
    except FlowDefinitionError as exc:
        print("upload refused:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except NeorcError as exc:
        print(f"upload refused: {exc}", file=sys.stderr)
        return 1
    for (name, kind), is_new in zip(queues.items(), declared, strict=True):
        state = "declared" if is_new else "unchanged"
        print(f"{state} {name} {kind.value}")
    for content, is_new in zip(contents, stored, strict=True):
        state = "stored" if is_new else "unchanged"
        print(f"{state} {content['name']} {content['version']}")
    return 0


def scheduler_start_command(args: argparse.Namespace) -> int:
    """Run the scheduler against the manager named by ``NEORC_MANAGER_ADDRESS``."""
    with _needs("http"):
        from neorc.http import HttpManagerClient

    address = _manager_address(args, "schedule for")

    async def serve() -> None:
        async with _client(
            HttpManagerClient, address, poll_timeout=args.poll_timeout
        ) as client:
            scheduler = Scheduler(client, poll_timeout=args.poll_timeout)
            _stop_on_signals(scheduler.stop)
            await scheduler.run()

    try:
        asyncio.run(serve())
    except AccessError as exc:
        raise SystemExit(_refused(exc)) from None
    return 0


def worker_start_command(args: argparse.Namespace) -> int:
    """Run a worker against the manager named by ``NEORC_MANAGER_ADDRESS``.

    It serves one queue of the flows the manager holds, with the handlers found
    at ``--code-location``.
    """
    return _serve_queue(args, _manager_address(args, "work for"))


def _serve_queue(args: argparse.Namespace, address: str) -> int:
    """A worker: check its handlers against the queue's tasks, then run."""
    with _needs("http"):
        from neorc.http import HttpQueueClient

    if not args.code_location.is_dir():
        raise SystemExit(f"no such directory: {str(args.code_location)!r}")
    if not is_queue_name(args.queue):
        raise SystemExit(
            f"{args.queue!r} is not a queue name: letters, digits, _ and -"
        )

    async def serve() -> int:
        async with _client(
            HttpQueueClient, address, poll_timeout=args.poll_timeout
        ) as client:
            worker = Worker(
                client,
                queue=args.queue,
                code_location=args.code_location,
                poll_timeout=args.poll_timeout,
                lease_seconds=args.lease_seconds,
            )
            stopping = asyncio.Event()

            def stop() -> None:
                stopping.set()
                worker.stop()

            _stop_on_signals(stop)
            if not await _prepared(worker, args, stopping):
                return 0
            try:
                await worker.run()
            except AccessError as exc:
                print(_refused(exc), file=sys.stderr)
                sys.stdout.flush()
                sys.stderr.flush()
                # A handler thread may still be running the task, whose lease
                # no longer beats: ending the process ends it, before another
                # worker takes the task and runs it too.
                _exit_now(1)
                return 1
        return 0

    return asyncio.run(serve())


PREPARE_RETRY_SECONDS = 5.0
"""How long a starting worker waits for a manager it cannot reach yet."""


async def _prepared(
    worker: Worker, args: argparse.Namespace, stopping: asyncio.Event
) -> bool:
    """Check the handlers against the queue's tasks, waiting out an unreachable manager.

    Workers outlive manager restarts, and are often started alongside one:
    an unreachable manager is retried until a stop signal. Handlers that do
    not fit their tasks will not fit on retry: that exits, listing every
    problem. Returns ``False`` when stopped before the check passed.
    """
    while not stopping.is_set():
        try:
            await worker.prepare()
            return True
        except AccessError as exc:
            raise SystemExit(_refused(exc)) from exc
        except HandlerError as exc:
            raise SystemExit(
                f"the handlers in {str(args.code_location)!r} do not fit the "
                f"tasks on queue {args.queue!r}:\n"
                + "\n".join(f"  {p}" for p in exc.problems)
            ) from exc
        except ManagerUnavailableError as exc:
            _log.warning(
                "cannot check the handlers yet (%s); retrying in %ss",
                exc,
                PREPARE_RETRY_SECONDS,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=PREPARE_RETRY_SECONDS)
        except NeorcError as exc:  # refused, and would be again: not worth retrying
            raise SystemExit(f"cannot start the worker: {exc}") from exc
    return False


_C = TypeVar("_C", bound=AbstractAsyncContextManager[Any])


@asynccontextmanager
async def _client(
    cls: Callable[..., _C], address: str, **options: object
) -> AsyncIterator[_C]:
    """An open HTTP client for ``address`` with the token from the environment."""
    try:
        client = cls(address, **options, **_client_options())
    except InvalidValueError as exc:  # a token that plain http would carry
        raise SystemExit(str(exc)) from None
    async with client:
        yield client


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``neorc`` console script; returns the exit status."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )
    try:
        exit_code: int = args.handler(args)
    except KeyboardInterrupt:
        return 130
    return exit_code


@contextmanager
def _needs(*extras: str) -> Iterator[None]:
    """Turn a missing optional dependency into the install command that fixes it.

    Nothing is installed by default, so this is the first thing a new deployment
    meets. It should say what to do, not print a traceback.
    """
    try:
        yield
    except ImportError as exc:
        raise SystemExit(
            f"this command needs the {' and '.join(map(repr, extras))} extra, which "
            f"is not installed ({exc}). Install it with: "
            f"pip install 'neorc[{','.join(extras)}]'"
        ) from exc


def _stop_on_signals(stop: Callable[[], None]) -> None:
    """Ask the worker to stop on the signals a supervisor sends to shut it down."""
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop)
        except NotImplementedError:  # a platform without signal handlers
            _log.debug("cannot install a handler for %s", signal_number)
