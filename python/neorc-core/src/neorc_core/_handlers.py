# Copyright 2026 The neorc Authors
# SPDX-License-Identifier: Apache-2.0

"""Finding the Python function a task's handler names, and checking it fits.

A handler is ``module:function``, and only a function defined in a module
under the worker's code location is one. Whoever uploads a flow chooses its
handlers and their fixed params, so a handler that could name any importable
function, ``subprocess:run`` or ``os:system``, would let them run anything on
every worker. See docs/specs/workers-and-manager.md, "Handlers".

The module is imported one dotted part at a time, and each part is found as
the import system would find it before it is imported: a part found outside
the code location, or already imported from outside it, is refused before any
of its code runs. Since a package's submodules are found through the package,
no module elsewhere is ever imported. A module counts as under the code
location only at the path its name gives from there, ``a/b.py`` for ``a.b``:
a virtual environment inside the code location is not the worker's code, nor
is a package pip installed into it, found by the ``RECORD`` beside it. The
function must also have been defined in such a module, not imported into one
from elsewhere.

A handler that starts with ``executables/`` names an executable instead, an
executable file in the code location's ``executables`` directory: see
``_executables``.
"""

from __future__ import annotations

import csv
import importlib
import inspect
import os
import sys
from collections.abc import Callable, Collection
from importlib.machinery import ModuleSpec
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from typing import Any

from neorc_core._executables import is_executable, resolve_executable


def resolve_handler(target: object, code_location: Path) -> Callable[..., Any]:
    """The function ``module:function`` names under ``code_location``.

    Raises ``ValueError`` saying why there is none: the target is not of that
    form, the module is not under ``code_location`` or does not import, or the
    function is not there or was defined outside ``code_location``.
    ``code_location`` goes to the front of the import path.

    A target that starts with ``executables/`` names an executable: the function
    returned runs it, and ``ValueError`` says why there is no such executable.
    """
    if is_executable(target):
        return resolve_executable(target, code_location)
    if not isinstance(target, str) or ":" not in target:
        raise ValueError(f'wants "module:function", not {target!r}')
    module_name, _, attribute = target.partition(":")
    if not all(part.isidentifier() for part in module_name.split(".")):
        raise ValueError(f"{module_name!r} is not a module name")
    location = code_location.resolve()
    if not sys.path or sys.path[0] != str(location):
        if str(location) in sys.path:
            sys.path.remove(str(location))
        sys.path.insert(0, str(location))
    _ensure_not_installed(module_name, location)
    parts = module_name.split(".")
    module = _import_under(parts[0], location)
    for depth in range(2, len(parts) + 1):
        module = _import_under(".".join(parts[:depth]), location)
    function: object = getattr(module, attribute, None)
    if not callable(function):
        raise ValueError(f"{module_name!r} has no function {attribute!r}")
    defined_in = sys.modules.get(getattr(function, "__module__", None) or "")
    if defined_in is None or not _is_under(defined_in, location):
        raise ValueError(
            f"{target} is not defined in the code location {location}: a handler "
            "must be a function of the worker's own code"
        )
    _ensure_not_installed(defined_in.__name__, location)
    return function


def _ensure_not_installed(name: str, location: Path) -> None:
    """Raise ``ValueError`` if module ``name`` is part of a package pip installed.

    ``pip install --target`` lays a dependency out inside the code location,
    where its path alone would pass for the worker's code; the ``*.dist-info``
    with a ``RECORD`` it leaves beside it says otherwise. Each directory on
    the module's path is looked at, for a target below the code location. The
    ``*.egg-info`` a setuptools build leaves in a project is not an install,
    and names the project's own packages: it is not looked at.
    """
    parts = name.split(".")
    for depth, part in enumerate(parts):
        directory = location.joinpath(*parts[:depth])
        if part in _installed_in(directory):
            raise ValueError(
                f"{'.'.join(parts[: depth + 1])!r} is a package installed into the "
                f"code location {location}, not the worker's own code"
            )


def _installed_in(directory: Path) -> set[str]:
    """The top-level names pip's ``RECORD`` files in ``directory`` list."""
    names: set[str] = set()
    try:
        records = [
            metadata / "RECORD"
            for metadata in directory.glob("*.dist-info")
            if (metadata / "RECORD").is_file()
        ]
        for record in records:
            with record.open(newline="", encoding="utf-8") as lines:
                for row in csv.reader(lines):
                    first = Path(row[0]).parts[0] if row and row[0] else ""
                    if first and first != ".." and not first.endswith(".dist-info"):
                        names.add(first.partition(".")[0])
    except (OSError, UnicodeDecodeError, csv.Error):
        pass  # what cannot be read claims nothing
    return names


def _import_under(name: str, location: Path) -> ModuleType:
    """Import ``name``, its parent imported already, only if under ``location``.

    Raises ``ValueError`` before running any of its code when the import
    system would find it elsewhere, or has already imported it from elsewhere.
    """
    module = sys.modules.get(name)
    if module is not None:
        if not _is_under(module, location):
            raise ValueError(
                f"{name!r} imports from outside the code location {location}"
            )
        # Through the import system, which waits for a module another thread
        # is still importing rather than hand it over half-built.
        return importlib.import_module(name)
    try:
        spec = find_spec(name)  # the parent is imported: nothing runs
    except Exception as exc:
        raise ValueError(f"cannot import {name!r}: {exc}") from exc
    if spec is None or not _spec_is_under(spec, location):
        raise ValueError(f"no module {name!r} in the code location {location}")
    try:
        return importlib.import_module(name)
    except Exception as exc:  # a syntax error or a failing module body, too
        raise ValueError(f"cannot import {name!r}: {exc}") from exc


def _spec_is_under(spec: ModuleSpec, location: Path) -> bool:
    """Whether a module found as ``spec`` would be loaded from ``location`` alone."""
    if spec.origin is not None and spec.has_location:
        return _is_path_of(spec.name, spec.origin, location)
    # A namespace package: every portion of it, wherever the path has one.
    portions = list(spec.submodule_search_locations or ())
    return (
        spec.origin is None
        and bool(portions)
        and all(_is_path_of(spec.name, portion, location) for portion in portions)
    )


def _is_under(module: ModuleType, location: Path) -> bool:
    """Whether ``module`` was loaded from ``location``, as ``_is_path_of`` says.

    A namespace package, which has no file, is if every portion of it is.
    """
    name = getattr(module, "__name__", None)
    if not isinstance(name, str):
        return False
    origin = getattr(module, "__file__", None)
    if isinstance(origin, str):
        return _is_path_of(name, origin, location)
    portions = list(getattr(module, "__path__", None) or ())
    return bool(portions) and all(_is_path_of(name, p, location) for p in portions)


def _is_path_of(name: str, path: str, location: Path) -> bool:
    """Whether ``path`` is where module ``name`` lies with ``location`` as its root.

    ``a.b`` is ``a/b.py`` (or another suffix), ``a/b/__init__.py``, or for a
    namespace portion the directory ``a/b``. A path merely somewhere under
    ``location`` is not enough: a virtual environment there, or any other
    directory on the import path, holds modules that are not the worker's.

    The name is read from the path as the import system found it, links
    followed only to check that what it loads is under ``location`` too: a
    link that stays inside, as a mounted ConfigMap has, is followed.
    """
    found_at = Path(os.path.abspath(path))
    if not (
        found_at.is_relative_to(location)
        and Path(path).resolve().is_relative_to(location)
    ):
        return False
    relative = found_at.relative_to(location)
    if found_at.is_dir():
        found = relative.parts
    elif relative.name.partition(".")[0] == "__init__":
        found = relative.parent.parts
    else:
        found = (*relative.parent.parts, relative.name.partition(".")[0])
    return found == tuple(name.split("."))


def signature_problems(
    function: Callable[..., Any], names: Collection[str]
) -> list[str]:
    """How a handler's parameters disagree with the inputs a task declares.

    Every declared input must be a parameter the handler accepts, by name or
    through ``**kwargs``, and every parameter without a default must be declared.
    """
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return []  # no signature to read: trust the call
    by_name = {
        p.name: p
        for p in parameters
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    }
    takes_any = any(p.kind is p.VAR_KEYWORD for p in parameters)
    problems = [
        f"takes no parameter {name!r}"
        for name in sorted(names)
        if name not in by_name and not takes_any
    ]
    problems.extend(
        f"parameter {name!r} is not an input of the task"
        for name, parameter in by_name.items()
        if name not in names and parameter.default is parameter.empty
    )
    problems.extend(
        f"parameter {p.name!r} is positional-only and cannot be passed by name"
        for p in parameters
        if p.kind is p.POSITIONAL_ONLY and p.default is p.empty
    )
    return problems
