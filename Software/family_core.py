"""Local storage layer for Family accounts.

Everything here is a thin SQLite wrapper — no network, no UI. A single file
(``family_local.db``) next to the app holds every account that has ever
signed in on this machine, the families they belong to, invitations between
them, and anything shared or recorded through Learn Mode. family_client.py
builds the actual behaviour (sign-in, sync, sharing) on top of this; this
module only knows how to read and write rows.

Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-user random salt —
never stored or compared in the clear.
"""
import hashlib
import json
import os
import sqlite3
import time
import uuid

DB_FILENAME = "family_local.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    phone         TEXT,
    email         TEXT,
    avatar        TEXT,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS families (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    join_code   TEXT UNIQUE NOT NULL,
    owner_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS family_members (
    family_id  TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member',
    joined_at  TEXT NOT NULL,
    PRIMARY KEY (family_id, user_id)
);

CREATE TABLE IF NOT EXISTS invitations (
    id           TEXT PRIMARY KEY,
    family_id    TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    to_user_id   TEXT,
    to_contact   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TEXT NOT NULL,
    resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS shares (
    id           TEXT PRIMARY KEY,
    family_id    TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    kind         TEXT NOT NULL,
    name         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    synced       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS progress (
    id           TEXT PRIMARY KEY,
    family_id    TEXT,
    user_id      TEXT NOT NULL,
    design       TEXT,
    complexity   TEXT,
    score        REAL,
    out_of       REAL,
    scored_by    TEXT,
    lesson       TEXT,
    level        INTEGER,
    timestamp    TEXT NOT NULL,
    photo        TEXT,
    improvements_json TEXT,
    synced       INTEGER NOT NULL DEFAULT 0
);

-- ── Competitions ────────────────────────────────────────────────────
-- A competition is a named contest with a join code, exactly like a family,
-- but players are keyed by *contact* rather than by a local user row: the
-- person you are competing against has never signed in on this device, so
-- there is no users row to point at. Their phone/email (lowercased) is the
-- key that survives the trip through the shared folder.
CREATE TABLE IF NOT EXISTS competitions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    join_code   TEXT UNIQUE NOT NULL,
    owner_key   TEXT NOT NULL,
    owner_name  TEXT,
    level       INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competition_players (
    competition_id TEXT NOT NULL,
    player_key     TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    level          INTEGER NOT NULL DEFAULT 1,
    rangolis       INTEGER NOT NULL DEFAULT 0,
    best_score     REAL    NOT NULL DEFAULT 0,
    total_score    REAL    NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL,
    PRIMARY KEY (competition_id, player_key)
);

CREATE TABLE IF NOT EXISTS competition_invitations (
    id             TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL,
    from_name      TEXT,
    to_contact     TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    resolved_at    TEXT
);

-- Every scored rangoli anyone has ever put into a competition, ours and
-- theirs. The id is minted by whoever scored it, so replaying the same
-- mailbox file twice cannot inflate a rank.
CREATE TABLE IF NOT EXISTS competition_results (
    id             TEXT PRIMARY KEY,
    competition_id TEXT,
    player_key     TEXT NOT NULL,
    display_name   TEXT,
    design         TEXT,
    score          REAL,
    out_of         REAL,
    level          INTEGER,
    scored_by      TEXT,
    timestamp      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(app_dir):
    """Open (creating if needed) the local database next to the app."""
    path = os.path.join(app_dir, DB_FILENAME)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def checkpoint_and_close(conn):
    """Fold the write-ahead log back into the main file and close cleanly."""
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.commit()
    except sqlite3.Error:
        pass
    conn.close()


def new_id():
    return uuid.uuid4().hex


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── passwords ────────────────────────────────────────────────────────────
def hash_password(password, salt=None):
    salt = salt or uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return digest.hex(), salt


def verify_password(password, password_hash, salt):
    check, _ = hash_password(password, salt)
    return _consteq(check, password_hash)


def _consteq(a, b):
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


# ── users ────────────────────────────────────────────────────────────────
def find_user_by_contact(conn, contact):
    contact = (contact or "").strip().lower()
    if not contact:
        return None
    row = conn.execute(
        "SELECT * FROM users WHERE lower(phone) = ? OR lower(email) = ?",
        (contact, contact)).fetchone()
    return dict(row) if row else None


def get_user(conn, user_id):
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_user(conn, display_name, contact, password, avatar=None):
    if find_user_by_contact(conn, contact):
        raise ValueError("An account with that phone/email already exists.")
    uid = new_id()
    pw_hash, salt = hash_password(password)
    is_email = "@" in contact
    conn.execute(
        "INSERT INTO users (id, display_name, phone, email, avatar, "
        "password_hash, password_salt, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (uid, display_name.strip(), None if is_email else contact.strip(),
         contact.strip() if is_email else None, avatar, pw_hash, salt, now()))
    conn.commit()
    return get_user(conn, uid)


def authenticate(conn, contact, password):
    user = find_user_by_contact(conn, contact)
    if not user:
        raise ValueError("No account with that phone/email on this device.")
    if not verify_password(password, user["password_hash"], user["password_salt"]):
        raise ValueError("Incorrect password.")
    return user


# ── families ─────────────────────────────────────────────────────────────
def _make_join_code():
    import random, string
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def create_family(conn, name, owner_id):
    fid = new_id()
    code = _make_join_code()
    while conn.execute("SELECT 1 FROM families WHERE join_code = ?",
                        (code,)).fetchone():
        code = _make_join_code()
    conn.execute(
        "INSERT INTO families (id, name, join_code, owner_id, created_at) "
        "VALUES (?,?,?,?,?)", (fid, name.strip(), code, owner_id, now()))
    conn.execute(
        "INSERT INTO family_members (family_id, user_id, role, joined_at) "
        "VALUES (?,?,?,?)", (fid, owner_id, "owner", now()))
    conn.commit()
    return get_family(conn, fid)


def get_family(conn, family_id):
    row = conn.execute("SELECT * FROM families WHERE id = ?",
                        (family_id,)).fetchone()
    return dict(row) if row else None


def find_family_by_code(conn, code):
    row = conn.execute("SELECT * FROM families WHERE join_code = ?",
                        (code.strip().upper(),)).fetchone()
    return dict(row) if row else None


def user_families(conn, user_id):
    rows = conn.execute(
        "SELECT f.* FROM families f JOIN family_members m ON m.family_id = f.id "
        "WHERE m.user_id = ? ORDER BY f.created_at", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def add_member(conn, family_id, user_id, role="member"):
    conn.execute(
        "INSERT OR IGNORE INTO family_members (family_id, user_id, role, "
        "joined_at) VALUES (?,?,?,?)", (family_id, user_id, role, now()))
    conn.commit()


def family_members(conn, family_id):
    rows = conn.execute(
        "SELECT u.*, m.role FROM family_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.family_id = ? ORDER BY m.joined_at", (family_id,)).fetchall()
    return [dict(r) for r in rows]


# ── invitations ──────────────────────────────────────────────────────────
def create_invitation(conn, family_id, from_user_id, to_contact):
    to_user = find_user_by_contact(conn, to_contact)
    iid = new_id()
    conn.execute(
        "INSERT INTO invitations (id, family_id, from_user_id, to_user_id, "
        "to_contact, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (iid, family_id, from_user_id, to_user["id"] if to_user else None,
         to_contact.strip(), "pending", now()))
    conn.commit()
    return iid


def incoming_invitations(conn, user):
    """Pending invitations addressed to this user, by id or by contact."""
    contacts = [c.strip().lower() for c in
                (user.get("phone"), user.get("email")) if c]
    contact_slots = ",".join("?" * len(contacts)) if contacts else "NULL"
    sql = (
        "SELECT i.*, f.name AS family_name, u.display_name AS from_name "
        "FROM invitations i "
        "JOIN families f ON f.id = i.family_id "
        "JOIN users u ON u.id = i.from_user_id "
        "WHERE i.status = 'pending' AND "
        f"(i.to_user_id = ? OR lower(i.to_contact) IN ({contact_slots}))")
    rows = conn.execute(sql, (user["id"], *contacts)).fetchall()
    return [dict(r) for r in rows]


def resolve_invitation(conn, invitation_id, accept, user_id=None):
    conn.execute(
        "UPDATE invitations SET status = ?, resolved_at = ? WHERE id = ?",
        ("accepted" if accept else "declined", now(), invitation_id))
    if accept:
        row = conn.execute("SELECT family_id FROM invitations WHERE id = ?",
                           (invitation_id,)).fetchone()
        if row and user_id:
            add_member(conn, row["family_id"], user_id)
    conn.commit()


# ── shares & progress ────────────────────────────────────────────────────
def add_share(conn, family_id, from_user_id, kind, name, payload):
    sid = new_id()
    conn.execute(
        "INSERT INTO shares (id, family_id, from_user_id, kind, name, "
        "payload_json, created_at) VALUES (?,?,?,?,?,?,?)",
        (sid, family_id, from_user_id, kind, name,
         json.dumps(payload), now()))
    conn.commit()
    return sid


def family_shares(conn, family_id, limit=50):
    rows = conn.execute(
        "SELECT s.*, u.display_name AS from_name FROM shares s "
        "JOIN users u ON u.id = s.from_user_id WHERE s.family_id = ? "
        "ORDER BY s.created_at DESC LIMIT ?", (family_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.pop("payload_json"))
        except (TypeError, ValueError):
            d["payload"] = {}
        out.append(d)
    return out


def add_progress(conn, family_id, user_id, session):
    pid = new_id()
    conn.execute(
        "INSERT INTO progress (id, family_id, user_id, design, complexity, "
        "score, out_of, scored_by, lesson, level, timestamp, photo, "
        "improvements_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, family_id, user_id, session.get("design"),
         session.get("complexity"), session.get("score"),
         session.get("out_of"), session.get("scored_by"),
         session.get("lesson"), session.get("level"),
         session.get("timestamp") or now(), session.get("photo"),
         json.dumps(session.get("improvements", []))))
    conn.commit()
    return pid


def family_progress(conn, family_id, limit=50):
    rows = conn.execute(
        "SELECT p.*, u.display_name AS from_name FROM progress p "
        "JOIN users u ON u.id = p.user_id WHERE p.family_id = ? "
        "ORDER BY p.timestamp DESC LIMIT ?", (family_id, limit)).fetchall()
    return [dict(r) for r in rows]


# ── competitions ─────────────────────────────────────────────────────────
# Ranking metric, used identically by the per-competition board and the world
# board: level first, then how many rangolis you have actually finished, then
# your best score. Level leads because that is what the child is climbing;
# rangolis break the tie because turning up repeatedly is the habit the whole
# app exists to build.
def _rank_key(row):
    return (-int(row.get("level") or 1),
            -int(row.get("rangolis") or 0),
            -float(row.get("best_score") or 0.0),
            (row.get("display_name") or "").lower())


def player_key_for(user):
    """The identity a player carries between devices.

    Local user ids are meaningless on anyone else's machine, so a player is
    keyed by whichever contact they signed up with. Falls back to the local
    id only for an account with no contact at all, which then simply never
    matches anyone else — the right failure, since it cannot be addressed.
    """
    for field in ("email", "phone"):
        val = (user.get(field) or "").strip().lower()
        if val:
            return val
    return user["id"]


def create_competition(conn, name, owner_key, owner_name, level=None):
    cid = new_id()
    code = _make_join_code()
    while conn.execute("SELECT 1 FROM competitions WHERE join_code = ?",
                       (code,)).fetchone():
        code = _make_join_code()
    conn.execute(
        "INSERT INTO competitions (id, name, join_code, owner_key, owner_name, "
        "level, created_at) VALUES (?,?,?,?,?,?,?)",
        (cid, name.strip(), code, owner_key, owner_name, level, now()))
    conn.commit()
    return get_competition(conn, cid)


def upsert_competition(conn, cid, name, code, owner_key, owner_name, level,
                       created_at=None):
    """Write down a competition somebody else created (arrived by mailbox)."""
    conn.execute(
        "INSERT INTO competitions (id, name, join_code, owner_key, owner_name, "
        "level, created_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
        "level = excluded.level",
        (cid, name, code, owner_key, owner_name, level, created_at or now()))
    conn.commit()
    return get_competition(conn, cid)


def get_competition(conn, competition_id):
    row = conn.execute("SELECT * FROM competitions WHERE id = ?",
                       (competition_id,)).fetchone()
    return dict(row) if row else None


def find_competition_by_code(conn, code):
    row = conn.execute("SELECT * FROM competitions WHERE join_code = ?",
                       (code.strip().upper(),)).fetchone()
    return dict(row) if row else None


def join_competition(conn, competition_id, player_key, display_name, level=1):
    conn.execute(
        "INSERT INTO competition_players (competition_id, player_key, "
        "display_name, level, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(competition_id, player_key) DO UPDATE SET "
        "display_name = excluded.display_name, level = excluded.level",
        (competition_id, player_key, display_name, int(level or 1), now()))
    conn.commit()


def my_competitions(conn, player_key):
    rows = conn.execute(
        "SELECT c.* FROM competitions c JOIN competition_players p "
        "ON p.competition_id = c.id WHERE p.player_key = ? "
        "ORDER BY c.created_at DESC", (player_key,)).fetchall()
    return [dict(r) for r in rows]


def add_competition_result(conn, result):
    """Record one scored rangoli and fold it into that player's standing.

    Returns False when the result has been seen before — the same mailbox
    file being applied twice must not count as two rangolis.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO competition_results (id, competition_id, "
        "player_key, display_name, design, score, out_of, level, scored_by, "
        "timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (result.get("id") or new_id(), result.get("competition_id"),
         result["player_key"], result.get("display_name"),
         result.get("design"), result.get("score"), result.get("out_of"),
         result.get("level"), result.get("scored_by"),
         result.get("timestamp") or now()))
    if cur.rowcount == 0:
        return False
    _restat_player(conn, result.get("competition_id"), result["player_key"],
                   result.get("display_name"), result.get("level"))
    conn.commit()
    return True


def _restat_player(conn, competition_id, player_key, display_name, level):
    """Recompute a player's standing from their results, rather than adding
    to a running total — a re-synced folder can then never double-count."""
    if competition_id is None:
        return
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(score) AS best, SUM(score) AS total "
        "FROM competition_results WHERE player_key = ? AND "
        "(competition_id IS ? OR competition_id = ?)",
        (player_key, competition_id, competition_id)).fetchone()
    conn.execute(
        "INSERT INTO competition_players (competition_id, player_key, "
        "display_name, level, rangolis, best_score, total_score, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(competition_id, player_key) DO UPDATE SET "
        "display_name = COALESCE(excluded.display_name, display_name), "
        "level = MAX(excluded.level, level), rangolis = excluded.rangolis, "
        "best_score = excluded.best_score, total_score = excluded.total_score, "
        "updated_at = excluded.updated_at",
        (competition_id, player_key, display_name, int(level or 1),
         row["n"] or 0, row["best"] or 0.0, row["total"] or 0.0, now()))


def competition_leaderboard(conn, competition_id):
    rows = conn.execute(
        "SELECT * FROM competition_players WHERE competition_id = ?",
        (competition_id,)).fetchall()
    board = sorted((dict(r) for r in rows), key=_rank_key)
    for i, row in enumerate(board, 1):
        row["rank"] = i
    return board


# Seed players for World ranking so the board is never empty on a fresh
# install. Keys are namespaced under ``npc:`` so they can never collide with
# a real contact-based player_key. Scores are fictional but sit in the same
# ranges real Learn Mode attempts produce, so a real child can climb past
# them as they improve.
WORLD_NPC_PLAYERS = (
    {"player_key": "npc:meera",    "display_name": "Meera",
     "level": 5, "rangolis": 28, "best_score": 9.6, "total_score": 248.0},
    {"player_key": "npc:arjun",    "display_name": "Arjun",
     "level": 5, "rangolis": 22, "best_score": 9.2, "total_score": 190.0},
    {"player_key": "npc:priya",    "display_name": "Priya",
     "level": 4, "rangolis": 19, "best_score": 8.9, "total_score": 155.0},
    {"player_key": "npc:leo",      "display_name": "Leo",
     "level": 4, "rangolis": 15, "best_score": 8.4, "total_score": 118.0},
    {"player_key": "npc:ananya",   "display_name": "Ananya",
     "level": 3, "rangolis": 14, "best_score": 8.1, "total_score": 102.0},
    {"player_key": "npc:sam",      "display_name": "Sam",
     "level": 3, "rangolis": 11, "best_score": 7.8, "total_score": 80.0},
    {"player_key": "npc:zara",     "display_name": "Zara",
     "level": 3, "rangolis": 9,  "best_score": 7.5, "total_score": 64.0},
    {"player_key": "npc:dev",      "display_name": "Dev",
     "level": 2, "rangolis": 8,  "best_score": 7.2, "total_score": 52.0},
    {"player_key": "npc:maya",     "display_name": "Maya",
     "level": 2, "rangolis": 6,  "best_score": 6.9, "total_score": 38.0},
    {"player_key": "npc:noah",     "display_name": "Noah",
     "level": 2, "rangolis": 5,  "best_score": 6.5, "total_score": 30.0},
    {"player_key": "npc:kavya",    "display_name": "Kavya",
     "level": 1, "rangolis": 4,  "best_score": 6.2, "total_score": 22.0},
    {"player_key": "npc:ben",      "display_name": "Ben",
     "level": 1, "rangolis": 3,  "best_score": 5.8, "total_score": 15.0},
)


def world_standings(conn):
    """Every player this device has ever heard of, ranked — plus a few
    fictional seed players so World ranking always has company.

    "World" is everyone whose results have reached this device through the
    shared folder, across all competitions, collapsed to one row per person,
    merged with WORLD_NPC_PLAYERS.
    """
    rows = conn.execute(
        "SELECT player_key, "
        "       MAX(display_name) AS display_name, "
        "       MAX(level)        AS level, "
        "       COUNT(*)          AS rangolis, "
        "       MAX(score)        AS best_score, "
        "       SUM(score)        AS total_score "
        "FROM competition_results GROUP BY player_key").fetchall()
    by_key = {r["player_key"]: dict(r) for r in rows}
    # NPCs fill empty slots; a real player with the same key would already
    # be in by_key (npc: keys never collide with contact keys).
    for npc in WORLD_NPC_PLAYERS:
        by_key.setdefault(npc["player_key"], dict(npc))
    board = sorted(by_key.values(), key=_rank_key)
    for i, row in enumerate(board, 1):
        row["rank"] = i
    return board


# ── competition invitations ──────────────────────────────────────────────
def create_competition_invitation(conn, competition_id, from_name, to_contact,
                                  invitation_id=None):
    iid = invitation_id or new_id()
    conn.execute(
        "INSERT OR IGNORE INTO competition_invitations (id, competition_id, "
        "from_name, to_contact, status, created_at) VALUES (?,?,?,?,?,?)",
        (iid, competition_id, from_name, (to_contact or "").strip(),
         "pending", now()))
    conn.commit()
    return iid


def incoming_competition_invitations(conn, user):
    contacts = [c.strip().lower() for c in
                (user.get("phone"), user.get("email")) if c]
    if not contacts:
        return []
    slots = ",".join("?" * len(contacts))
    rows = conn.execute(
        "SELECT i.*, c.name AS competition_name, c.join_code, c.level "
        "FROM competition_invitations i "
        "JOIN competitions c ON c.id = i.competition_id "
        f"WHERE i.status = 'pending' AND lower(i.to_contact) IN ({slots})",
        contacts).fetchall()
    return [dict(r) for r in rows]


def resolve_competition_invitation(conn, invitation_id, accept):
    conn.execute(
        "UPDATE competition_invitations SET status = ?, resolved_at = ? "
        "WHERE id = ?",
        ("accepted" if accept else "declined", now(), invitation_id))
    row = conn.execute(
        "SELECT competition_id FROM competition_invitations WHERE id = ?",
        (invitation_id,)).fetchone()
    conn.commit()
    return row["competition_id"] if row else None


# ── session (remember who was last signed in, on this device) ───────────
def save_session(conn, user_id):
    conn.execute(
        "INSERT INTO session (key, value) VALUES ('current_user_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (user_id,))
    conn.commit()


def clear_session(conn):
    conn.execute("DELETE FROM session WHERE key = 'current_user_id'")
    conn.commit()


def load_session(conn):
    row = conn.execute(
        "SELECT value FROM session WHERE key = 'current_user_id'").fetchone()
    return row["value"] if row else None
