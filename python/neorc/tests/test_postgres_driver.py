# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Connection strings as asyncpg is handed them.

asyncpg reads URLs; neorc also takes libpq's ``key=value`` strings, and libpq
parameters asyncpg would send to the server as settings it does not have.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import pytest

from neorc.postgres import PostgresCredentialStore, PostgresTaskNotifier
from neorc.postgres._driver import connect_arguments


def test_a_url_is_handed_over_as_it_is() -> None:
    url = "postgresql://neorc:secret@db.example:5433/neorc?sslmode=require"

    assert connect_arguments(url) == {"dsn": url}


def test_libpq_link_parameters_become_a_timeout_or_are_dropped() -> None:
    url = (
        "postgresql://db.example/neorc?connect_timeout=7&keepalives=1"
        "&keepalives_idle=10&tcp_user_timeout=30000&application_name=neorc"
    )

    assert connect_arguments(url) == {
        "dsn": "postgresql://db.example/neorc?application_name=neorc",
        "timeout": 7.0,
    }


def test_a_key_value_string_becomes_a_url() -> None:
    arguments = connect_arguments(
        "host=/var/run/postgresql port=5433 dbname='neo rc' user=neorc"
        r" password='it\'s secret' options='-c search_path=elsewhere'"
        " connect_timeout=3"
    )

    assert arguments["timeout"] == 3.0
    parts = urlsplit(arguments["dsn"])
    assert parts.scheme == "postgresql" and parts.netloc == ""
    query = dict(pair.split("=", 1) for pair in parts.query.split("&"))
    assert {key: unquote(value) for key, value in query.items()} == {
        "host": "/var/run/postgresql",
        "port": "5433",
        "dbname": "neo rc",
        "user": "neorc",
        "password": "it's secret",
        "options": "-c search_path=elsewhere",
    }


def test_an_empty_string_leaves_it_to_the_environment() -> None:
    assert connect_arguments("") == {}


@pytest.mark.parametrize("text", ["host", "host=x dbname", "password='open"])
def test_a_malformed_key_value_string_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="invalid connection string"):
        connect_arguments(text)


def _as_key_value(url: str) -> str:
    parts = urlsplit(url)
    pairs = {
        "host": parts.hostname,
        "port": parts.port,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "dbname": parts.path.lstrip("/"),
    }
    return " ".join(
        f"{key}='{value}'" for key, value in pairs.items() if value not in (None, "")
    )


@pytest.mark.postgres
async def test_the_stores_open_on_a_key_value_string(pg_schema: str) -> None:
    if not pg_schema.startswith("postgresql://") or "?" in pg_schema:
        pytest.skip("needs a plain postgresql:// URL to rewrite")
    conninfo = _as_key_value(pg_schema)

    async with PostgresCredentialStore(conninfo) as credentials:
        assert await credentials.tokens() == []
    async with PostgresTaskNotifier(conninfo) as notifier:
        await notifier.notify()
