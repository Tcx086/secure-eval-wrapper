"""PostgreSQL-only configuration loading.

The loader reads only PostgreSQL environment variables and never falls back to any other
storage target.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


REQUIRED_POSTGRES_ENV = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

OPTIONAL_POSTGRES_ENV = ("POSTGRES_SSLMODE",)


class PostgresConfigError(RuntimeError):
    """Raised when PostgreSQL configuration is missing or invalid."""


@dataclass(frozen=True)
class PostgresConfig:
    """Connection settings for PostgreSQL.

    This value object is inert: constructing it does not connect to PostgreSQL.
    """

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "disable"

    def to_connection_kwargs(self) -> dict[str, object]:
        """Return keyword arguments compatible with common PostgreSQL Python drivers."""

        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
        }


def load_postgres_config(environ: Mapping[str, str] | None = None) -> PostgresConfig:
    """Load PostgreSQL settings from environment variables.

    Raises:
        PostgresConfigError: if a required PostgreSQL setting is missing or invalid.
    """

    source = os.environ if environ is None else environ
    values = {name: source.get(name, "").strip() for name in REQUIRED_POSTGRES_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise PostgresConfigError(
            "Missing PostgreSQL environment variables: "
            + ", ".join(missing)
            + ". Configure PostgreSQL explicitly; no fallback storage is supported."
        )

    try:
        port = int(values["POSTGRES_PORT"])
    except ValueError as exc:
        raise PostgresConfigError("POSTGRES_PORT must be an integer.") from exc

    if port <= 0 or port > 65535:
        raise PostgresConfigError("POSTGRES_PORT must be between 1 and 65535.")

    sslmode = source.get("POSTGRES_SSLMODE", "disable").strip() or "disable"

    return PostgresConfig(
        host=values["POSTGRES_HOST"],
        port=port,
        database=values["POSTGRES_DB"],
        user=values["POSTGRES_USER"],
        password=values["POSTGRES_PASSWORD"],
        sslmode=sslmode,
    )
