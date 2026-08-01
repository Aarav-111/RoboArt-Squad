"""Postgres backend for the family database -- Supabase in practice.

`family_core` was written against sqlite3 and is staying that way: the
offline replica on the judging laptop must keep working with nothing but
the standard library, because `pip install` is not always possible there.

So rather than port 40-odd call sites, this module makes a Postgres
connection *look* like the sqlite3 one they already expect:

    conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

returns a mapping, `?` placeholders work, and the handful of SQLite-only
constructs in family_core are rewritten on the way through. The online
half imports psycopg; the offline half never does.

Session mode, not transaction mode
──────────────────────────────────
Point this at Supabase's *session* pooler (port 5432). family_server holds
one connection per request thread and issues its own BEGIN/COMMIT, which
transaction mode (6543) would break by handing the COMMIT to a different
backend than the BEGIN.
"""

import re
import threading
import time

try:
    import psycopg
    from psycopg.rows import dict_row
    IntegrityError = psycopg.IntegrityError
    OperationalError = psycopg.OperationalError
    available = True
except ImportError:                                  # offline half, no driver
    psycopg = None
    dict_row = None

    class IntegrityError(Exception):
        pass

    class OperationalError(Exception):
        pass

    available = False


def is_pg_url(path):
    """True for the connection strings Supabase hands out."""
    return isinstance(path, str) and path.startswith(
        ("postgres://", "postgresql://"))


# ── SQL translation ───────────────────────────────────────────────────────
# Everything here is driven by what family_core actually emits. It is not a
# general SQLite-to-Postgres translator and should not grow into one; if a
# new query needs a rule that is not below, write the query portably instead.

_TABLE_INFO_RE = re.compile(r"^\s*PRAGMA\s+table_info\((\w+)\)\s*;?\s*$",
                            re.IGNORECASE)
_INSERT_IGNORE_RE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s",
                               re.IGNORECASE)


def _placeholders(sql):
    """`?` -> `%s`, leaving anything inside single-quoted literals alone.

    psycopg reads `%` as its own placeholder marker, so a literal one would
    have to be doubled. family_core emits none today (there is no LIKE in
    it) -- the escape below is here so that adding one later fails loudly at
    the query rather than silently at the driver.
    """
    out = []
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
            out.append(ch)
        elif in_string:
            out.append("%%" if ch == "%" else ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


def translate(sql):
    """Rewrite one family_core statement into its Postgres equivalent."""
    m = _TABLE_INFO_RE.match(sql)
    if m:
        # `PRAGMA table_info(x)` is used only to learn a table's column
        # names, and only the `name` key of each row is read.
        return ("SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='%s'" % m.group(1))

    stripped = sql.strip().upper()
    if stripped.startswith("BEGIN IMMEDIATE"):
        # SQLite takes the write lock up front; Postgres has no equivalent
        # and does not need one -- the row locks it takes as the transaction
        # runs cover the same ground.
        return "BEGIN"
    if stripped.startswith("PRAGMA"):
        return None                       # journal_mode, foreign_keys: no-ops

    ignore = bool(_INSERT_IGNORE_RE.match(sql))
    if ignore:
        sql = _INSERT_IGNORE_RE.sub("INSERT INTO ", sql, count=1)

    sql = _placeholders(sql)

    if ignore:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


def translate_script(script):
    """Split a multi-statement schema script and translate each statement."""
    out = []
    for stmt in script.split(";"):
        if not stmt.strip():
            continue
        translated = translate(stmt)
        if translated:
            out.append(translated)
    return out


# ── connecting ────────────────────────────────────────────────────────────

# Supabase pauses a free-tier project after a week of inactivity, and the
# first connection after that is refused while the instance wakes up --
# which takes a few seconds, not minutes. Retrying turns "the app crashed
# at the judging table" into a pause nobody comments on. The delays are
# short and few because a genuinely wrong password must still fail fast
# enough to be recognised as a wrong password.
CONNECT_ATTEMPTS = 4
CONNECT_BACKOFF = (1, 3, 6)          # seconds between attempts


def _connect_with_retry(url):
    last = None
    for attempt in range(CONNECT_ATTEMPTS):
        try:
            return psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                   connect_timeout=20)
        except psycopg.OperationalError as e:
            # Authentication and permission failures are settled facts; only
            # reachability is worth waiting on.
            if "password" in str(e).lower() or "authenticat" in str(e).lower():
                raise
            last = e
            if attempt < len(CONNECT_BACKOFF):
                time.sleep(CONNECT_BACKOFF[attempt])
    raise OperationalError(
        "Could not reach the online family database after %d attempts. "
        "If the Supabase project has been idle for a week it may be paused "
        "-- open the dashboard to resume it. Last error: %s"
        % (CONNECT_ATTEMPTS, last))


# ── connection wrapper ────────────────────────────────────────────────────

class PGConnection:
    """The sqlite3.Connection surface that family_core relies on.

    Only the methods family_core and family_server actually call are here:
    execute, executescript, close, and the cursor's fetchone/fetchall/
    iteration. Rows come back as plain dicts, which `dict(row)` and
    `row["col"]` both handle, so call sites do not change.

    Not thread-safe, exactly like the sqlite3 connections it replaces --
    family_server gives each request thread its own, and FamilyClient
    serialises through one lock. The internal lock here guards against the
    interleaving that would otherwise corrupt the driver's protocol state,
    not against logical races.
    """

    def __init__(self, url):
        if not available:
            raise RuntimeError(
                "Postgres support needs psycopg. Install it with:\n"
                '    pip install "psycopg[binary]"')
        self._conn = _connect_with_retry(url)
        self._lock = threading.RLock()

    def execute(self, sql, params=()):
        translated = translate(sql)
        if translated is None:
            return _EmptyCursor()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(translated, tuple(params))
            return _Cursor(cur)

    def executescript(self, script):
        with self._lock:
            for stmt in translate_script(script):
                with self._conn.cursor() as cur:
                    cur.execute(stmt)

    def commit(self):
        pass          # autocommit; explicit COMMIT goes through execute()

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass


class _Cursor:
    """Wraps a psycopg cursor so a statement that returned nothing can still
    be asked for rows -- sqlite3 answers None/[] there, psycopg raises."""

    def __init__(self, cur):
        self._cur = cur

    def _has_rows(self):
        return self._cur.description is not None

    def fetchone(self):
        return self._cur.fetchone() if self._has_rows() else None

    def fetchall(self):
        return self._cur.fetchall() if self._has_rows() else []

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return None


class _EmptyCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __iter__(self):
        return iter(())

    rowcount = 0
    lastrowid = None


def connect(url):
    return PGConnection(url)
