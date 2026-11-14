# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The ``neorc`` command.

    neorc run examples/hello --flow a
    neorc manager start
    neorc tokens create worker-1 --role worker --queue default
    neorc flows upload examples/hello/flows --manager-address 127.0.0.1:8420
    neorc scheduler start --manager-address 127.0.0.1:8420
    neorc worker start --manager-address 127.0.0.1:8420 --code-location examples/hello

Adapters are imported inside the handlers, so a host that installed only the
extras it needs can still run the CLI.

The commands that reach a manager send the API token in ``NEORC_API_TOKEN``,
never taken from the command line, which every user of the host can read.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import ssl
import sys
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from neorc_core import (
    AccessError,
    ApiToken,
    FlowDefinitionError,
    HandlerError,
    InvalidValueError,
    ManagerUnavailableError,
    NeorcError,
    QueueKind,
    Role,
    RunStatus,
    Scheduler,
    Worker,
    _values,
)
from neorc_core.flows import DEFAULT_QUEUE, is_queue_name, read_flows
from neorc_core.local import run_local

MANAGER_ADDRESS_ENV = "NEORC_MANAGER_ADDRESS"
API_TOKEN_ENV = "NEORC_API_TOKEN"
ALLOW_INSECURE_HTTP_ENV = "NEORC_ALLOW_INSECURE_HTTP"
DATABASE_URL_ENV = "NEORC_DATABASE_URL"
AUTH_CONFIG_ENV = "NEORC_AUTH_CONFIG"

ROLES = tuple(role.value for role in Role)
QUEUE_KINDS = tuple(kind.value for kind in QueueKind)

DEFAULT_HOST = "0.0.0.0"  # a manager serves workers on other hosts
DEFAULT_PORT = 8420

if TYPE_CHECKING:
    from neorc.auth import AuthConfig

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
    manager_start.add_argument("--host", default=DEFAULT_HOST)
    manager_start.add_argument("--port", type=int, default=DEFAULT_PORT)
    manager_start.add_argument(
        "--database-url",
        default=None,
        help="defaults to $NEORC_DATABASE_URL",
    )
    manager_start.add_argument(
        "--create-schema",
        action="store_true",
        help="create the tables at startup if they are not there yet",
    )
    manager_start.add_argument(
        "--no-ui",
        action="store_true",
        help="serve the API alone, without the web UI at /ui/",
    )
    manager_start.add_argument(
        "--no-auth",
        action="store_true",
        help="serve anyone who reaches the manager, with no API token: "
        "for trying it on a machine nobody else reaches",
    )
    manager_start.add_argument(
        "--auth-config",
        type=Path,
        default=None,
        help="sign-in to the UI: providers and who may sign in; defaults to "
        f"${AUTH_CONFIG_ENV}",
    )
    manager_start.add_argument(
        "--ssl-certfile", default=None, help="serve HTTPS with this certificate"
    )
    manager_start.add_argument(
        "--ssl-keyfile", default=None, help="and this private key"
    )
    manager_start.set_defaults(handler=manager_start_command)

    tokens = commands.add_parser(
        "tokens", help="manage the API tokens a manager accepts, on its database"
    )
    tokens_commands = tokens.add_subparsers(dest="subcommand", required=True)
    tokens_create = tokens_commands.add_parser(
        "create", help="create a token and print its secret, which is shown once"
    )
    tokens_create.add_argument("name", help="letters, digits, _, . and -")
    tokens_create.add_argument(
        "--role",
        required=True,
        choices=ROLES,
        help="what the token is for; each role may do a fixed set of things",
    )
    tokens_create.add_argument(
        "--queue",
        default=None,
        help="the queue a worker or responder token is bound to",
    )
    tokens_create.add_argument(
        "--expires-days",
        type=float,
        default=None,
        help="stop accepting it after this many days; never by default",
    )
    _database_url_option(tokens_create)
    tokens_create.set_defaults(handler=tokens_create_command)
    tokens_list = tokens_commands.add_parser("list", help="list the tokens")
    _database_url_option(tokens_list)
    tokens_list.set_defaults(handler=tokens_list_command)
    tokens_revoke = tokens_commands.add_parser(
        "revoke", help="stop accepting a token, at once"
    )
    tokens_revoke.add_argument("name")
    _database_url_option(tokens_revoke)
    tokens_revoke.set_defaults(handler=tokens_revoke_command)

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
    _database_url_option(queues_declare)
    queues_declare.set_defaults(handler=queues_declare_command)
    queues_list = queues_commands.add_parser("list", help="list the declared queues")
    _database_url_option(queues_list)
    queues_list.set_defaults(handler=queues_list_command)

    sessions = commands.add_parser(
        "sessions", help="manage the sessions of people signed in to the UI"
    )
    sessions_commands = sessions.add_subparsers(dest="subcommand", required=True)
    sessions_clear = sessions_commands.add_parser("clear", help="sign everyone out")
    _database_url_option(sessions_clear)
    sessions_clear.set_defaults(handler=sessions_clear_command)

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


def _database_url_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        default=None,
        help=f"the manager's database; defaults to ${DATABASE_URL_ENV}",
    )


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
        f"the API token in ${API_TOKEN_ENV}"
        if os.environ.get(API_TOKEN_ENV)
        else f"a request with no API token (set ${API_TOKEN_ENV})"
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
    """Run the manager service. Needs the ``manager`` extra."""
    with _needs("manager", "postgres"):
        from neorc.manager import run
        from neorc.postgres._driver import CONNECTION_ERRORS

    if (args.ssl_certfile is None) != (args.ssl_keyfile is None):
        raise SystemExit(
            "neorc manager start: pass both --ssl-certfile and --ssl-keyfile"
        )
    if args.ssl_certfile is not None:
        # Loaded here once, so a missing, unreadable or malformed file is said
        # to be that, not left to fail inside the server's startup.
        try:
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(
                args.ssl_certfile, args.ssl_keyfile
            )
        except (OSError, ssl.SSLError) as exc:
            raise SystemExit(
                "neorc manager start: cannot serve HTTPS with "
                f"{args.ssl_certfile!r} and {args.ssl_keyfile!r}: {exc}"
            ) from None
    auth_config = _auth_config(args)
    if args.no_auth:
        where = f"{args.host}:{args.port}"
        if args.host == DEFAULT_HOST:
            where += ", every interface of this host"
        _log.warning(
            "no authentication: anyone who reaches %s can do everything, and so "
            "can any web page this machine's browser opens, through DNS rebinding",
            where,
        )
    try:
        run(
            args.host,
            args.port,
            auth=not args.no_auth,
            auth_config=auth_config,
            database_url=args.database_url,
            create_schema=args.create_schema,
            ui=not args.no_ui,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
        )
    except FileNotFoundError as exc:  # no built UI to serve: say how to get one
        raise SystemExit(f"neorc manager start: {exc}, or pass --no-ui") from None
    except RuntimeError as exc:  # no database, or no credential tables in it
        raise SystemExit(f"neorc manager start: {exc}") from None
    except CONNECTION_ERRORS as exc:  # checked before serving
        raise SystemExit(
            f"neorc manager start: cannot reach the database: {exc}"
        ) from None
    return 0


def _auth_config(args: argparse.Namespace) -> AuthConfig | None:
    """The sign-in configuration named, read and checked; ``None`` if none is."""
    path: Path | None = args.auth_config
    if path is None and os.environ.get(AUTH_CONFIG_ENV):
        path = Path(os.environ[AUTH_CONFIG_ENV])
    if path is None:
        if not args.no_auth:
            _log.warning(
                "no --auth-config: API tokens only, and nobody can sign in to the UI"
            )
        return None
    if args.no_auth:
        raise SystemExit(
            "neorc manager start: --no-auth asks for no identity, so there is no "
            f"signing in to configure: drop --auth-config or ${AUTH_CONFIG_ENV}"
        )
    with _needs("manager"):
        from neorc.auth import AuthConfigError, load_auth_config
    try:
        config = load_auth_config(path)
    except AuthConfigError as exc:
        raise SystemExit(
            f"neorc manager start: {path} cannot be used:\n"
            + "\n".join(f"  {problem}" for problem in exc.problems)
        ) from None
    except OSError as exc:
        raise SystemExit(f"neorc manager start: cannot read {path}: {exc}") from None
    if not config.secure:
        _log.warning(
            "the public URL %s is http: browsers share its cookies with every other "
            "server on this machine, so sign in here only where those are trusted",
            config.public_url,
        )
    return config


def tokens_create_command(args: argparse.Namespace) -> int:
    """Create a token; its secret alone on stdout, what it is on stderr."""
    from neorc_core import Access

    expires = None if args.expires_days is None else args.expires_days * 86400

    async def create(access: Access) -> int:
        try:
            secret, token = await access.create_token(
                args.name, Role(args.role), queue=args.queue, expires_seconds=expires
            )
        except InvalidValueError as exc:
            raise SystemExit(f"cannot create the token: {exc}") from None
        until = token.expires_at.isoformat() if token.expires_at else "never"
        print(
            f"created {_described(token)} token {token.name!r}, expiring {until}; "
            "this is the only time its secret is shown:",
            file=sys.stderr,
        )
        print(secret)
        return 0

    return _on_credentials(args, create)


def tokens_list_command(args: argparse.Namespace) -> int:
    """One line per token: name, role, created, expiry. Never a secret."""
    from datetime import UTC, datetime

    from neorc_core import Access

    async def listing(access: Access) -> int:
        now = datetime.now(UTC)
        for token in await access.tokens():
            if token.expires_at is None:
                expiry = "never expires"
            elif token.expires_at <= now:
                expiry = f"expired {token.expires_at.isoformat()}"
            else:
                expiry = f"expires {token.expires_at.isoformat()}"
            print(
                f"{token.name}\t{_described(token)}"
                f"\tcreated {token.created_at.isoformat()}\t{expiry}"
            )
        return 0

    return _on_credentials(args, listing)


def _described(token: ApiToken) -> str:
    """A token's role, with the queue a worker or responder token is bound to."""
    if token.queue is None:
        return token.role.value
    return f"{token.role.value} on queue {token.queue!r}"


def tokens_revoke_command(args: argparse.Namespace) -> int:
    """Revoke a token by name; exits 1 if there is none."""
    from neorc_core import Access

    async def revoke(access: Access) -> int:
        if await access.revoke_token(args.name):
            print(f"revoked token {args.name!r}")
            return 0
        print(f"no token called {args.name!r}", file=sys.stderr)
        return 1

    return _on_credentials(args, revoke)


def queues_declare_command(args: argparse.Namespace) -> int:
    """Declare a queue's kind; exits 1, saying why, when it is refused."""
    from neorc_core import Access

    async def declare(access: Access) -> int:
        try:
            await access.declare_queue(args.name, QueueKind(args.kind))
        except InvalidValueError as exc:
            raise SystemExit(f"cannot declare the queue: {exc}") from None
        print(f"declared queue {args.name!r} as {args.kind}")
        return 0

    return _on_credentials(args, declare)


def queues_list_command(args: argparse.Namespace) -> int:
    """One line per declared queue: name, kind."""
    from neorc_core import Access

    async def listing(access: Access) -> int:
        for queue in await access.queues():
            print(f"{queue.name}\t{queue.kind.value}")
        return 0

    return _on_credentials(args, listing)


def sessions_clear_command(args: argparse.Namespace) -> int:
    """End every session: everyone signed in signs in again."""
    from neorc_core import Access

    async def clear(access: Access) -> int:
        count = await access.end_sessions()
        print(f"ended {count} session{'' if count == 1 else 's'}")
        return 0

    return _on_credentials(args, clear)


def _on_credentials(
    args: argparse.Namespace, act: Callable[..., Awaitable[int]]
) -> int:
    """Run ``act`` on an ``Access`` over the manager's database.

    The tables of credentials and of queue declarations must be there already:
    these commands create none, so a mistyped database URL is not given a set
    of empty tables.
    """
    with _needs("postgres"):
        from neorc.postgres import PostgresCredentialStore, PostgresStore
        from neorc.postgres._driver import CONNECTION_ERRORS
        from neorc.postgres._schema import (
            CREDENTIAL_TABLES,
            QUEUES_TABLE,
            MissingTablesError,
            ensure_tables,
        )

    from neorc_core import Access

    dsn: str | None = args.database_url or os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        raise SystemExit(f"no database: pass --database-url or set ${DATABASE_URL_ENV}")

    async def main() -> int:
        await ensure_tables(dsn, (*CREDENTIAL_TABLES, QUEUES_TABLE))
        async with (
            PostgresCredentialStore(dsn, max_size=1) as credentials,
            PostgresStore(dsn, max_size=1) as store,
        ):
            return await act(Access(credentials, queues=store))

    try:
        return asyncio.run(main())
    except MissingTablesError as exc:
        raise SystemExit(str(exc)) from None
    except CONNECTION_ERRORS as exc:
        raise SystemExit(f"cannot reach the database: {exc}") from None


def flows_upload_command(args: argparse.Namespace) -> int:
    """Upload the flow files in a directory as one set, and say what was stored.

    The files are read and validated as ``neorc run`` reads them; the manager
    checks the set and the version rules. Exits 1 when it refuses.
    """
    with _needs("http"):
        from neorc.http import HttpManagerClient

    address = _manager_address(args, "upload to")
    if not args.directory.is_dir():
        raise SystemExit(f"no such directory: {str(args.directory)!r}")
    try:
        contents = read_flows(args.directory)
    except FlowDefinitionError as exc:
        raise SystemExit(
            "invalid flow files:\n" + "\n".join(f"  {p}" for p in exc.problems)
        ) from exc
    except OSError as exc:
        raise SystemExit(f"cannot read {str(args.directory)!r}: {exc}") from exc
    if not contents:
        raise SystemExit(f"no flow files in {str(args.directory)!r}")

    async def upload() -> list[bool]:
        async with _client(HttpManagerClient, address) as client:
            return await client.upload_flows(contents)

    try:
        stored = asyncio.run(upload())
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
