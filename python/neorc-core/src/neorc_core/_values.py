# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""The values that travel between tasks, and how they are written down.

Flow inputs, task inputs and task results are JSON values plus datetimes. On the
wire and in storage a datetime is an object with a single ``$datetime`` key; user
code only ever sees ``datetime`` objects. See docs/specs/workers-and-manager.md,
"Values".
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any

from neorc_core._errors import InvalidValueError, PayloadTooLargeError

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
"""A value as JSON holds it: datetimes are already tagged."""

MAX_PAYLOAD_BYTES = 1024 * 1024
"""The largest encoded payload, in bytes of UTF-8: the SQS message limit."""

MAX_JSON_DEPTH = 200
"""The deepest nesting of arrays and objects that JSON text may have."""

MAX_VALUE_DEPTH = MAX_JSON_DEPTH // 2
"""The deepest nesting a value may have: half of what JSON text may.

A value travels inside envelopes, a report, a delivery, a run's state, and
gains a list level for every loop or fan-out between the step that produced it
and the one that reads it. The other half is theirs, so a value that is
accepted can always be sent on.
"""

DATETIME_TAG = "$datetime"
RESERVED_PREFIX = "$"

NUL = "\u0000"
"""The one character a string may not hold: Postgres ``text`` cannot store it."""


def encode(
    value: Any, *, path: str = "value", limit: int = MAX_VALUE_DEPTH
) -> JsonValue:
    """Turn a user value into its JSON form, tagging datetimes.

    Raises ``InvalidValueError`` for anything neorc does not carry: sets,
    tuples, dates, naive datetimes, non-finite floats, non-string keys, keys
    starting with ``$``, text no store can hold (see ``check_text``), and
    nesting past ``limit``. ``path`` names the value in the error. The default
    limit is for a value entering the system; a worker writing a task's inputs
    again, for an executable, passes ``MAX_JSON_DEPTH``, as it does to ``decode``.
    """
    return _encode(value, path, 0, limit)


def decode(
    value: JsonValue, *, path: str = "value", limit: int = MAX_VALUE_DEPTH
) -> Any:
    """Turn a JSON form back into a user value, untagging datetimes.

    Raises ``InvalidValueError`` for a malformed tag, any other ``$`` key,
    text no store can hold, a number JSON text cannot carry back, and nesting
    past ``limit``. ``path`` names the value in the error. The default limit is
    for a value entering the system; a worker decoding inputs already accepted,
    and collected into lists by loops and fan-outs, passes ``MAX_JSON_DEPTH``.
    """
    return _decode(value, path, 0, limit)


def check_text(text: str, path: str) -> None:
    """Raise ``InvalidValueError`` if ``text`` cannot be stored or sent.

    NUL has no place in these values, and Postgres ``text`` and ``jsonb``
    cannot hold it; a lone surrogate cannot be encoded as UTF-8. Every store
    refuses both, so a value the in-memory store takes is one the deployed
    store takes too.
    """
    if NUL in text:
        raise InvalidValueError(f"{path}: text holds NUL (U+0000)")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidValueError(f"{path}: text is not valid UTF-8: {exc}") from None


def dumps(value: Any) -> str:
    """Encode a user value and serialise it to compact JSON."""
    return json.dumps(encode(value), separators=(",", ":"), ensure_ascii=False)


def dumps_json(value: JsonValue) -> str:
    """Serialise a value already in its JSON form, compactly, as it travels.

    This is the encoding the payload limit is counted in.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def loads(text: str) -> Any:
    """Parse JSON and decode it into a user value.

    Raises ``InvalidValueError`` for what JSON text cannot carry back, ``NaN``
    and ``Infinity`` included, and for nesting past the depth limit.
    """
    ensure_json_depth(text)
    return decode(json.loads(text))


def ensure_json_depth(text: str, *, limit: int = MAX_JSON_DEPTH) -> None:
    """Raise ``InvalidValueError`` if JSON text nests deeper than ``limit``.

    Call before ``json.loads`` on untrusted text: on some Pythons its C parser
    exhausts the thread's stack and crashes the process on deep nesting, before
    it can raise ``RecursionError``.
    """
    depth = 0
    for token in _JSON_TOKEN.finditer(text):
        bracket = token.group()
        if bracket in "[{":
            depth += 1
            if depth > limit:
                raise InvalidValueError(f"JSON nests deeper than {limit} levels")
        elif bracket in "]}":
            depth -= 1


def ensure_fits(encoded: str, *, limit: int = MAX_PAYLOAD_BYTES) -> None:
    """Raise ``PayloadTooLargeError`` if ``encoded`` is over ``limit`` bytes."""
    try:
        size = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise InvalidValueError(f"payload is not valid UTF-8: {exc}") from None
    if size > limit:
        raise PayloadTooLargeError(f"payload is {size} bytes, over the {limit} limit")


_JSON_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"?|[\[\]{}]')
"""A string, whose brackets do not nest, or a bracket.

The closing quote is optional so an unterminated string ends the scan in one
pass rather than being retried from every later quote, which is quadratic.
"""


def _encode(value: Any, path: str, depth: int, limit: int) -> JsonValue:
    # bool before int: a bool is an int in Python.
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        check_text(value, path)
        return value
    if isinstance(value, int):
        _check_int(value, path)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidValueError(f"{path}: {value} is not a JSON number")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidValueError(f"{path}: datetime {value} has no timezone")
        return {DATETIME_TAG: value.isoformat()}
    if isinstance(value, list):
        _check_depth(depth, path, limit)
        return [
            _encode(item, f"{path}[{i}]", depth + 1, limit)
            for i, item in enumerate(value)
        ]
    if isinstance(value, dict):
        _check_depth(depth, path, limit)
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            _check_key(key, path)
            encoded[key] = _encode(item, f"{path}.{key}", depth + 1, limit)
        return encoded
    raise InvalidValueError(f"{path}: {type(value).__name__} is not a supported type")


def _decode(value: JsonValue, path: str, depth: int, limit: int) -> Any:
    if isinstance(value, list):
        _check_depth(depth, path, limit)
        return [
            _decode(item, f"{path}[{i}]", depth + 1, limit)
            for i, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if DATETIME_TAG in value and len(value) == 1:
            return _parse_datetime(value[DATETIME_TAG], path)
        _check_depth(depth, path, limit)
        decoded: dict[str, Any] = {}
        for key, item in value.items():
            _check_key(key, path)
            decoded[key] = _decode(item, f"{path}.{key}", depth + 1, limit)
        return decoded
    if isinstance(value, str):
        check_text(value, path)
    elif isinstance(value, float) and not math.isfinite(value):
        # json.loads takes NaN and Infinity; JSON text cannot carry them back.
        raise InvalidValueError(f"{path}: {value} is not a JSON number")
    return value


def _check_depth(depth: int, path: str, limit: int) -> None:
    """``depth`` containers enclose the one about to be entered at ``path``."""
    if depth >= limit:
        raise InvalidValueError(f"{path}: nests deeper than {limit} levels")


def _check_int(value: int, path: str) -> None:
    # Python refuses to write an integer past its digit limit, as a ValueError
    # from json.dumps; refuse it here, as a value, before anything writes it.
    try:
        str(value)
    except ValueError as exc:
        raise InvalidValueError(f"{path}: integer cannot be written: {exc}") from None


def _check_key(key: object, path: str) -> None:
    if not isinstance(key, str):
        raise InvalidValueError(f"{path}: key {key!r} is not a string")
    if key.startswith(RESERVED_PREFIX):
        raise InvalidValueError(f"{path}: key {key!r} is reserved for neorc")
    check_text(key, f"{path}: key {key!r}")


def _parse_datetime(text: JsonValue, path: str) -> datetime:
    if not isinstance(text, str):
        raise InvalidValueError(f"{path}: {DATETIME_TAG} must hold an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise InvalidValueError(
            f"{path}: {text!r} is not an ISO 8601 datetime"
        ) from None
    if parsed.tzinfo is None:
        raise InvalidValueError(f"{path}: datetime {text!r} has no timezone")
    return parsed
