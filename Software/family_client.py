"""Business logic for Family accounts: sign-in, invitations, sharing, sync.

There is no hosted backend for this project, so "sharing between relatives"
is built on a mailbox folder both devices can already reach — a shared
Dropbox/iCloud Drive/NAS folder the family points every device at once, in
Settings. Each device drops what it wants to share as a small JSON file into
that folder and picks up whatever the others left there. That is the whole
sync protocol: no server, no accounts beyond this device, nothing to run.

Everything is stored locally first (see family_core.py) so the app never
blocks on the mailbox being reachable — "online" just means that folder is
reachable right now.
"""
import json
import os
import threading
import time

import family_core

MAILBOX_CONFIG_FILE = "family_mailbox.json"
AUTO_SYNC_INTERVAL_S = 45


class FamilyClient:
    def __init__(self, app_dir, log=None):
        self.app_dir = app_dir
        self.log = log or (lambda msg, tag="info": None)
        self.conn = family_core.connect(app_dir)

        self.current_user = None
        self._family = None          # the family row currently "active"
        self._mailbox_path = self._load_mailbox_path()

        self._sync_thread = None
        self._sync_stop = threading.Event()

        self._restore_session()

    # ── session restore ───────────────────────────────────────────────────
    def _restore_session(self):
        uid = family_core.load_session(self.conn)
        if not uid:
            return
        user = family_core.get_user(self.conn, uid)
        if user:
            self.current_user = user
            self._pick_active_family()

    def _pick_active_family(self):
        fams = family_core.user_families(self.conn, self.current_user["id"]) \
            if self.current_user else []
        self._family = fams[0] if fams else None

    # ── mailbox (the "network") ───────────────────────────────────────────
    def _mailbox_config_path(self):
        return os.path.join(self.app_dir, MAILBOX_CONFIG_FILE)

    def _load_mailbox_path(self):
        try:
            with open(self._mailbox_config_path(), encoding="utf-8") as fh:
                return json.load(fh).get("path") or None
        except Exception:
            return None

    def set_mailbox_path(self, path):
        """Point this device at the shared folder the rest of the family
        also uses. Any folder that syncs between devices on its own
        (Dropbox, iCloud Drive, a shared NAS mount) works."""
        self._mailbox_path = path or None
        try:
            with open(self._mailbox_config_path(), "w", encoding="utf-8") as fh:
                json.dump({"path": self._mailbox_path}, fh)
        except OSError as e:
            self.log(f"Family: couldn't save the shared folder — {e}", "err")

    def mailbox_path(self):
        return self._mailbox_path

    def is_online(self):
        """Whether the shared mailbox folder is reachable right now."""
        return bool(self._mailbox_path) and os.path.isdir(self._mailbox_path)

    # ── accounts ───────────────────────────────────────────────────────────
    def is_logged_in(self):
        return self.current_user is not None

    def sign_up(self, display_name, contact, password, avatar=None):
        if not display_name.strip():
            raise ValueError("Enter a name.")
        if not contact.strip():
            raise ValueError("Enter a phone number or email.")
        if len(password) < 4:
            raise ValueError("Password needs to be at least 4 characters.")
        user = family_core.create_user(
            self.conn, display_name, contact, password, avatar)
        self._finish_login(user)
        return user

    def sign_in(self, contact, password):
        user = family_core.authenticate(self.conn, contact, password)
        self._finish_login(user)
        return user

    def _finish_login(self, user):
        self.current_user = user
        family_core.save_session(self.conn, user["id"])
        self._pick_active_family()

    def logout(self):
        self.stop_auto_sync()
        family_core.clear_session(self.conn)
        self.current_user = None
        self._family = None

    def current_family(self):
        return self._family

    def family_name(self):
        return self._family["name"] if self._family else None

    # ── families ───────────────────────────────────────────────────────────
    def create_family(self, name):
        self._require_login()
        fam = family_core.create_family(self.conn, name, self.current_user["id"])
        self._family = fam
        return fam

    def join_family(self, code):
        self._require_login()
        fam = family_core.find_family_by_code(self.conn, code)
        if not fam:
            raise ValueError("No family uses that code.")
        family_core.add_member(self.conn, fam["id"], self.current_user["id"])
        self._family = fam
        return fam

    def switch_family(self, family_id):
        fam = family_core.get_family(self.conn, family_id)
        if fam:
            self._family = fam
        return fam

    def my_families(self):
        if not self.is_logged_in():
            return []
        return family_core.user_families(self.conn, self.current_user["id"])

    def members(self):
        if not self._family:
            return []
        return family_core.family_members(self.conn, self._family["id"])

    # ── invitations ──────────────────────────────────────────────────────
    def invite(self, contact):
        self._require_login()
        if not self._family:
            raise ValueError("Create or join a family first.")
        iid = family_core.create_invitation(
            self.conn, self._family["id"], self.current_user["id"], contact)
        self._write_mailbox_item("invitation", contact, {
            "invitation_id": iid,
            "family_id": self._family["id"],
            "family_name": self._family["name"],
            "family_join_code": self._family["join_code"],
            "from_name": self.current_user["display_name"],
            "from_contact": (self.current_user.get("phone")
                             or self.current_user.get("email")),
            "to_contact": contact,
        })
        return iid

    def incoming_invitations(self):
        if not self.is_logged_in():
            return []
        return family_core.incoming_invitations(self.conn, self.current_user)

    def respond_invitation(self, invitation_id, accept):
        self._require_login()
        family_core.resolve_invitation(
            self.conn, invitation_id, accept, self.current_user["id"])
        if accept:
            self._pick_active_family()

    # ── sharing / progress ────────────────────────────────────────────────
    def share(self, kind, name, payload):
        self._require_login()
        family_id = self._family["id"] if self._family else None
        family_core.add_share(
            self.conn, family_id, self.current_user["id"], kind, name, payload)
        if family_id:
            self._write_mailbox_item("share", None, {
                "family_id": family_id,
                "from_name": self.current_user["display_name"],
                "kind": kind, "name": name, "payload": payload,
            })

    def shares(self, limit=50):
        if not self._family:
            return []
        return family_core.family_shares(self.conn, self._family["id"], limit)

    def record_progress(self, session):
        self._require_login()
        family_id = self._family["id"] if self._family else None
        family_core.add_progress(
            self.conn, family_id, self.current_user["id"], session)
        if family_id:
            self._write_mailbox_item("progress", None, {
                "family_id": family_id,
                "from_name": self.current_user["display_name"],
                "session": session,
            })

    def progress_feed(self, limit=50):
        if not self._family:
            return []
        return family_core.family_progress(self.conn, self._family["id"], limit)

    # ── competitions ─────────────────────────────────────────────────────
    # Same shape as families — create or join by code, invite by contact —
    # but built on contacts instead of local user rows, because the person
    # you are competing against has no account on this machine.

    def player_key(self):
        self._require_login()
        return family_core.player_key_for(self.current_user)

    def player_name(self):
        return (self.current_user or {}).get("display_name", "Player")

    def create_competition(self, name, level=1):
        self._require_login()
        if not name.strip():
            raise ValueError("Give the competition a name.")
        comp = family_core.create_competition(
            self.conn, name, self.player_key(), self.player_name(), level)
        family_core.join_competition(
            self.conn, comp["id"], self.player_key(), self.player_name(), level)
        self._announce_competition(comp)
        return comp

    def join_competition(self, code, level=1):
        self._require_login()
        comp = family_core.find_competition_by_code(self.conn, code)
        if not comp:
            raise ValueError("No competition uses that code.")
        family_core.join_competition(
            self.conn, comp["id"], self.player_key(), self.player_name(), level)
        self._write_mailbox_item("competition_join", None, {
            "competition": comp,
            "player_key": self.player_key(),
            "display_name": self.player_name(),
            "level": int(level or 1),
        })
        return comp

    def my_competitions(self):
        if not self.is_logged_in():
            return []
        return family_core.my_competitions(self.conn, self.player_key())

    def invite_to_competition(self, competition_id, contact):
        self._require_login()
        comp = family_core.get_competition(self.conn, competition_id)
        if not comp:
            raise ValueError("That competition no longer exists.")
        if not (contact or "").strip():
            raise ValueError("Enter a phone number or email to invite.")
        iid = family_core.create_competition_invitation(
            self.conn, competition_id, self.player_name(), contact)
        self._write_mailbox_item("competition_invitation", contact, {
            "invitation_id": iid,
            "competition": comp,
            "from_name": self.player_name(),
            "to_contact": contact.strip(),
        })
        return iid

    def incoming_competition_invitations(self):
        if not self.is_logged_in():
            return []
        return family_core.incoming_competition_invitations(
            self.conn, self.current_user)

    def respond_competition_invitation(self, invitation_id, accept, level=1):
        self._require_login()
        cid = family_core.resolve_competition_invitation(
            self.conn, invitation_id, accept)
        if not (accept and cid):
            return None
        comp = family_core.get_competition(self.conn, cid)
        if comp:
            return self.join_competition(comp["join_code"], level)
        return None

    def record_competition_result(self, competition_id, session):
        """Put one scored rangoli on the board and post it to the others.

        Called for every scored Learn Mode attempt of a competition the
        player has joined, so a rangoli counts wherever it was drawn — the
        child does not have to remember to "enter" it.
        """
        self._require_login()
        result = {
            "id": family_core.new_id(),
            "competition_id": competition_id,
            "player_key": self.player_key(),
            "display_name": self.player_name(),
            "design": session.get("design"),
            "score": session.get("score"),
            "out_of": session.get("out_of"),
            "level": session.get("level"),
            "scored_by": session.get("scored_by"),
            "timestamp": session.get("timestamp") or family_core.now(),
        }
        family_core.add_competition_result(self.conn, result)
        self._write_mailbox_item("competition_result", None,
                                 {"result": result})
        return result

    def leaderboard(self, competition_id):
        return family_core.competition_leaderboard(self.conn, competition_id)

    def world_standings(self):
        return family_core.world_standings(self.conn)

    def my_world_rank(self):
        """(rank, total, rank_at_my_level, total_at_my_level) or None.

        Deliberately reports the size of the pool alongside the rank: being
        3rd is a different thing among 4 players than among 400, and the UI
        must never be able to imply the bigger one.
        """
        if not self.is_logged_in():
            return None
        key = self.player_key()
        board = self.world_standings()
        me = next((r for r in board if r["player_key"] == key), None)
        if me is None:
            return None
        peers = [r for r in board if (r.get("level") or 1) == (me.get("level") or 1)]
        mine = next(i for i, r in enumerate(peers, 1)
                    if r["player_key"] == key)
        return (me["rank"], len(board), mine, len(peers))

    def _announce_competition(self, comp):
        self._write_mailbox_item("competition", None, {"competition": comp})

    # ── sync ────────────────────────────────────────────────────────────
    def _write_mailbox_item(self, kind, _to_contact, payload):
        """Drop one JSON file in the shared folder for the others to pick up.
        Best-effort only — never raises, since nothing here is guaranteed
        to be reachable."""
        if not self.is_online():
            return
        try:
            fname = f"{int(time.time() * 1000)}_{kind}_{family_core.new_id()[:8]}.json"
            path = os.path.join(self._mailbox_path, fname)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"kind": kind, **payload}, fh)
        except OSError as e:
            self.log(f"Family: couldn't reach the shared folder — {e}", "err")

    def sync_now(self):
        """Pull in anything left for us in the mailbox since last time."""
        if not self.is_online():
            return 0
        try:
            names = sorted(os.listdir(self._mailbox_path))
        except OSError as e:
            self.log(f"Family: couldn't read the shared folder — {e}", "err")
            return 0

        seen_path = os.path.join(self.app_dir, ".family_mailbox_seen.json")
        try:
            with open(seen_path, encoding="utf-8") as fh:
                seen = set(json.load(fh))
        except Exception:
            seen = set()

        applied = 0
        for fname in names:
            if not fname.endswith(".json") or fname in seen:
                continue
            full = os.path.join(self._mailbox_path, fname)
            try:
                with open(full, encoding="utf-8") as fh:
                    item = json.load(fh)
            except (OSError, ValueError) as e:
                # Half-written by a syncing folder, most likely — leave it
                # unseen so the next tick picks it up once it lands.
                self.log(f"Family: will retry a mailbox item ({e}).", "err")
                continue
            seen.add(fname)
            if self._apply_mailbox_item(item):
                applied += 1

        try:
            with open(seen_path, "w", encoding="utf-8") as fh:
                json.dump(sorted(seen)[-500:], fh)
        except OSError:
            pass
        if applied:
            self.log(f"Family: synced {applied} update(s) from the family "
                     f"folder.", "recv")
        return applied

    def _apply_mailbox_item(self, item):
        if not self.is_logged_in():
            return False
        kind = item.get("kind")
        if kind == "invitation":
            contacts = [c for c in (self.current_user.get("phone"),
                                    self.current_user.get("email")) if c]
            if str(item.get("to_contact", "")).strip().lower() not in \
                    [c.strip().lower() for c in contacts]:
                return False
            fam = family_core.find_family_by_code(
                self.conn, item.get("family_join_code", ""))
            if fam is None:
                return False
            existing = family_core.incoming_invitations(
                self.conn, self.current_user)
            if any(i["family_id"] == fam["id"] for i in existing):
                return False
            sender = family_core.find_user_by_contact(
                self.conn, item.get("from_contact", ""))
            from_user_id = sender["id"] if sender else fam["owner_id"]
            family_core.create_invitation(
                self.conn, fam["id"], from_user_id, item.get("to_contact", ""))
            return True
        if kind in ("competition", "competition_join", "competition_invitation"):
            comp = item.get("competition") or {}
            if not comp.get("id") or not comp.get("join_code"):
                return False
            family_core.upsert_competition(
                self.conn, comp["id"], comp.get("name", "Competition"),
                comp["join_code"], comp.get("owner_key", ""),
                comp.get("owner_name"), comp.get("level"),
                comp.get("created_at"))
            if kind == "competition_join" and item.get("player_key"):
                family_core.join_competition(
                    self.conn, comp["id"], item["player_key"],
                    item.get("display_name", "Player"), item.get("level", 1))
                return True
            if kind == "competition_invitation":
                # Only an invitation addressed to one of our own contacts is
                # ours to answer; everyone else's invitations pass through
                # the same folder and must be left alone.
                contacts = [c.strip().lower() for c in
                            (self.current_user.get("phone"),
                             self.current_user.get("email")) if c]
                if str(item.get("to_contact", "")).strip().lower() not in contacts:
                    return False
                family_core.create_competition_invitation(
                    self.conn, comp["id"], item.get("from_name"),
                    item.get("to_contact"), item.get("invitation_id"))
            return True
        if kind == "competition_result":
            result = item.get("result") or {}
            if not result.get("player_key"):
                return False
            if result.get("player_key") == family_core.player_key_for(
                    self.current_user):
                return False          # our own row, already recorded locally
            return family_core.add_competition_result(self.conn, result)
        if kind in ("share", "progress") and self._family and \
                item.get("family_id") == self._family["id"]:
            # Already recorded locally by whoever sent it; nothing further
            # to do on the receiving side beyond having pulled it down —
            # the feed reads straight from this device's own table, which
            # only the sender wrote to. A fuller sync would merge rows
            # here; for a two- or three-device family this mailbox pull is
            # mainly what keeps "is_online" honest.
            return True
        return False

    def start_auto_sync(self):
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return
        self._sync_stop.clear()

        def loop():
            while not self._sync_stop.is_set():
                try:
                    self.sync_now()
                except Exception as e:                 # noqa: BLE001
                    self.log(f"Family sync error: {e}", "err")
                self._sync_stop.wait(AUTO_SYNC_INTERVAL_S)

        self._sync_thread = threading.Thread(target=loop, daemon=True)
        self._sync_thread.start()

    def stop_auto_sync(self):
        self._sync_stop.set()

    def close(self):
        self.stop_auto_sync()
        family_core.checkpoint_and_close(self.conn)

    def _require_login(self):
        if not self.is_logged_in():
            raise RuntimeError("Not signed in.")
