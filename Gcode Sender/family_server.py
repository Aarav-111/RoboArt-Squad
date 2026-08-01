"""The ONLINE half of Rangoli-Bot family accounts.

A small JSON API over `family_core`, built on nothing but the standard
library so it can be started on any machine that can already run the app:

    python family_server.py                  # localhost:8765
    python family_server.py --host 0.0.0.0   # reachable across the house wifi
    python family_server.py --port 80 --db /var/lib/rangoli/family.db

Point the app at it by writing the URL into family_config.json (the Family
screen has a field for it) and every device that can reach it shares one
live database. Run it on the parent's laptop for a LAN demo, or deploy the
same file to any host with a Python runtime for real internet sync -- the
code does not care which, and neither does the client.

Authentication is a bearer token handed out at login and stored in the
`sessions` table. Tokens are `secrets.token_urlsafe` and are compared
against the database, not derived from anything guessable.

── What this server is not ───────────────────────────────────────────────
It speaks plain HTTP. On a LAN behind a home router that is fine, and it is
what a competition demo will actually use. Exposed to the open internet it
must sit behind a TLS terminator (nginx, Caddy, any PaaS router) -- passwords
cross this wire on login, and hashing them client-side would only turn the
hash into the password. There is no rate limiting here either; put it behind
something that has it before pointing the public at it.
"""

import argparse
import json
import os
import secrets
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import family_core as core

DEFAULT_PORT = 8765
DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "family_server.db")
SESSION_DAYS = 60

# sqlite3 connections are not safe to share across threads, and this is a
# threading server. One connection per thread, opened on first use.
_local = threading.local()
_DB_PATH = DEFAULT_DB


def db():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = core.connect(_DB_PATH)
        _local.conn = conn
    return conn


# ── sessions ──────────────────────────────────────────────────────────────

def issue_token(conn, user_id):
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (token, user_id,
         now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         (now + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return token


def user_for_token(conn, token):
    if not token:
        return None
    cur = conn.execute("SELECT * FROM sessions WHERE token=?", (token,))
    row = cur.fetchone()
    if row is None:
        return None
    if row["expires_at"] < core.utcnow():
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    return core.get_user(conn, row["user_id"])


# ── route table ───────────────────────────────────────────────────────────
# Handlers take (conn, user, body, query) and return a JSON-able dict.
# `user` is None only on the routes listed in PUBLIC_ROUTES.

ROUTES = {}
PUBLIC_ROUTES = {("GET", "/api/ping"), ("POST", "/api/signup"),
                 ("POST", "/api/login"), ("POST", "/api/adopt")}


def route(method, path):
    def deco(fn):
        ROUTES[(method, path)] = fn
        return fn
    return deco


@route("GET", "/api/ping")
def r_ping(conn, user, body, query):
    """Cheap liveness probe -- the client's online/offline decision hangs on it."""
    return {"ok": True, "service": "rangoli-family", "rev": core.current_rev(conn)}


@route("POST", "/api/signup")
def r_signup(conn, user, body, query):
    u = core.create_user(
        conn,
        contact=body.get("contact"),
        password=body.get("password"),
        display_name=body.get("display_name"),
        role=body.get("role", "adult"),
        avatar=body.get("avatar", ""))
    # A family is created only when somebody actually starts one -- either
    # by naming it here, or later by sending their first invitation.
    # Handing every new account an empty family of its own means Grandma
    # accepts an invitation and is then in two families, staring at the
    # wrong (empty) one.
    fam_name = (body.get("family_name") or "").strip()
    if fam_name:
        core.create_family(conn, u["user_id"], fam_name)
    core.link_invitations_to_user(conn, u)
    return {"token": issue_token(conn, u["user_id"]), "user": core.public_user(u)}


@route("POST", "/api/login")
def r_login(conn, user, body, query):
    u = core.authenticate(conn, body.get("contact"), body.get("password"))
    core.link_invitations_to_user(conn, u)
    return {"token": issue_token(conn, u["user_id"]), "user": core.public_user(u)}


@route("POST", "/api/adopt")
def r_adopt(conn, user, body, query):
    """Take over an account that was created offline, hash and all.

    Somebody signs up with no internet -- on the way to a competition, in a
    hall with no wifi -- and their account exists only on that laptop. There
    is no token to sync with and no plaintext password left anywhere to
    re-register with, so without this the account could never reach the
    server and their family would never see them.

    The row arrives carrying the PBKDF2 hash it was created with, which is
    exactly what /api/signup would have stored, so the password keeps
    working everywhere afterwards.

    This is no weaker than signup: both let an unauthenticated caller claim
    an unused contact (there is no email/SMS verification anywhere in this
    system), and both refuse a contact that is already taken.
    """
    row = body.get("user") or {}
    for field in ("user_id", "display_name", "pw_salt", "pw_hash", "pw_iter"):
        if not row.get(field):
            raise core.FamilyError("bad_adopt", "Incomplete account record.")
    contact = row.get("email") or row.get("phone")
    kind, value = core.normalise_contact(contact)

    existing = core.find_user_by_contact(conn, value)
    if existing is not None:
        raise core.FamilyError(
            "account_exists",
            "An account already uses that %s on the server. Log in with its "
            "password instead." % ("email address" if kind == "email"
                                   else "phone number"), status=409)
    if core.get_user(conn, row["user_id"]) is not None:
        raise core.FamilyError("account_exists",
                               "That account is already on the server.", 409)

    now = core.utcnow()
    conn.execute(
        "INSERT INTO users(user_id,email,phone,display_name,role,avatar,"
        "pw_salt,pw_hash,pw_iter,created_at,updated_at,rev) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["user_id"], value if kind == "email" else None,
         value if kind == "phone" else None, row["display_name"],
         row.get("role", "adult"), row.get("avatar", ""),
         row["pw_salt"], row["pw_hash"], int(row["pw_iter"]),
         row.get("created_at") or now, now, core.next_rev(conn)))
    u = core.get_user(conn, row["user_id"])
    core.link_invitations_to_user(conn, u)
    return {"token": issue_token(conn, u["user_id"]), "user": core.public_user(u)}


@route("POST", "/api/logout")
def r_logout(conn, user, body, query):
    token = body.get("_token")
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
    return {"ok": True}


@route("GET", "/api/me")
def r_me(conn, user, body, query):
    return {"user": core.public_user(user),
            "families": core.families_for_user(conn, user["user_id"])}


@route("POST", "/api/family")
def r_create_family(conn, user, body, query):
    fam = core.create_family(conn, user["user_id"], body.get("name"))
    return {"family": fam}


@route("GET", "/api/family/members")
def r_members(conn, user, body, query):
    fid = query.get("family_id")
    core.require_member(conn, fid, user["user_id"])
    return {"members": core.family_members(conn, fid)}


@route("POST", "/api/invite")
def r_invite(conn, user, body, query):
    inv = core.create_invitation(
        conn, body.get("family_id"), user["user_id"], body.get("to_contact"))
    return {"invitation": inv}


@route("GET", "/api/invitations")
def r_invitations(conn, user, body, query):
    core.link_invitations_to_user(conn, user)
    out = {"incoming": core.pending_invitations_for_user(conn, user["user_id"])}
    fid = query.get("family_id")
    if fid and core.is_member(conn, fid, user["user_id"]):
        out["sent"] = core.sent_invitations(conn, fid)
    return out


@route("POST", "/api/invitation/respond")
def r_respond(conn, user, body, query):
    inv = core.respond_invitation(
        conn, body.get("invite_id"), user["user_id"], bool(body.get("accept")))
    return {"invitation": inv}


@route("POST", "/api/invitation/cancel")
def r_cancel(conn, user, body, query):
    return {"invitation": core.cancel_invitation(
        conn, body.get("invite_id"), user["user_id"])}


@route("GET", "/api/messages")
def r_messages(conn, user, body, query):
    fid = query.get("family_id")
    core.require_member(conn, fid, user["user_id"])
    return {"messages": core.messages_for_family(
        conn, fid, since=query.get("since"),
        limit=int(query.get("limit") or 200))}


@route("POST", "/api/messages")
def r_post_message(conn, user, body, query):
    msg = core.post_message(
        conn, body.get("family_id"), user["user_id"], body.get("body"),
        kind=body.get("kind", "text"), msg_id=body.get("msg_id"),
        created_at=body.get("created_at"))
    return {"message": msg}


@route("GET", "/api/shares")
def r_shares(conn, user, body, query):
    fid = query.get("family_id")
    core.require_member(conn, fid, user["user_id"])
    return {"shares": core.shares_for_family(conn, fid, kind=query.get("kind"))}


@route("POST", "/api/shares")
def r_post_share(conn, user, body, query):
    share = core.create_share(
        conn, body.get("family_id"), user["user_id"], body.get("kind"),
        body.get("title"), body.get("payload"),
        share_id=body.get("share_id"), created_at=body.get("created_at"))
    return {"share": share}


@route("GET", "/api/progress")
def r_progress(conn, user, body, query):
    fid = query.get("family_id")
    if fid:
        core.require_member(conn, fid, user["user_id"])
        return {"progress": core.progress_for_family(conn, fid)}
    return {"progress": core.progress_for_user(conn, user["user_id"])}


@route("POST", "/api/progress")
def r_post_progress(conn, user, body, query):
    row = core.record_progress(
        conn, user["user_id"], body.get("family_id"), body.get("session") or {})
    return {"progress": row}


@route("POST", "/api/sync")
def r_sync(conn, user, body, query):
    """The one endpoint that matters: push offline work up, pull the rest down.

    Push first, then read. Doing it in that order inside one request means
    the caller's own offline rows come back in the same response carrying
    their new server revisions, so the client's high-water mark advances
    past its own writes instead of re-downloading them next time.
    """
    pushed = 0
    push = body.get("push") or {}
    if push:
        pushed = core.apply_changes(conn, push, assign_rev=True)

    since = int(body.get("since_rev") or 0)
    changes = core.changes_since(conn, since, user_id=user["user_id"])
    return {"pushed": pushed,
            "rev": core.current_rev(conn),
            "changes": changes}


# ── HTTP plumbing ─────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "RangoliFamily/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("  %s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        body = {}
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8")) or {}
                except (ValueError, UnicodeDecodeError):
                    return self._send(400, {"error": "bad_json",
                                            "message": "Malformed JSON body."})

        handler = ROUTES.get((method, path))
        if handler is None:
            return self._send(404, {"error": "not_found",
                                    "message": "No such endpoint: %s" % path})

        conn = db()
        token = ""
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        body["_token"] = token

        user = None
        if (method, path) not in PUBLIC_ROUTES:
            user = user_for_token(conn, token)
            if user is None:
                return self._send(401, {"error": "auth_required",
                                        "message": "Please log in again."})

        try:
            # One transaction per request: a half-applied invite (row written,
            # membership not) would be worse than a clean failure.
            conn.execute("BEGIN IMMEDIATE")
            result = handler(conn, user, body, query)
            conn.execute("COMMIT")
            self._send(200, result)
        except core.FamilyError as e:
            conn.execute("ROLLBACK")
            self._send(e.status, {"error": e.code, "message": e.message})
        except Exception as e:                       # noqa: BLE001
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            if self.server.verbose:
                import traceback
                traceback.print_exc()
            self._send(500, {"error": "server_error", "message": str(e)})


def main():
    global _DB_PATH
    ap = argparse.ArgumentParser(description="Rangoli-Bot family sync server")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 (default) or 0.0.0.0 to allow other devices")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default=None,
                    help="database file, or a postgresql:// URL. Defaults to "
                         "$FAMILY_DB_URL if set, otherwise a local file.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    # The online database is addressed by a URL holding a password, so it
    # comes from the environment rather than the command line -- shell
    # history and `ps` output are both places it should not end up.
    target = args.db or os.environ.get("FAMILY_DB_URL") or DEFAULT_DB
    _DB_PATH = target if core.is_url(target) else os.path.abspath(target)
    core.connect(_DB_PATH).close()          # create/migrate before serving

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.verbose = not args.quiet
    httpd.daemon_threads = True

    print("Rangoli-Bot family server")
    print("  database : %s" % core.redact(_DB_PATH))
    if core.is_url(_DB_PATH):
        print("             ^ ONLINE (Supabase) -- shared with every device")
    else:
        # Falling back to a local file when the online database was meant is
        # a silent failure of exactly the kind that is discovered late: the
        # server works, accounts are created, and none of it is where the
        # family can see it. Say so at the only moment anyone is looking.
        print("             ^ LOCAL FILE, not the online database.")
        print("               Accounts made here stay on this laptop only.")
        print("               For Supabase, set FAMILY_DB_URL and restart"
              " (in VS Code,")
        print("               reopen the window so it picks the variable up).")
    print("  listening: http://%s:%d" % (args.host, args.port))
    if args.host == "127.0.0.1":
        print("  (this laptop only -- use --host 0.0.0.0 to let other "
              "devices connect)")
    print("  put this URL in the app's Family screen, then Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
