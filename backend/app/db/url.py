"""Database URL normalisation.

Managed Postgres providers (Neon, Supabase, RDS, Azure Flexible Server) hand
out libpq-style URLs — ``postgresql://…?sslmode=require&channel_binding=require``.
Two things go wrong if that string is used verbatim:

* **asyncpg** does not understand ``sslmode``/``channel_binding``. They arrive
  as unexpected keyword arguments and connection fails at startup.
* **Alembic** runs migrations through a *synchronous* driver, which does
  understand ``sslmode`` and needs it kept.

So one input URL has to become two outputs. Everything here is pure string
manipulation over :func:`urllib.parse` — no settings, no I/O — so it is cheap
to test exhaustively, which matters: a mistake surfaces only in production.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# libpq connection parameters that the async driver cannot accept.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "target_session_attrs"}

# sslmode values that mean "encrypt the connection".
_SSL_REQUIRED_MODES = {"require", "verify-ca", "verify-full"}

_ASYNC_DRIVER = "postgresql+asyncpg"
_SYNC_DRIVER = "postgresql+psycopg2"


def is_sqlite(url: str) -> bool:
    """SQLite is used by the fast unit-test suite and needs no normalisation."""
    return url.startswith("sqlite")


def async_database_url(url: str) -> tuple[str, dict[str, object]]:
    """Return an asyncpg-safe URL plus connect args for :func:`create_async_engine`.

    SSL is passed through ``connect_args`` rather than left in the query string
    because the exact query-parameter spelling asyncpg accepts has varied
    between SQLAlchemy versions, whereas ``connect(ssl=...)`` has been stable.
    """
    if is_sqlite(url):
        return url, {}

    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode = params.pop("sslmode", None)
    for key in _LIBPQ_ONLY_PARAMS:
        params.pop(key, None)

    connect_args: dict[str, object] = {}
    if sslmode is not None and sslmode in _SSL_REQUIRED_MODES:
        connect_args["ssl"] = sslmode
    elif sslmode == "disable":
        connect_args["ssl"] = False

    normalised = urlunsplit(
        (_ASYNC_DRIVER, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )
    return normalised, connect_args


def sync_database_url(url: str) -> str:
    """Return the same database addressed through a synchronous driver.

    Used by Alembic. ``sslmode`` is deliberately preserved — psycopg2 speaks
    libpq and requires it to negotiate TLS against managed providers.
    """
    if is_sqlite(url):
        # sqlite+aiosqlite:///path -> sqlite:///path (the stdlib driver).
        # Swapped textually rather than through urlunsplit, which collapses
        # SQLite's empty-netloc triple slash down to one and yields a URL
        # that points at a different file.
        scheme, separator, rest = url.partition(":")
        return "sqlite" + separator + rest

    parts = urlsplit(url)
    return urlunsplit((_SYNC_DRIVER, parts.netloc, parts.path, parts.query, parts.fragment))
