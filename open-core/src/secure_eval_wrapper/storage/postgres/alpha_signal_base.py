"""Small DB-API helpers isolated from Phase 2 validation imports."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (UUID, Decimal, datetime)):
        return str(value)
    if hasattr(value, "value"):
        return _jsonable(value.value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _json_param(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


class _PostgresRepositoryBase:
    def __init__(self, connection: Any, *, commit_on_write: bool = True) -> None:
        if connection is None:
            raise TypeError("a DB-API PostgreSQL connection is required")
        self.connection = connection
        self.commit_on_write = commit_on_write

    def _fetchall(self, sql: str, params: Sequence[object] = ()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
            description = getattr(cursor, "description", None)
            if not description:
                return tuple({str(index): value for index, value in enumerate(row)} for row in rows)
            names = [str(item[0]) for item in description]
            return tuple({name: value for name, value in zip(names, row)} for row in rows)
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _fetchone(self, sql: str, params: Sequence[object] = ()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
            if row is None:
                return None
            description = getattr(cursor, "description", None)
            if description:
                return {str(item[0]): value for item, value in zip(description, row)}
            return {str(index): value for index, value in enumerate(row)}
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    @contextmanager
    def transaction(self) -> Iterator["_PostgresRepositoryBase"]:
        previous = self.commit_on_write
        self.commit_on_write = False
        try:
            yield self
        except Exception:
            if hasattr(self.connection, "rollback"):
                self.connection.rollback()
            raise
        else:
            if hasattr(self.connection, "commit"):
                self.connection.commit()
        finally:
            self.commit_on_write = previous


__all__ = ["_PostgresRepositoryBase", "_json_param"]
