"""Connection parameter helpers for PostgreSQL.

No connection is opened here. Concrete repository implementations will own driver selection and
connection lifecycle in a later phase.
"""

from __future__ import annotations

from typing import Mapping

from secure_eval_wrapper.storage.postgres.config import PostgresConfig, load_postgres_config


def build_connection_kwargs(
    config: PostgresConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build PostgreSQL driver keyword arguments without opening a connection."""

    resolved_config = config if config is not None else load_postgres_config(environ)
    return resolved_config.to_connection_kwargs()
