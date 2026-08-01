"""The OFFLINE half of Rangoli-Bot family accounts, and the bridge to online.

The app talks to this class and never to the network directly. Every call
does the same three things in the same order:

    1. write it to the local database   (always -- this is the source of truth
                                         the UI reads back from)
    2. mark the row as needing to go up (the outbox)
    3. if the server is reachable, push it now (otherwise: on the next sync)

Which means there is no "offline mode" to switch into and no feature that
quietly stops working when the wifi drops. Sending a chat message with no
internet writes the message, shows it, and delivers it when there is
internet. Logging in with no internet checks the same password hash the
server would have checked. Losing connectivity mid-session changes nothing
except how soon other people see your work.

The local database is created by `family_core.connect`, exactly as the
server's is -- same CREATE TABLE, same constraints, same functions
operating on it. A replica that drifts from the original is worse than no
replica, and the only reliable way to stop drift is to not write the rules
down twice.

── What is in the local replica ──────────────────────────────────────────
Everything the logged-in user is entitled to see: their family's members,
invitations, chat, shared notebook pages and progress -- plus the member
account rows, PBKDF2 hash and salt included. That last part is what lets
any family member log in on this laptop with no internet at all. No
plaintext password is ever stored, by either side; a hash is what the
server holds too, so the replica is complete without being a liability.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

import family_core as core

DEFAULT_CONFIG_NAME = "family_config.json"
DEFAULT_DB_NAME = "family_local.db"

# How long a reachability answer is trusted before we probe again. Long
# enough that a burst of UI actions costs one probe, short enough that
# coming back onto wifi is noticed within a few seconds.
ONLINE_CACHE_SECONDS = 8
PING_TIMEOUT = 2.5          # a dead server must not freeze the UI
REQUEST_TIMEOUT = 12.0      # sync can legitimately be slower than a ping


class FamilyClient:
    def __init__(self, app_dir, log=None):
        self.app_dir = app_dir
        self.config_path = os.path.join(app_dir, DEFAULT_CONFIG_NAME)
        self.db_path = os.path.join(app_dir, DEFAULT_DB_NAME)
        self._log = log or (lambda msg, tag="info": None)

        # One connection, one lock. Traffic is a handful of rows per action;
        # a connection pool would be machinery with nothing to do. The lock
        # matters because the auto-sync thread writes while the UI reads.
        self._lock = threading.RLock()
        self.conn = core.connect(self.db_path)

        self.config = self._load_config()
        self.server_url = (self.config.get("server_url") or "").rstrip("/")
        self.token = self.config.get("token") or ""

        self.current_user = None
        self.current_family_id = None

        self._online = False
        self._online_checked_at = 0.0
        self._sync_thread = None
        self._sync_stop = threading.Event()
        self._listeners = []

        self._restore_session()

    # ── config ────────────────────────────────────────────────────────────

    def _load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except OSError as e:
            self._log("Could not save family settings: %s" % e, "err")

    def set_server_url(self, url):
        """Point at a different server. Empty string = offline-only forever."""
        self.server_url = (url or "").strip().rstrip("/")
        self.config["server_url"] = self.server_url
        self._save_config()
        self._online_checked_at = 0.0        # re-probe immediately

    def _restore_session(self):
        """Come back as whoever was last logged in, with no network needed."""
        uid = self.config.get("user_id")
        if not uid:
            return
        with self._lock:
            user = core.get_user(self.conn, uid)
        if not user:
            # The config names somebody the database has never heard of --
            # a restored-from-backup .db, a wiped file, a config copied
            # between machines. Leaving the id in place means every later
            # save writes a session that can never be restored, so clear it
            # and let the sign-in screen do its job.
            self._log("The saved sign-in no longer matches this laptop's "
                      "family database — please sign in again.", "info")
            for key in ("user_id", "family_id", "token"):
                self.config.pop(key, None)
            self.token = ""
            self._save_config()
            return
        self.current_user = core.public_user(user)
        self.current_family_id = (self.config.get("family_id")
                                  or self._first_family_id(uid))

    def _first_family_id(self, user_id):
        with self._lock:
            fams = core.families_for_user(self.conn, user_id)
        return fams[0]["family_id"] if fams else None

    # ── change notification ───────────────────────────────────────────────

    def add_listener(self, fn):
        """Called (from the sync thread) after a sync brings anything new.

        The callback runs off the UI thread -- Tk callers must bounce it
        through root.after rather than touching widgets from here.
        """
        self._listeners.append(fn)

    def _notify(self, what):
        for fn in list(self._listeners):
            try:
                fn(what)
            except Exception:                     # noqa: BLE001
                pass

    # ── connectivity ──────────────────────────────────────────────────────

    def is_online(self, force=False):
        """Is the server reachable right now? Cached briefly, never blocking long."""
        if not self.server_url:
            return False
        now = time.time()
        if not force and (now - self._online_checked_at) < ONLINE_CACHE_SECONDS:
            return self._online
        self._online_checked_at = now
        try:
            req = urllib.request.Request(self.server_url + "/api/ping",
                                         method="GET")
            with urllib.request.urlopen(req, timeout=PING_TIMEOUT) as resp:
                self._online = (resp.status == 200)
        except Exception:                         # noqa: BLE001
            self._online = False
        return self._online

    def _api(self, method, path, body=None, query=None, timeout=REQUEST_TIMEOUT):
        """One HTTP call. Raises FamilyError on a refusal, OSError on a network fault.

        The two are kept distinct on purpose: a FamilyError is the server
        saying no and belongs on screen; an OSError just means we are
        offline and the caller should fall through to the local path.
        """
        if not self.server_url:
            raise OSError("no server configured")
        url = self.server_url + path
        if query:
            from urllib.parse import urlencode
            url += "?" + urlencode({k: v for k, v in query.items()
                                    if v is not None})
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token

        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:                     # noqa: BLE001
                payload = {}
            code = payload.get("error", "server_error")
            if code == "auth_required":
                # The token died (expired, or the server's database was
                # reset). Drop it so the next login re-authenticates rather
                # than looping on 401s.
                self.token = ""
                self.config.pop("token", None)
                self._save_config()
            raise core.FamilyError(
                code, payload.get("message", "The server refused that."),
                status=e.code)
        except urllib.error.URLError as e:
            raise OSError(str(e.reason))

    # ── outbox ────────────────────────────────────────────────────────────
    # Rows written here that the server has not seen yet. Keyed by table +
    # primary key rather than storing a copy of the row, so if the same row
    # is edited three times offline only the final state is uploaded.

    def _ensure_outbox(self):
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS outbox ("
            "  table_name TEXT NOT NULL,"
            "  pk         TEXT NOT NULL,"
            "  queued_at  TEXT NOT NULL,"
            "  PRIMARY KEY (table_name, pk))")

    def _queue(self, table, pk_values):
        self._ensure_outbox()
        self.conn.execute(
            "INSERT OR REPLACE INTO outbox(table_name,pk,queued_at) VALUES(?,?,?)",
            (table, json.dumps(list(pk_values)), core.utcnow()))

    def _collect_outbox(self):
        self._ensure_outbox()
        bundle, keys = {}, []
        cur = self.conn.execute("SELECT table_name, pk FROM outbox")
        for row in cur.fetchall():
            table = row["table_name"]
            if table not in core.PRIMARY_KEYS:
                continue
            pk_vals = json.loads(row["pk"])
            cols = core.PRIMARY_KEYS[table]
            where = " AND ".join("%s=?" % c for c in cols)
            got = self.conn.execute(
                "SELECT * FROM %s WHERE %s" % (table, where), pk_vals).fetchone()
            if got is not None:
                bundle.setdefault(table, []).append(dict(got))
            keys.append((table, row["pk"]))
        return bundle, keys

    def _clear_outbox(self, keys):
        for table, pk in keys:
            self.conn.execute(
                "DELETE FROM outbox WHERE table_name=? AND pk=?", (table, pk))

    def pending_count(self):
        """How many local changes are still waiting to reach the server."""
        with self._lock:
            self._ensure_outbox()
            row = self.conn.execute("SELECT COUNT(*) AS n FROM outbox").fetchone()
        return row["n"] if row else 0

    # ── auth ──────────────────────────────────────────────────────────────

    def signup(self, contact, password, display_name,
               role="adult", avatar="", family_name=""):
        """Create an account. Online if possible, locally if not.

        Online is preferred for signup specifically because contact
        uniqueness is only globally true on the server -- two people can
        register the same phone number on two offline laptops and only find
        out when they reconnect. That is handled (see _push_local_signup)
        but it is better avoided.
        """
        kind, value = core.normalise_contact(contact)      # validate early

        if self.is_online():
            try:
                resp = self._api("POST", "/api/signup", {
                    "contact": value, "password": password,
                    "display_name": display_name, "role": role,
                    "avatar": avatar, "family_name": family_name,
                })
                self._adopt_session(resp["token"], resp["user"])
                # Mirror the credentials down so this account can log in here
                # again with no internet. The hash comes from the local
                # derivation, which is deterministic given the same password.
                self._mirror_credentials(resp["user"], password)
                self.sync(force=True)
                return self.current_user
            except core.FamilyError:
                raise                       # "already registered" etc -- show it
            except OSError:
                pass                        # dropped mid-call; fall through

        return self._local_signup(value, password, display_name, role,
                                  avatar, family_name)

    def _local_signup(self, contact, password, display_name, role,
                      avatar, family_name):
        with self._lock:
            user = core.create_user(self.conn, contact, password, display_name,
                                    role=role, avatar=avatar)
            self._queue("users", (user["user_id"],))
            # Only start a family if they named one -- see the note in
            # family_server.r_signup. Someone who was invited here joins the
            # inviter's family instead of collecting an empty one.
            fam = None
            if (family_name or "").strip():
                fam = core.create_family(self.conn, user["user_id"], family_name)
                self._queue("families", (fam["family_id"],))
                self._queue("family_members",
                            (fam["family_id"], user["user_id"]))
            core.link_invitations_to_user(self.conn, user)
        self._adopt_session("", core.public_user(user))
        self._log("Account created offline — it will sync when the server "
                  "is reachable.", "info")
        return self.current_user

    def login(self, contact, password):
        """Log in. Tries the server, falls back to the local replica.

        Both paths verify the same PBKDF2 hash. Offline login therefore
        works for anyone whose account has reached this laptop: the people
        who have signed in here before, and every member of their family.
        """
        kind, value = core.normalise_contact(contact)

        if self.is_online():
            try:
                resp = self._api("POST", "/api/login",
                                 {"contact": value, "password": password})
                self._adopt_session(resp["token"], resp["user"])
                self._mirror_credentials(resp["user"], password)
                self.sync(force=True)
                return self.current_user
            except core.FamilyError as e:
                # A genuine "wrong password" from the server is final. But
                # do not let a server that has lost its database lock out
                # somebody whose account is sitting right here -- fall back
                # and let the local hash decide.
                if e.code == "bad_credentials":
                    with self._lock:
                        local = core.find_user_by_contact(self.conn, value)
                    if local is None:
                        raise
                else:
                    raise
            except OSError:
                pass

        with self._lock:
            user = core.authenticate(self.conn, value, password)
            core.link_invitations_to_user(self.conn, user)
        self._adopt_session(self.token, core.public_user(user))
        self._log("Logged in offline — using the copy of the family stored "
                  "on this laptop.", "info")
        return self.current_user

    def _mirror_credentials(self, user, password):
        """Ensure a server-authenticated account can also log in here offline.

        The server never sends hashes back over the API (public_user strips
        them), so on a successful online login we derive the hash locally
        from the password the user just typed. Same algorithm, same
        parameters, so the row that lands here is the row the server holds.
        """
        with self._lock:
            existing = core.get_user(self.conn, user["user_id"])
            if existing is not None:
                # Re-derive with the stored salt; if it already verifies,
                # the mirror is current and there is nothing to do.
                if core.verify_password(password, existing["pw_salt"],
                                        existing["pw_hash"],
                                        existing["pw_iter"]):
                    return
                salt, pw_hash, iters = core.hash_password(password)
                self.conn.execute(
                    "UPDATE users SET pw_salt=?, pw_hash=?, pw_iter=? "
                    "WHERE user_id=?",
                    (salt, pw_hash, iters, user["user_id"]))
                return
            salt, pw_hash, iters = core.hash_password(password)
            now = user.get("created_at") or core.utcnow()
            self.conn.execute(
                "INSERT OR IGNORE INTO users(user_id,email,phone,display_name,"
                "role,avatar,pw_salt,pw_hash,pw_iter,created_at,updated_at,rev) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (user["user_id"], user.get("email"), user.get("phone"),
                 user.get("display_name", ""), user.get("role", "adult"),
                 user.get("avatar", ""), salt, pw_hash, iters,
                 now, user.get("updated_at") or now, 0))

    def _adopt_session(self, token, user):
        self.token = token or ""
        self.current_user = user
        self._resolve_family()
        self._persist_session()

    def _resolve_family(self):
        """Pick a current family, dropping one we are no longer a member of.

        Called again after every sync: on an online signup the family is
        created server-side and only exists here once the first sync lands,
        so resolving it once at login time would leave the user family-less
        until they restarted.
        """
        if not self.current_user:
            return
        uid = self.current_user["user_id"]
        with self._lock:
            fams = [f["family_id"] for f in core.families_for_user(self.conn, uid)]
        if self.current_family_id in fams:
            return
        self.current_family_id = fams[0] if fams else None

    def _persist_session(self):
        self.config["user_id"] = (self.current_user or {}).get("user_id")
        self.config["family_id"] = self.current_family_id
        self.config["server_url"] = self.server_url
        if self.token:
            self.config["token"] = self.token
        else:
            self.config.pop("token", None)
        self._save_config()

    def logout(self):
        self.stop_auto_sync()
        if self.token and self.is_online():
            try:
                self._api("POST", "/api/logout", {})
            except Exception:                     # noqa: BLE001
                pass
        self.token = ""
        self.current_user = None
        self.current_family_id = None
        for key in ("token", "user_id", "family_id"):
            self.config.pop(key, None)
        self._save_config()

    def is_logged_in(self):
        return self.current_user is not None

    # ── families & members ────────────────────────────────────────────────

    def families(self):
        if not self.current_user:
            return []
        with self._lock:
            return core.families_for_user(self.conn, self.current_user["user_id"])

    def set_family(self, family_id):
        self.current_family_id = family_id
        self._persist_session()

    def family_name(self):
        if not self.current_family_id:
            return ""
        with self._lock:
            fam = core.get_family(self.conn, self.current_family_id)
        return fam["name"] if fam else ""

    def members(self):
        if not self.current_family_id:
            return []
        with self._lock:
            return core.family_members(self.conn, self.current_family_id)

    def create_family(self, name):
        uid = self.current_user["user_id"]
        with self._lock:
            fam = core.create_family(self.conn, uid, name)
            self._queue("families", (fam["family_id"],))
            self._queue("family_members", (fam["family_id"], uid))
        self.set_family(fam["family_id"])
        self._push_soon()
        return fam

    # ── invitations ───────────────────────────────────────────────────────
    # No code, no OTP: address it to a phone number or an email, and it
    # shows up in that person's app as Accept / Reject.

    def invite(self, to_contact):
        """Invite by phone or email. Starts a family if this is the first one.

        Inviting somebody is the moment a family becomes a real thing, so
        that is where it gets created rather than at signup.
        """
        core.normalise_contact(to_contact)        # fail before creating anything
        if not self.current_family_id:
            self.create_family("%s's Family"
                               % self.current_user.get("display_name", "My"))
        uid = self.current_user["user_id"]
        with self._lock:
            inv = core.create_invitation(
                self.conn, self.current_family_id, uid, to_contact)
            self._queue("invitations", (inv["invite_id"],))
        self._push_soon()
        return inv

    def incoming_invitations(self):
        if not self.current_user:
            return []
        with self._lock:
            return core.pending_invitations_for_user(
                self.conn, self.current_user["user_id"])

    def sent_invitations(self):
        if not self.current_family_id:
            return []
        with self._lock:
            return core.sent_invitations(self.conn, self.current_family_id)

    def respond_invitation(self, invite_id, accept):
        uid = self.current_user["user_id"]
        with self._lock:
            inv = core.respond_invitation(self.conn, invite_id, uid, accept)
            self._queue("invitations", (invite_id,))
            if accept:
                self._queue("family_members", (inv["family_id"], uid))
        if accept:
            # Switch to the family they just chose to join -- it is the one
            # they want to be looking at, and for most users it is their only
            # one. Someone in several families can switch back afterwards.
            self.set_family(inv["family_id"])
        self._push_soon()
        return inv

    def cancel_invitation(self, invite_id):
        with self._lock:
            inv = core.cancel_invitation(
                self.conn, invite_id, self.current_user["user_id"])
            self._queue("invitations", (invite_id,))
        self._push_soon()
        return inv

    # ── chat ──────────────────────────────────────────────────────────────

    def messages(self, since=None, limit=200):
        if not self.current_family_id:
            return []
        with self._lock:
            return core.messages_for_family(
                self.conn, self.current_family_id, since=since, limit=limit)

    def send_message(self, body, kind="text"):
        """Write, show, queue. Delivery is the sync engine's problem, not the child's."""
        if not self.current_family_id:
            raise core.FamilyError("no_family", "You are not in a family yet.")
        uid = self.current_user["user_id"]
        with self._lock:
            msg = core.post_message(
                self.conn, self.current_family_id, uid, body, kind=kind)
            self._queue("messages", (msg["msg_id"],))
        self._push_soon()
        return msg

    # ── shares ────────────────────────────────────────────────────────────

    def shares(self, kind=None, limit=100):
        if not self.current_family_id:
            return []
        with self._lock:
            return core.shares_for_family(
                self.conn, self.current_family_id, kind=kind, limit=limit)

    def share(self, kind, title, payload):
        if not self.current_family_id:
            raise core.FamilyError("no_family", "You are not in a family yet.")
        uid = self.current_user["user_id"]
        with self._lock:
            row = core.create_share(
                self.conn, self.current_family_id, uid, kind, title, payload)
            self._queue("shares", (row["share_id"],))
        self._push_soon()
        return row

    # ── progress ──────────────────────────────────────────────────────────

    def record_progress(self, session):
        """Publish one scored Learn Mode session to the family.

        `scored_by` is carried through untouched. A sample or AI-fallback
        score must stay labelled as one all the way to the relative who
        opens the progress tab -- family sharing is not a laundering step.
        """
        if not self.current_user:
            return None
        uid = self.current_user["user_id"]
        with self._lock:
            row = core.record_progress(
                self.conn, uid, self.current_family_id, session)
            self._queue("progress", (row["session_id"],))
        self._push_soon()
        return row

    def family_progress(self):
        if not self.current_family_id:
            return []
        with self._lock:
            return core.progress_for_family(self.conn, self.current_family_id)

    def my_progress(self):
        if not self.current_user:
            return []
        with self._lock:
            return core.progress_for_user(self.conn, self.current_user["user_id"])

    # ── sync ──────────────────────────────────────────────────────────────

    def sync(self, force=False):
        """Push everything queued, pull everything new. Safe to call often.

        Returns (pushed, pulled) or (0, 0) when there is nothing to talk to.
        Never raises for being offline -- that is a normal state here, not
        an error.
        """
        if not self.current_user or not self.server_url:
            return (0, 0)
        if not self.is_online(force=force):
            return (0, 0)
        if not self.token and not self._adopt_offline_account():
            # Created offline and the server would not take it (or already
            # knows that contact). Nothing can be attributed without a
            # session, so wait for a real login rather than pushing
            # unattributable rows.
            return (0, 0)

        try:
            with self._lock:
                push, keys = self._collect_outbox()
                since = int(core.get_meta(self.conn, "sync_rev", 0) or 0)

            resp = self._api("POST", "/api/sync",
                             {"since_rev": since, "push": push})

            with self._lock:
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    pulled = core.apply_changes(
                        self.conn, resp.get("changes") or {}, assign_rev=False)
                    self._clear_outbox(keys)
                    core.set_meta(self.conn, "sync_rev", resp.get("rev", since))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
            pushed = resp.get("pushed", 0)
            if pulled:
                # A pull can add the family we were just invited into, or
                # remove one we were taken out of.
                before = self.current_family_id
                self._resolve_family()
                if self.current_family_id != before:
                    self._persist_session()
                self._notify("sync")
            return (pushed, pulled)

        except core.FamilyError as e:
            if e.code == "account_exists":
                # An offline signup collided with a real account on the
                # server. Say so plainly rather than retrying forever.
                self._log(
                    "This phone/email is already registered on the server. "
                    "Log out and log in with that password to join up the "
                    "two copies.", "err")
                with self._lock:
                    self._clear_outbox([k for k in keys if k[0] == "users"])
            else:
                self._log("Sync refused: %s" % e.message, "err")
            return (0, 0)
        except OSError:
            self._online = False        # went away mid-sync; try again later
            return (0, 0)
        except Exception as e:                    # noqa: BLE001
            self._log("Sync failed: %s" % e, "err")
            return (0, 0)

    def _adopt_offline_account(self):
        """Hand an account created with no internet to the server, once.

        Signing up offline leaves a real account on this laptop and no
        session anywhere. There is no plaintext password kept to re-register
        with, so the stored hash is uploaded instead and the server issues a
        token against it -- see family_server.r_adopt.

        Returns True when there is a usable token afterwards.
        """
        if self.token:
            return True
        if not self.current_user:
            return False
        with self._lock:
            row = core.get_user(self.conn, self.current_user["user_id"])
        if row is None:
            return False
        try:
            resp = self._api("POST", "/api/adopt", {"user": dict(row)})
        except core.FamilyError as e:
            if e.code == "account_exists":
                self._log(
                    "This phone/email already has an account on the server. "
                    "Log out and log back in with that password to join the "
                    "two up.", "err")
            return False
        except OSError:
            return False

        self.token = resp.get("token", "")
        self._persist_session()
        if self.token:
            self._log("Your offline account is now on the family server.",
                      "info")
        return bool(self.token)

    def _push_soon(self):
        """Fire a sync off-thread so a chat send never blocks the UI."""
        # Deliberately not gated on having a token: sync() adopts an
        # offline-created account first, and gating here would mean that
        # account's very first push never happened.
        if not self.server_url:
            return
        threading.Thread(target=self.sync, daemon=True).start()

    def start_auto_sync(self, interval=10):
        """Poll in the background -- this is what makes chat feel live."""
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_stop.clear()

        def loop():
            while not self._sync_stop.wait(interval):
                if self.current_user:
                    self.sync()

        self._sync_thread = threading.Thread(target=loop, daemon=True)
        self._sync_thread.start()

    def stop_auto_sync(self):
        self._sync_stop.set()
        self._sync_thread = None

    def status_line(self):
        """One short string for the banner: where the data is going."""
        if not self.server_url:
            return "Offline only"
        if self._online:
            pending = self.pending_count()
            return "Online" if not pending else "Online · syncing %d" % pending
        pending = self.pending_count()
        return "Offline" if not pending else "Offline · %d waiting" % pending

    def close(self):
        self.stop_auto_sync()
        try:
            # Fold the write-ahead log back into the .db file before letting
            # go. In WAL mode recent writes live in a separate
            # `family_local.db-wal` sidecar, so the .db on its own can be
            # missing the newest accounts and messages. Checkpointing makes
            # the single file complete -- which is what anyone copying it to
            # a USB stick, a backup, or another laptop will assume.
            with self._lock:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:                         # noqa: BLE001
            pass
        try:
            self.conn.close()
        except Exception:                         # noqa: BLE001
            pass
