"""Shared account + family data layer for Rangoli-Bot.

This one module is the schema AND the logic for both halves of the system:

    family_server.py  imports it  -> the ONLINE database
    family_client.py  imports it  -> the OFFLINE replica on this laptop

That is deliberate, and it is the whole trick. "Offline is an exact replica
of online" is not a promise anybody can keep by writing the rules down twice
and hoping the two copies stay honest with each other. Here there is only one
copy of the rules. A login that succeeds online succeeds offline for the same
reason, byte for byte, because it ran the same function against the same
CREATE TABLE.

Nothing outside the standard library is used. The app already ships to a
judging table where `pip install` may not be an option, so sqlite3 + hashlib
+ secrets is the entire dependency list.

── On passwords ───────────────────────────────────────────────────────────
Every user row carries a PBKDF2-SHA256 hash with a per-user random salt, and
that is what replicates to this laptop for every family member. It is what
the server itself stores -- the replica is complete, and any family member
can log in here with no internet using their real password.

What is NOT stored, anywhere, in either half, is a plaintext password. A
laptop carried to a competition is a laptop that can be left in a hall, and
a stolen replica must not also be a stolen list of the family's passwords
(which people reuse on their email and their bank). Verification works by
re-deriving the hash from what was typed and comparing it in constant time,
so the offline capability is genuinely identical without the liability.

── On sync ────────────────────────────────────────────────────────────────
Every synced row carries `updated_at` (ISO-8601 UTC) and `rev` (a monotonic
counter the server hands out). A client pulls everything with rev > its high
water mark, which makes catching up cheap and restartable.

Merge rules, per table, chosen so two people working offline cannot destroy
each other's work:

  users / families / family_members / invitations
        mutable rows -> last-write-wins on `updated_at`
  messages / shares / progress
        append-only, primary key generated on the client as a UUID ->
        insert-or-ignore, so a merge can never lose a chat message or a
        scored session no matter what order things arrive in

Invitation status is the one mutable field that matters and it moves in one
direction only (pending -> accepted/rejected/cancelled), so last-write-wins
cannot resurrect a declined invite.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import family_pg

SCHEMA_VERSION = 1

# OWASP's floor for PBKDF2-SHA256 is well above this, but this runs on the
# UI thread of a Tk app on a school laptop and a login that takes a visible
# second reads as "broken" to a child. 240k is a defensible middle: ~0.15s
# on the target hardware, and stored per-row so it can be raised later
# without invalidating existing accounts.
PBKDF2_ITERATIONS = 240_000

# Tables that participate in sync, in dependency order. Order matters on
# apply: a family_members row that lands before its user row would have
# nothing to point at.
SYNC_TABLES = [
    "users",
    "families",
    "family_members",
    "invitations",
    "messages",
    "shares",
    "progress",
]

# Append-only tables merge by insert-or-ignore; the rest are last-write-wins.
APPEND_ONLY = {"messages", "shares", "progress"}

PRIMARY_KEYS = {
    "users":          ("user_id",),
    "families":       ("family_id",),
    "family_members": ("family_id", "user_id"),
    "invitations":    ("invite_id",),
    "messages":       ("msg_id",),
    "shares":         ("share_id",),
    "progress":       ("session_id",),
}

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT,
    phone        TEXT,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'adult',
    avatar       TEXT DEFAULT '',
    pw_salt      TEXT NOT NULL,
    pw_hash      TEXT NOT NULL,
    pw_iter      INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    rev          INTEGER NOT NULL DEFAULT 0
);
-- Partial uniqueness: an account is created with EITHER an email or a phone,
-- so the unused column is NULL and many users may share that NULL.
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email
    ON users(email) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone
    ON users(phone) WHERE phone IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_users_rev ON users(rev);

CREATE TABLE IF NOT EXISTS families (
    family_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_families_rev ON families(rev);

CREATE TABLE IF NOT EXISTS family_members (
    family_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    member_role TEXT NOT NULL DEFAULT 'member',   -- 'owner' | 'member'
    can_invite  INTEGER NOT NULL DEFAULT 1,
    joined_at   TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (family_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_members_rev  ON family_members(rev);
CREATE INDEX IF NOT EXISTS ix_members_user ON family_members(user_id);

CREATE TABLE IF NOT EXISTS invitations (
    invite_id     TEXT PRIMARY KEY,
    family_id     TEXT NOT NULL,
    from_user_id  TEXT NOT NULL,
    to_contact    TEXT NOT NULL,      -- normalised email or phone
    to_user_id    TEXT,               -- filled in once an account matches
    status        TEXT NOT NULL,      -- pending|accepted|rejected|cancelled
    created_at    TEXT NOT NULL,
    responded_at  TEXT,
    updated_at    TEXT NOT NULL,
    rev           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_inv_rev     ON invitations(rev);
CREATE INDEX IF NOT EXISTS ix_inv_contact ON invitations(to_contact, status);
CREATE INDEX IF NOT EXISTS ix_inv_family  ON invitations(family_id, status);

CREATE TABLE IF NOT EXISTS messages (
    msg_id       TEXT PRIMARY KEY,
    family_id    TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    body         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'text',   -- text|sticker|encouragement
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    rev          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_msg_rev    ON messages(rev);
CREATE INDEX IF NOT EXISTS ix_msg_family ON messages(family_id, created_at);

CREATE TABLE IF NOT EXISTS shares (
    share_id     TEXT PRIMARY KEY,
    family_id    TEXT NOT NULL,
    from_user_id TEXT NOT NULL,
    kind         TEXT NOT NULL,   -- notebook_page|design|photo
    title        TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',   -- JSON
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    rev          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_share_rev    ON shares(rev);
CREATE INDEX IF NOT EXISTS ix_share_family ON shares(family_id, created_at);

CREATE TABLE IF NOT EXISTS progress (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    family_id    TEXT,
    design       TEXT NOT NULL DEFAULT '',
    complexity   TEXT NOT NULL DEFAULT '',
    score        REAL,
    out_of       REAL,
    scored_by    TEXT NOT NULL DEFAULT 'sample',   -- ai|sample|human
    lesson       TEXT NOT NULL DEFAULT '',
    level        INTEGER NOT NULL DEFAULT 1,
    photo        TEXT NOT NULL DEFAULT '',
    improvements TEXT NOT NULL DEFAULT '[]',       -- JSON array
    timestamp    TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    rev          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_prog_rev    ON progress(rev);
CREATE INDEX IF NOT EXISTS ix_prog_user   ON progress(user_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_prog_family ON progress(family_id, timestamp);

-- Server-side only in practice, but created everywhere so the two databases
-- really are the same shape.
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ── errors ────────────────────────────────────────────────────────────────

class FamilyError(Exception):
    """A rule was broken in a way the user needs to be told about.

    `code` is stable and machine-readable so the HTTP layer and the Tk layer
    can react to the same failure without matching on prose.
    """

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── time, ids, contacts ───────────────────────────────────────────────────

def utcnow():
    """ISO-8601 UTC to the second, sortable as a plain string.

    Sync compares these lexicographically, so a fixed width and a fixed zone
    are load-bearing -- local time would make a device in a different zone
    look like it wrote the future.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id():
    return uuid.uuid4().hex


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def normalise_contact(raw):
    """Return ('email'|'phone', canonical) or raise.

    Invitations are addressed to a contact string typed by one person and
    matched against an account created by another, months apart. Without a
    canonical form, "Grandma@Gmail.com " and "+91 98765 43210" never find
    the accounts they belong to. Both sides call this, always.
    """
    if raw is None:
        raise FamilyError("contact_required", "Enter an email address or phone number.")
    s = str(raw).strip()
    if not s:
        raise FamilyError("contact_required", "Enter an email address or phone number.")

    if "@" in s:
        s = s.lower()
        if not _EMAIL_RE.match(s):
            raise FamilyError("bad_email", "That does not look like an email address.")
        return "email", s

    # Phone: keep a leading +, drop every separator humans type.
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        raise FamilyError(
            "bad_contact", "Enter a valid email address or phone number.")
    if len(digits) < 7 or len(digits) > 15:      # ITU E.164 bounds
        raise FamilyError("bad_phone", "That does not look like a phone number.")
    return "phone", ("+" + digits) if plus else digits


# ── password hashing ──────────────────────────────────────────────────────

def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    if not password or len(password) < 6:
        raise FamilyError(
            "weak_password", "Password must be at least 6 characters.")
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return salt, dk.hex(), iterations


def verify_password(password, salt, expected_hash, iterations):
    """Constant-time verify. Never raises on bad input -- returns False.

    A timing-safe compare is the point: a plain `==` on hex digests leaks
    how many leading characters were right, one request at a time.
    """
    if not password or not salt or not expected_hash:
        return False
    try:
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), expected_hash)


# ── database ──────────────────────────────────────────────────────────────

def is_url(path):
    """True if `path` addresses the online database rather than a file."""
    return family_pg.is_pg_url(path)


def redact(path):
    """A database target safe to print: the password becomes `***`.

    Startup banners and error messages both name the database, and the
    online one carries its password in the middle of the URL.
    """
    if not is_url(path):
        return path
    scheme, _, rest = path.partition("://")
    creds, at, host = rest.rpartition("@")
    if not at:
        return path
    user, colon, _pw = creds.partition(":")
    return "%s://%s%s@%s" % (scheme, user, ":***" if colon else "", host)


def connect(path):
    """Open (creating if needed) a family database at `path`.

    `path` is a filename for the offline replica, or a `postgresql://` URL
    for the online database (Supabase). The URL form goes through
    `family_pg`, which presents the same connection surface, so nothing
    below this function needs to know which half it is talking to.

    `check_same_thread=False` because both callers hand the connection
    across threads on purpose: the client syncs on a background thread
    while the Tk UI reads on the main one, and the server hands each
    request thread its own connection. sqlite3's default guard would
    reject the first case outright.

    The safety it gives up has to be paid for elsewhere, and is:
    FamilyClient serialises every access through one RLock, and
    family_server keeps a separate connection per thread so two threads
    never share one. Anything else opening this database must do the same.
    """
    if family_pg.is_pg_url(path):
        conn = family_pg.connect(path)
    else:
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(path, timeout=15, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)",
                     (str(SCHEMA_VERSION),))
    return conn


def _row_to_dict(row):
    return dict(row) if row is not None else None


def next_rev(conn):
    """Hand out the next monotonic revision.

    Deliberately NOT max(rev)+1 across the tables: that recycles a number
    whenever the highest row is deleted, and a client whose high-water mark
    is already past it would skip the replacement forever.
    """
    cur = conn.execute("SELECT value FROM meta WHERE key='rev_counter'")
    row = cur.fetchone()
    val = int(row["value"]) + 1 if row else 1
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('rev_counter',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(val),))
    return val


def current_rev(conn):
    cur = conn.execute("SELECT value FROM meta WHERE key='rev_counter'")
    row = cur.fetchone()
    return int(row["value"]) if row else 0


def get_meta(conn, key, default=None):
    cur = conn.execute("SELECT value FROM meta WHERE key=?", (key,))
    row = cur.fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))


# ── users ─────────────────────────────────────────────────────────────────

def public_user(row):
    """A user as everyone else is allowed to see them: no salt, no hash."""
    if row is None:
        return None
    d = dict(row)
    for secret_field in ("pw_salt", "pw_hash", "pw_iter"):
        d.pop(secret_field, None)
    return d


def find_user_by_contact(conn, contact):
    kind, value = normalise_contact(contact)
    column = "email" if kind == "email" else "phone"
    cur = conn.execute(
        "SELECT * FROM users WHERE %s = ?" % column, (value,))
    return _row_to_dict(cur.fetchone())


def get_user(conn, user_id):
    cur = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return _row_to_dict(cur.fetchone())


def create_user(conn, contact, password, display_name,
                role="adult", avatar="", user_id=None, now=None):
    """Register an account keyed on whichever contact they chose.

    Returns the full row INCLUDING the hash fields -- the caller decides
    whether it is replicating (keep them) or answering a request (strip
    them via public_user).
    """
    kind, value = normalise_contact(contact)
    display_name = (display_name or "").strip()
    if not display_name:
        raise FamilyError("name_required", "Enter a name.")
    if len(display_name) > 60:
        raise FamilyError("name_too_long", "That name is too long.")
    if role not in ("adult", "child"):
        role = "adult"

    salt, pw_hash, iters = hash_password(password)
    now = now or utcnow()
    uid = user_id or new_id()
    rev = next_rev(conn)

    email = value if kind == "email" else None
    phone = value if kind == "phone" else None
    try:
        conn.execute(
            "INSERT INTO users(user_id,email,phone,display_name,role,avatar,"
            "pw_salt,pw_hash,pw_iter,created_at,updated_at,rev) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, email, phone, display_name, role, avatar or "",
             salt, pw_hash, iters, now, now, rev))
    except sqlite3.IntegrityError:
        raise FamilyError(
            "account_exists",
            "An account already uses that %s. Try logging in instead."
            % ("email address" if kind == "email" else "phone number"),
            status=409)
    return get_user(conn, uid)


def authenticate(conn, contact, password):
    """Verify credentials against this database. Same call online and off."""
    user = find_user_by_contact(conn, contact)
    # Deliberately identical message and comparable cost for "no such
    # account" and "wrong password", so neither the screen nor the clock
    # tells an attacker which contacts are registered.
    if user is None:
        hash_password_dummy()
        raise FamilyError(
            "bad_credentials", "That email/phone and password do not match.",
            status=401)
    if not verify_password(password, user["pw_salt"], user["pw_hash"],
                           user["pw_iter"]):
        raise FamilyError(
            "bad_credentials", "That email/phone and password do not match.",
            status=401)
    return user


def hash_password_dummy():
    """Burn the same PBKDF2 time as a real check on a missing account."""
    hashlib.pbkdf2_hmac("sha256", b"x", b"y" * 16, PBKDF2_ITERATIONS)


def touch_user(conn, user_id, **fields):
    """Update mutable profile fields and bump the revision."""
    allowed = {"display_name", "avatar", "role"}
    sets, values = [], []
    for key, val in fields.items():
        if key in allowed:
            sets.append("%s=?" % key)
            values.append(val)
    if not sets:
        return get_user(conn, user_id)
    now = utcnow()
    sets += ["updated_at=?", "rev=?"]
    values += [now, next_rev(conn), user_id]
    conn.execute("UPDATE users SET %s WHERE user_id=?" % ",".join(sets), values)
    return get_user(conn, user_id)


# ── families ──────────────────────────────────────────────────────────────

def create_family(conn, owner_id, name, family_id=None, now=None):
    now = now or utcnow()
    fid = family_id or new_id()
    name = (name or "").strip() or "My Family"
    conn.execute(
        "INSERT INTO families(family_id,name,created_by,created_at,updated_at,rev) "
        "VALUES(?,?,?,?,?,?)", (fid, name, owner_id, now, now, next_rev(conn)))
    conn.execute(
        "INSERT INTO family_members(family_id,user_id,member_role,can_invite,"
        "joined_at,active,updated_at,rev) VALUES(?,?,'owner',1,?,1,?,?)",
        (fid, owner_id, now, now, next_rev(conn)))
    return get_family(conn, fid)


def get_family(conn, family_id):
    cur = conn.execute("SELECT * FROM families WHERE family_id=?", (family_id,))
    return _row_to_dict(cur.fetchone())


def families_for_user(conn, user_id):
    cur = conn.execute(
        "SELECT f.* FROM families f JOIN family_members m "
        "ON m.family_id=f.family_id "
        "WHERE m.user_id=? AND m.active=1 ORDER BY f.created_at", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def family_members(conn, family_id):
    """Members joined to their user rows, hashes stripped."""
    cur = conn.execute(
        "SELECT m.member_role, m.can_invite, m.joined_at, u.* "
        "FROM family_members m JOIN users u ON u.user_id=m.user_id "
        "WHERE m.family_id=? AND m.active=1 "
        "ORDER BY (m.member_role='owner') DESC, u.display_name", (family_id,))
    out = []
    for row in cur.fetchall():
        d = public_user(row)
        out.append(d)
    return out


def is_member(conn, family_id, user_id):
    cur = conn.execute(
        "SELECT 1 FROM family_members WHERE family_id=? AND user_id=? AND active=1",
        (family_id, user_id))
    return cur.fetchone() is not None


def require_member(conn, family_id, user_id):
    if not is_member(conn, family_id, user_id):
        raise FamilyError(
            "not_a_member", "You are not part of that family.", status=403)


def add_member(conn, family_id, user_id, member_role="member",
               can_invite=1, now=None):
    now = now or utcnow()
    conn.execute(
        "INSERT INTO family_members(family_id,user_id,member_role,can_invite,"
        "joined_at,active,updated_at,rev) VALUES(?,?,?,?,?,1,?,?) "
        "ON CONFLICT(family_id,user_id) DO UPDATE SET "
        "active=1, updated_at=excluded.updated_at, rev=excluded.rev",
        (family_id, user_id, member_role, int(can_invite), now, now,
         next_rev(conn)))


# ── invitations ───────────────────────────────────────────────────────────
# No codes, no OTP. The sender types the other person's email or phone; the
# invitation appears in that person's app as a card with Accept / Reject.
# Everyone who is in a family may invite (the permission is on the member
# row so it can be tightened later without a migration).

def create_invitation(conn, family_id, from_user_id, to_contact,
                      invite_id=None, now=None):
    require_member(conn, family_id, from_user_id)
    cur = conn.execute(
        "SELECT can_invite FROM family_members WHERE family_id=? AND user_id=?",
        (family_id, from_user_id))
    row = cur.fetchone()
    if row is not None and not row["can_invite"]:
        raise FamilyError(
            "cannot_invite", "You do not have permission to invite people.",
            status=403)

    kind, value = normalise_contact(to_contact)

    # Inviting yourself is a dead end that looks like a bug when the card
    # shows up in your own list.
    inviter = get_user(conn, from_user_id)
    if inviter and value in (inviter.get("email"), inviter.get("phone")):
        raise FamilyError("self_invite", "That is your own account.")

    target = find_user_by_contact(conn, value)
    if target and is_member(conn, family_id, target["user_id"]):
        raise FamilyError(
            "already_member",
            "%s is already in this family." % target["display_name"])

    # Re-inviting somebody who already has a card waiting should not stack
    # up duplicates in their list.
    cur = conn.execute(
        "SELECT * FROM invitations WHERE family_id=? AND to_contact=? "
        "AND status='pending'", (family_id, value))
    existing = cur.fetchone()
    if existing is not None:
        raise FamilyError(
            "invite_pending",
            "An invitation to %s is already waiting for a reply." % value)

    now = now or utcnow()
    iid = invite_id or new_id()
    conn.execute(
        "INSERT INTO invitations(invite_id,family_id,from_user_id,to_contact,"
        "to_user_id,status,created_at,responded_at,updated_at,rev) "
        "VALUES(?,?,?,?,?,'pending',?,NULL,?,?)",
        (iid, family_id, from_user_id, value,
         target["user_id"] if target else None, now, now, next_rev(conn)))
    return get_invitation(conn, iid)


def get_invitation(conn, invite_id):
    cur = conn.execute("SELECT * FROM invitations WHERE invite_id=?", (invite_id,))
    return _row_to_dict(cur.fetchone())


def link_invitations_to_user(conn, user):
    """Attach invites addressed to a contact that only just got an account.

    Someone can be invited before they sign up -- that is the normal case,
    since you invite Grandma and then tell her to install it. Those rows
    have to_user_id NULL. This runs on signup and on login so the waiting
    card appears the first time she opens the app.
    """
    contacts = [c for c in (user.get("email"), user.get("phone")) if c]
    if not contacts:
        return 0
    placeholders = ",".join("?" * len(contacts))
    cur = conn.execute(
        "SELECT invite_id FROM invitations WHERE to_user_id IS NULL "
        "AND status='pending' AND to_contact IN (%s)" % placeholders, contacts)
    ids = [r["invite_id"] for r in cur.fetchall()]
    now = utcnow()
    for iid in ids:
        conn.execute(
            "UPDATE invitations SET to_user_id=?, updated_at=?, rev=? "
            "WHERE invite_id=?", (user["user_id"], now, next_rev(conn), iid))
    return len(ids)


def pending_invitations_for_user(conn, user_id):
    """Invitation cards to show this user, with who sent them and to which family."""
    user = get_user(conn, user_id)
    if not user:
        return []
    contacts = [c for c in (user.get("email"), user.get("phone")) if c]
    placeholders = ",".join("?" * len(contacts)) if contacts else "''"
    cur = conn.execute(
        "SELECT i.*, f.name AS family_name, u.display_name AS from_name, "
        "       u.avatar AS from_avatar "
        "FROM invitations i "
        "LEFT JOIN families f ON f.family_id = i.family_id "
        "LEFT JOIN users u    ON u.user_id  = i.from_user_id "
        "WHERE i.status='pending' AND (i.to_user_id=? OR i.to_contact IN (%s)) "
        "ORDER BY i.created_at DESC" % placeholders,
        [user_id] + contacts)
    return [dict(r) for r in cur.fetchall()]


def sent_invitations(conn, family_id):
    cur = conn.execute(
        "SELECT i.*, u.display_name AS from_name FROM invitations i "
        "LEFT JOIN users u ON u.user_id=i.from_user_id "
        "WHERE i.family_id=? ORDER BY i.created_at DESC", (family_id,))
    return [dict(r) for r in cur.fetchall()]


def respond_invitation(conn, invite_id, user_id, accept):
    """Accept or reject. Accepting is what actually joins the family."""
    inv = get_invitation(conn, invite_id)
    if inv is None:
        raise FamilyError("no_invite", "That invitation no longer exists.", 404)

    user = get_user(conn, user_id)
    if user is None:
        raise FamilyError("no_user", "Unknown account.", 404)
    owned = (inv["to_user_id"] == user_id
             or inv["to_contact"] in (user.get("email"), user.get("phone")))
    if not owned:
        raise FamilyError(
            "not_yours", "That invitation was not sent to you.", status=403)
    if inv["status"] != "pending":
        raise FamilyError(
            "already_answered",
            "That invitation was already %s." % inv["status"])

    now = utcnow()
    status = "accepted" if accept else "rejected"
    conn.execute(
        "UPDATE invitations SET status=?, to_user_id=?, responded_at=?, "
        "updated_at=?, rev=? WHERE invite_id=?",
        (status, user_id, now, now, next_rev(conn), invite_id))
    if accept:
        add_member(conn, inv["family_id"], user_id, "member", 1, now)
    return get_invitation(conn, invite_id)


def cancel_invitation(conn, invite_id, user_id):
    inv = get_invitation(conn, invite_id)
    if inv is None:
        raise FamilyError("no_invite", "That invitation no longer exists.", 404)
    require_member(conn, inv["family_id"], user_id)
    if inv["status"] != "pending":
        raise FamilyError("already_answered",
                          "That invitation was already %s." % inv["status"])
    now = utcnow()
    conn.execute(
        "UPDATE invitations SET status='cancelled', responded_at=?, "
        "updated_at=?, rev=? WHERE invite_id=?",
        (now, now, next_rev(conn), invite_id))
    return get_invitation(conn, invite_id)


# ── chat ──────────────────────────────────────────────────────────────────
# msg_id is generated by whoever composes the message, so a message written
# with no internet keeps the same identity when it finally uploads and can
# never be merged in twice.

def post_message(conn, family_id, from_user_id, body,
                 kind="text", msg_id=None, created_at=None):
    require_member(conn, family_id, from_user_id)
    body = (body or "").strip()
    if not body:
        raise FamilyError("empty_message", "Type something first.")
    if len(body) > 2000:
        raise FamilyError("message_too_long", "That message is too long.")
    now = created_at or utcnow()
    mid = msg_id or new_id()
    conn.execute(
        "INSERT OR IGNORE INTO messages(msg_id,family_id,from_user_id,body,"
        "kind,created_at,updated_at,rev) VALUES(?,?,?,?,?,?,?,?)",
        (mid, family_id, from_user_id, body, kind, now, now, next_rev(conn)))
    cur = conn.execute("SELECT * FROM messages WHERE msg_id=?", (mid,))
    return _row_to_dict(cur.fetchone())


def messages_for_family(conn, family_id, since=None, limit=200):
    """Newest `limit` messages, returned oldest-first for direct rendering."""
    if since:
        cur = conn.execute(
            "SELECT m.*, u.display_name AS from_name, u.avatar AS from_avatar "
            "FROM messages m LEFT JOIN users u ON u.user_id=m.from_user_id "
            "WHERE m.family_id=? AND m.created_at > ? "
            "ORDER BY m.created_at LIMIT ?", (family_id, since, limit))
        return [dict(r) for r in cur.fetchall()]
    cur = conn.execute(
        "SELECT m.*, u.display_name AS from_name, u.avatar AS from_avatar "
        "FROM messages m LEFT JOIN users u ON u.user_id=m.from_user_id "
        "WHERE m.family_id=? ORDER BY m.created_at DESC LIMIT ?",
        (family_id, limit))
    return list(reversed([dict(r) for r in cur.fetchall()]))


# ── shares (Kolam notebook pages, designs, photos) ────────────────────────

def create_share(conn, family_id, from_user_id, kind, title, payload,
                 share_id=None, created_at=None):
    require_member(conn, family_id, from_user_id)
    if kind not in ("notebook_page", "design", "photo"):
        raise FamilyError("bad_share_kind", "Unknown share type.")
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    now = created_at or utcnow()
    sid = share_id or new_id()
    conn.execute(
        "INSERT OR IGNORE INTO shares(share_id,family_id,from_user_id,kind,"
        "title,payload,created_at,updated_at,rev) VALUES(?,?,?,?,?,?,?,?,?)",
        (sid, family_id, from_user_id, kind, title or "", payload,
         now, now, next_rev(conn)))
    cur = conn.execute("SELECT * FROM shares WHERE share_id=?", (sid,))
    return _row_to_dict(cur.fetchone())


def shares_for_family(conn, family_id, kind=None, limit=100):
    sql = ("SELECT s.*, u.display_name AS from_name FROM shares s "
           "LEFT JOIN users u ON u.user_id=s.from_user_id WHERE s.family_id=?")
    args = [family_id]
    if kind:
        sql += " AND s.kind=?"
        args.append(kind)
    sql += " ORDER BY s.created_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ── progress ──────────────────────────────────────────────────────────────
# `scored_by` rides along on every row and is never defaulted to 'ai'. The
# competition rule this app is built under is that an AI-fallback or sample
# number must never be shown to a judge as a measured one, and that rule has
# to survive the trip through the family database too.

def record_progress(conn, user_id, family_id, session):
    sid = session.get("session_id") or new_id()
    now = session.get("timestamp") or utcnow()
    scored_by = session.get("scored_by") or "sample"
    if scored_by not in ("ai", "sample", "human"):
        scored_by = "sample"
    improvements = session.get("improvements") or []
    if not isinstance(improvements, str):
        improvements = json.dumps(improvements)
    conn.execute(
        "INSERT OR IGNORE INTO progress(session_id,user_id,family_id,design,"
        "complexity,score,out_of,scored_by,lesson,level,photo,improvements,"
        "timestamp,updated_at,rev) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, user_id, family_id, session.get("design", ""),
         session.get("complexity", ""), session.get("score"),
         session.get("out_of"), scored_by, session.get("lesson", ""),
         int(session.get("level") or 1), session.get("photo", ""),
         improvements, now, utcnow(), next_rev(conn)))
    cur = conn.execute("SELECT * FROM progress WHERE session_id=?", (sid,))
    return _row_to_dict(cur.fetchone())


def progress_for_user(conn, user_id, limit=500):
    cur = conn.execute(
        "SELECT * FROM progress WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit))
    return list(reversed([dict(r) for r in cur.fetchall()]))


def progress_for_family(conn, family_id, limit=500):
    cur = conn.execute(
        "SELECT p.*, u.display_name AS learner_name FROM progress p "
        "LEFT JOIN users u ON u.user_id=p.user_id "
        "WHERE p.family_id=? ORDER BY p.timestamp DESC LIMIT ?",
        (family_id, limit))
    return list(reversed([dict(r) for r in cur.fetchall()]))


# ── sync ──────────────────────────────────────────────────────────────────

def changes_since(conn, rev, user_id=None, limit_per_table=1000):
    """Everything with rev > `rev` that `user_id` is entitled to see.

    Scoped to the user's families, plus their own account, plus anyone who
    shares a family with them -- that last part is what puts family members'
    credential rows (hashes, never plaintext) into this laptop's replica so
    they can log in here with no internet.
    """
    out = {}
    if user_id:
        fams = [f["family_id"] for f in families_for_user(conn, user_id)]
    else:
        fams = None      # server-side full dump / admin path

    def fetch(table, sql, args):
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        out[table] = rows

    if fams is None:
        for table in SYNC_TABLES:
            fetch(table,
                  "SELECT * FROM %s WHERE rev > ? ORDER BY rev LIMIT ?" % table,
                  (rev, limit_per_table))
        return out

    fam_ph = ",".join("?" * len(fams)) if fams else "''"

    # Users: me, plus everyone in my families, plus anyone who has invited
    # me (so their name renders on the invitation card before I accept).
    fetch("users",
          "SELECT * FROM users WHERE rev > ? AND (user_id = ? OR user_id IN "
          "  (SELECT user_id FROM family_members WHERE family_id IN (%s)) "
          " OR user_id IN (SELECT from_user_id FROM invitations "
          "                WHERE to_user_id = ?)) "
          "ORDER BY rev LIMIT ?" % fam_ph,
          [rev, user_id] + fams + [user_id, limit_per_table])

    fetch("families",
          "SELECT * FROM families WHERE rev > ? AND (family_id IN (%s) "
          " OR family_id IN (SELECT family_id FROM invitations "
          "                  WHERE to_user_id = ?)) ORDER BY rev LIMIT ?" % fam_ph,
          [rev] + fams + [user_id, limit_per_table])

    fetch("family_members",
          "SELECT * FROM family_members WHERE rev > ? AND family_id IN (%s) "
          "ORDER BY rev LIMIT ?" % fam_ph,
          [rev] + fams + [limit_per_table])

    fetch("invitations",
          "SELECT * FROM invitations WHERE rev > ? AND (family_id IN (%s) "
          " OR to_user_id = ?) ORDER BY rev LIMIT ?" % fam_ph,
          [rev] + fams + [user_id, limit_per_table])

    for table in ("messages", "shares", "progress"):
        fetch(table,
              "SELECT * FROM %s WHERE rev > ? AND family_id IN (%s) "
              "ORDER BY rev LIMIT ?" % (table, fam_ph),
              [rev] + fams + [limit_per_table])

    return out


def apply_changes(conn, changes, assign_rev=False):
    """Merge a bundle of rows from the other side into this database.

    `assign_rev=True` is the server accepting a client's offline work: the
    incoming rev is meaningless there and gets replaced with a fresh local
    one so other clients notice the row. `assign_rev=False` is a client
    accepting the server's rows, where the server's rev IS the high-water
    mark and must be preserved exactly.
    """
    applied = 0
    for table in SYNC_TABLES:                     # dependency order
        for row in changes.get(table) or []:
            if _apply_row(conn, table, dict(row), assign_rev):
                applied += 1
    return applied


def _apply_row(conn, table, row, assign_rev):
    keys = PRIMARY_KEYS[table]
    where = " AND ".join("%s=?" % k for k in keys)
    key_vals = [row.get(k) for k in keys]
    if any(v is None for v in key_vals):
        return False

    # Joined display columns (from_name, family_name, ...) are conveniences
    # for the UI and have no home in the table -- drop anything unknown
    # rather than letting it blow up the INSERT.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    row = {k: v for k, v in row.items() if k in cols}
    if assign_rev:
        row["rev"] = next_rev(conn)

    cur = conn.execute(
        "SELECT * FROM %s WHERE %s" % (table, where), key_vals)
    existing = cur.fetchone()

    if existing is None:
        placeholders = ",".join("?" * len(row))
        conn.execute(
            "INSERT OR IGNORE INTO %s(%s) VALUES(%s)"
            % (table, ",".join(row.keys()), placeholders),
            list(row.values()))
        return True

    if table in APPEND_ONLY:
        return False        # already have it; append-only rows never change

    # Last-write-wins. Ties go to the incumbent, which makes the merge
    # deterministic regardless of which side ran it.
    if (row.get("updated_at") or "") <= (existing["updated_at"] or ""):
        return False

    sets = ",".join("%s=?" % k for k in row if k not in keys)
    if not sets:
        return False
    vals = [v for k, v in row.items() if k not in keys] + key_vals
    conn.execute("UPDATE %s SET %s WHERE %s" % (table, sets, where), vals)
    return True
