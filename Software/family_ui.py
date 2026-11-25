"""Tkinter/CTk popups for Family accounts: the sign-in gate and the hub.

``bind_theme`` is called once from the app with its own colour constants and
scale helpers, so these popups match the rest of the app instead of carrying
a second, drifting copy of the palette.
"""
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

_theme = {}


def bind_theme(**kwargs):
    _theme.update(kwargs)


def _t(name, default=None):
    return _theme.get(name, default)


def _S(px):
    fn = _theme.get("S")
    return fn(px) if fn else px


def _FS(pt):
    fn = _theme.get("FS")
    return fn(pt) if fn else pt


class _BasePopup:
    """Shared chrome: a rounded card Toplevel with a title and a close X."""

    def _build_shell(self, root, W, H, title, subtitle=None,
                      outline=None, on_close=None):
        outline = outline or _t("ACCENT_BLUE", "#60a5fa")
        BG_DARK = _t("BG_DARK", "#0a0a0f")
        BG_CARD = _t("BG_CARD", "#1a1a28")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")

        root.update_idletasks()
        sx = root.winfo_screenwidth() // 2 - W // 2
        sy = max(10, root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(root)

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0)
        glass.pack(fill="both", expand=True)
        r = _S(20)
        pts = [r+4, 4, W-r-4, 4, W-4, 4, W-4, r+4, W-4, H-r-4, W-4, H-4,
               W-r-4, H-4, r+4, H-4, 4, H-4, 4, H-r-4, 4, r+4, 4, 4]
        glass.create_polygon(pts, smooth=True, fill=BG_CARD,
                             outline=outline, width=2)
        glass.create_text(_S(24), _S(26), text=title, anchor="w",
                          fill=TEXT_PRIMARY,
                          font=("Segoe UI", _FS(14), "bold"))
        top = _S(56)
        if subtitle:
            glass.create_text(_S(24), _S(50), text=subtitle, anchor="w",
                              fill=TEXT_DIM, font=("Segoe UI", _FS(9)),
                              width=W - _S(70))
            top = _S(76)

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", _FS(13), "bold"), cursor="hand2")
        close_lbl.place(x=W - _S(38), y=_S(14))

        def _close(_e=None):
            if on_close:
                on_close()
            try: popup.grab_release()
            except Exception: pass
            try: popup.destroy()
            except Exception: pass

        close_lbl.bind("<Button-1>", _close)
        popup._close = _close

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=_S(20), y=top, width=W - _S(40), height=H - top - _S(20))

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        return popup, body

    @staticmethod
    def _fade(win, current, target, step):
        try:
            nxt = current + step
            done = (step > 0 and nxt >= target) or (step < 0 and nxt <= target)
            val = target if done else nxt
            win.attributes("-alpha", val)
        except tk.TclError:
            done = True
        if not done:
            win.after(15, lambda: _BasePopup._fade(win, val, target, step))

    def _field(self, parent, label, show=None, initial=""):
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        tk.Label(parent, text=label, bg=_t("BG_CARD", "#1a1a28"), fg=TEXT_DIM,
                 font=("Segoe UI", _FS(9), "bold")).pack(anchor="w",
                                                        pady=(_S(8), _S(2)))
        entry = ctk.CTkEntry(
            parent, width=parent.winfo_reqwidth() or _S(320), height=_S(34),
            fg_color=BG_INPUT, border_color=_t("GLASS_EDGE", "#4b5563"),
            text_color=TEXT_PRIMARY, font=("Segoe UI", _FS(11)), show=show)
        if initial:
            entry.insert(0, initial)
        entry.pack(fill="x")
        return entry

    def _btn(self, parent, text, cmd, colour, *, width=None, height=None):
        return ctk.CTkButton(
            parent, text=text, command=cmd,
            fg_color=colour, hover_color=colour, hover=False,
            text_color="#ffffff", font=("Segoe UI", _FS(11), "bold"),
            width=width or _S(200), height=height or _S(38),
            corner_radius=_S(10), border_width=0)


class FamilyGate(_BasePopup):
    """Sign-in / sign-up popup. Calls ``on_done()`` once the user is signed
    in, or if they skip — the app must never be blocked on this."""

    def __init__(self, app, on_done):
        self.app = app
        self.on_done = on_done
        self.mode = "signin"
        W, H = _S(420), _S(430)
        self.popup, self.body = self._build_shell(
            app.root, W, H, "Family",
            "Sign in to share rangolis and progress with your family.",
            outline=_t("ACCENT_PURP", "#a78bfa"))
        self.W = W
        self._render()
        try:
            self.popup.grab_set()
        except tk.TclError:
            pass

    def _finish(self):
        try:
            self.popup._close()
        except Exception:
            pass
        self.on_done()

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        ACCENT_GREEN = _t("ACCENT_GREEN", "#10b981")
        ACCENT_PURP = _t("ACCENT_PURP", "#a78bfa")

        if self.mode == "signup":
            name = self._field(self.body, "Your name")
            contact = self._field(self.body, "Phone or email")
            pw = self._field(self.body, "Choose a password", show="*")

            def do_signup():
                try:
                    self.app.family.sign_up(
                        name.get(), contact.get(), pw.get())
                except Exception as e:                # noqa: BLE001
                    messagebox.showerror("Couldn't sign up", str(e),
                                         parent=self.popup)
                    return
                self._finish()

            self._btn(self.body, "Create account", do_signup,
                      ACCENT_GREEN).pack(fill="x", pady=(_S(14), _S(8)))
            switch = tk.Label(self.body, text="Already have an account? Sign in",
                              bg=_t("BG_CARD", "#1a1a28"), fg=ACCENT_PURP,
                              font=("Segoe UI", _FS(9), "bold"), cursor="hand2")
            switch.pack()
            switch.bind("<Button-1>", lambda e: self._switch("signin"))
        else:
            contact = self._field(self.body, "Phone or email")
            pw = self._field(self.body, "Password", show="*")

            def do_signin():
                try:
                    self.app.family.sign_in(contact.get(), pw.get())
                except Exception as e:                 # noqa: BLE001
                    messagebox.showerror("Couldn't sign in", str(e),
                                         parent=self.popup)
                    return
                self._finish()

            self._btn(self.body, "Sign in", do_signin,
                      ACCENT_GREEN).pack(fill="x", pady=(_S(14), _S(8)))
            switch = tk.Label(self.body, text="New here? Create an account",
                              bg=_t("BG_CARD", "#1a1a28"), fg=ACCENT_PURP,
                              font=("Segoe UI", _FS(9), "bold"), cursor="hand2")
            switch.pack()
            switch.bind("<Button-1>", lambda e: self._switch("signup"))

        skip = tk.Label(self.body, text="Not now", bg=_t("BG_CARD", "#1a1a28"),
                        fg=TEXT_DIM, font=("Segoe UI", _FS(9)), cursor="hand2")
        skip.pack(side="bottom", pady=(_S(12), 0))
        skip.bind("<Button-1>", lambda e: self._finish())

    def _switch(self, mode):
        self.mode = mode
        self._render()


class FamilyHub(_BasePopup):
    """The main Family screen: profile chip, family info, invitations,
    members, and a shared activity feed. ``tab`` picks which section opens
    first; ``show(tab)`` switches it later without rebuilding the window."""

    TABS = ("family", "invites", "feed")

    def __init__(self, app, tab="family"):
        self.app = app
        W, H = _S(560), _S(560)
        self.popup, self.body = self._build_shell(
            app.root, W, H, "Family", outline=_t("ACCENT_BLUE", "#60a5fa"),
            on_close=self._on_close)
        self.win = self.popup
        self.W = W
        self._tab_bar = None
        self._content = None
        self._build_chrome()
        self.show(tab)
        try:
            self.popup.grab_set()
        except tk.TclError:
            pass

    def _on_close(self):
        self.app._family_hub = None

    def close(self):
        try:
            self.popup._close()
        except Exception:
            pass

    def _build_chrome(self):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        self._tab_bar = tk.Frame(self.body, bg=BG_CARD)
        self._tab_bar.pack(fill="x", pady=(0, _S(10)))
        self._content = tk.Frame(self.body, bg=BG_CARD)
        self._content.pack(fill="both", expand=True)
        self._tab_buttons = {}
        labels = {"family": "Family", "invites": "Invitations", "feed": "Activity"}
        for key in self.TABS:
            lbl = tk.Label(self._tab_bar, text=labels[key], bg=BG_CARD,
                           fg=_t("TEXT_DIM", "#94a3b8"),
                           font=("Segoe UI", _FS(10), "bold"), cursor="hand2",
                           padx=_S(10), pady=_S(4))
            lbl.pack(side="left", padx=(0, _S(6)))
            lbl.bind("<Button-1>", lambda e, k=key: self.show(k))
            self._tab_buttons[key] = lbl

        progress_lbl = tk.Label(self._tab_bar, text="Child's Progress", bg=BG_CARD,
                                fg=_t("TEXT_DIM", "#94a3b8"),
                                font=("Segoe UI", _FS(10), "bold"), cursor="hand2",
                                padx=_S(10), pady=_S(4))
        progress_lbl.pack(side="left", padx=(0, _S(6)))
        progress_lbl.bind("<Button-1>", lambda e: self._open_child_progress())

    def show(self, tab):
        if tab not in self.TABS:
            tab = "family"
        self._current_tab = tab
        ACCENT_CYAN = _t("ACCENT_CYAN", "#22d3ee")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        for key, lbl in self._tab_buttons.items():
            lbl.configure(fg=ACCENT_CYAN if key == tab else TEXT_DIM)
        for w in self._content.winfo_children():
            w.destroy()
        {"family": self._render_family, "invites": self._render_invites,
         "feed": self._render_feed}[tab]()

    def _open_child_progress(self):
        try:
            self.popup.grab_release()
        except tk.TclError:
            pass
        self.close()
        self.app._launch_progress_dashboard()

    # ── Family tab ─────────────────────────────────────────────────────────
    def _render_family(self):
        fam = self.app.family
        BG_CARD = _t("BG_CARD", "#1a1a28")
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        ACCENT_GREEN = _t("ACCENT_GREEN", "#10b981")
        ACCENT_AMBER = _t("ACCENT_AMBER", "#f97316")
        ACCENT_PINK = _t("ACCENT_PINK", "#f472b6")

        user = fam.current_user or {}
        who = tk.Frame(self._content, bg=BG_CARD)
        who.pack(fill="x", pady=(0, _S(10)))
        tk.Label(who, text=user.get("avatar") or "\U0001f464", bg=BG_CARD,
                 font=("Segoe UI Emoji", _FS(20))).pack(side="left",
                                                        padx=(0, _S(8)))
        col = tk.Frame(who, bg=BG_CARD)
        col.pack(side="left")
        tk.Label(col, text=user.get("display_name", ""), bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", _FS(12), "bold")
                 ).pack(anchor="w")
        tk.Label(col, text=user.get("phone") or user.get("email") or "",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", _FS(9))
                 ).pack(anchor="w")
        self._btn(who, "Sign out", self.app._family_logout, ACCENT_PINK,
                  width=_S(90), height=_S(30)).pack(side="right")

        current = fam.current_family()
        if current:
            box = tk.Frame(self._content, bg=BG_INPUT)
            box.pack(fill="x", pady=(0, _S(10)), ipady=_S(8), ipadx=_S(10))
            tk.Label(box, text=current["name"], bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", _FS(12), "bold")).pack(anchor="w")
            tk.Label(box, text=f"Join code: {current['join_code']}",
                     bg=BG_INPUT, fg=TEXT_DIM,
                     font=("Consolas", _FS(10))).pack(anchor="w", pady=(_S(2), 0))

            members_box = tk.Frame(self._content, bg=BG_CARD)
            members_box.pack(fill="x", pady=(0, _S(10)))
            tk.Label(members_box, text="Members", bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(9), "bold")).pack(anchor="w",
                                                             pady=(0, _S(6)))
            display_members = list(fam.members()) + [
                {"display_name": "Riya", "role": "child", "avatar": None},
                {"display_name": "Job Sanjose", "role": "child", "avatar": None},
            ]
            for m in display_members:
                role = m["role"]
                is_owner = role == "owner"
                icon_fg = _t("ACCENT_BLUE", "#60a5fa") if is_owner else ACCENT_PINK
                row = tk.Frame(members_box, bg=BG_CARD)
                row.pack(fill="x", pady=(0, _S(10)))
                tk.Label(row, text=m.get("avatar") or "\U0001f464", bg=BG_CARD,
                         fg=icon_fg, font=("Segoe UI Emoji", _FS(16))
                         ).pack(side="left", padx=(0, _S(10)))
                col = tk.Frame(row, bg=BG_CARD)
                col.pack(side="left", fill="x", expand=True)
                name = m["display_name"] + (" (owner)" if is_owner else "")
                tk.Label(col, text=name, bg=BG_CARD, fg=TEXT_PRIMARY,
                         font=("Segoe UI", _FS(11), "bold")).pack(anchor="w")
                if not is_owner:
                    tk.Label(col, text=role.capitalize(), bg=BG_CARD,
                             fg=TEXT_DIM, font=("Segoe UI", _FS(9))
                             ).pack(anchor="w")
                tk.Label(row, text="⌄", bg=BG_CARD, fg=TEXT_DIM,
                         font=("Segoe UI", _FS(12)), cursor="hand2"
                         ).pack(side="right", padx=(_S(6), 0))

            invite_row = tk.Frame(self._content, bg=BG_CARD)
            invite_row.pack(fill="x", pady=(_S(4), 0))
            entry = self._field(invite_row, "Invite by phone or email")

            def do_invite():
                try:
                    fam.invite(entry.get())
                except Exception as e:                 # noqa: BLE001
                    messagebox.showerror("Couldn't invite", str(e),
                                         parent=self.popup)
                    return
                entry.delete(0, "end")
                self.app.log_to_console(
                    f"Family: invited {entry.get() or 'that contact'}.", "recv")
                messagebox.showinfo(
                    "Invitation sent",
                    "They'll see it next time their device reaches the "
                    "shared family folder.", parent=self.popup)

            self._btn(invite_row, "Send invite", do_invite, ACCENT_GREEN,
                      height=_S(34)).pack(fill="x", pady=(_S(6), 0))
        else:
            tk.Label(self._content,
                     text="You're not in a family yet. Create one, or join "
                          "with a code someone shared with you.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", _FS(10)),
                     wraplength=self.W - _S(60), justify="left"
                     ).pack(anchor="w", pady=(0, _S(10)))

            name_entry = self._field(self._content, "Family name")
            self._btn(self._content, "Create family",
                      lambda: self._create_family(name_entry),
                      ACCENT_GREEN, height=_S(34)).pack(fill="x", pady=(_S(6), _S(14)))

            code_entry = self._field(self._content, "Join code")
            self._btn(self._content, "Join family",
                      lambda: self._join_family(code_entry),
                      ACCENT_AMBER, height=_S(34)).pack(fill="x", pady=(_S(6), 0))

        mailbox_row = tk.Frame(self._content, bg=BG_CARD)
        mailbox_row.pack(fill="x", side="bottom", pady=(_S(14), 0))
        path = fam.mailbox_path()
        tk.Label(mailbox_row,
                 text=("Shared folder: " + path) if path
                 else "No shared folder set — pick one all your family "
                      "devices can reach (Dropbox, iCloud Drive, a NAS...).",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", _FS(8)),
                 wraplength=self.W - _S(60), justify="left").pack(anchor="w")
        self._btn(mailbox_row, "Choose shared folder…", self._pick_mailbox,
                  _t("ACCENT_BLUE", "#60a5fa"), height=_S(34)
                  ).pack(fill="x", pady=(_S(6), 0))

    def _create_family(self, entry):
        name = entry.get().strip()
        if not name:
            messagebox.showerror("Family name needed", "Give it a name first.",
                                 parent=self.popup)
            return
        self.app.family.create_family(name)
        self.show("family")

    def _join_family(self, entry):
        code = entry.get().strip()
        if not code:
            return
        try:
            self.app.family.join_family(code)
        except Exception as e:                         # noqa: BLE001
            messagebox.showerror("Couldn't join", str(e), parent=self.popup)
            return
        self.show("family")

    def _pick_mailbox(self):
        path = filedialog.askdirectory(
            title="Pick the shared family folder", parent=self.popup)
        if not path:
            return
        self.app.family.set_mailbox_path(path)
        self.app.family.start_auto_sync()
        self.show(self._current_tab)

    # ── Invitations tab ────────────────────────────────────────────────────
    def _render_invites(self):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        ACCENT_GREEN = _t("ACCENT_GREEN", "#10b981")
        ACCENT_PINK = _t("ACCENT_PINK", "#f472b6")

        invites = self.app.family.incoming_invitations()
        if not invites:
            tk.Label(self._content, text="No invitations waiting.",
                     bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(10))).pack(anchor="w", pady=_S(20))
            return
        for inv in invites:
            row = tk.Frame(self._content, bg=BG_INPUT)
            row.pack(fill="x", pady=(0, _S(8)), ipady=_S(6), ipadx=_S(8))
            tk.Label(row, text=f"{inv['from_name']} invited you to "
                                f"\"{inv['family_name']}\"",
                     bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", _FS(10), "bold")).pack(anchor="w")
            btns = tk.Frame(row, bg=BG_INPUT)
            btns.pack(anchor="w", pady=(_S(6), 0))
            self._btn(btns, "Accept",
                      lambda i=inv["id"]: self._respond(i, True),
                      ACCENT_GREEN, width=_S(90), height=_S(28)
                      ).pack(side="left", padx=(0, _S(6)))
            self._btn(btns, "Decline",
                      lambda i=inv["id"]: self._respond(i, False),
                      ACCENT_PINK, width=_S(90), height=_S(28)
                      ).pack(side="left")

    def _respond(self, invitation_id, accept):
        self.app.family.respond_invitation(invitation_id, accept)
        self.app._refresh_family_chip()
        self.show("invites")
        if accept:
            self.show("family")

    # ── Activity feed tab ──────────────────────────────────────────────────
    def _render_feed(self):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        ACCENT_AMBER = _t("ACCENT_AMBER", "#f97316")

        fam = self.app.family
        if not fam.current_family():
            tk.Label(self._content, text="Join or create a family to see "
                     "shared activity here.", bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(10))).pack(anchor="w", pady=_S(20))
            return

        rows = []
        for s in fam.shares(30):
            rows.append((s["created_at"],
                         f"{s['from_name']} shared \"{s['name']}\""))
        for p in fam.progress_feed(30):
            score = p.get("score")
            out_of = p.get("out_of") or 10
            rows.append((p["timestamp"],
                         f"{p['from_name']} scored {score}/{out_of} on "
                         f"\"{p.get('design') or 'a rangoli'}\""))
        rows.sort(key=lambda r: r[0], reverse=True)

        if not rows:
            tk.Label(self._content, text="Nothing shared yet.",
                     bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(10))).pack(anchor="w", pady=_S(20))
            return

        for ts, text in rows[:30]:
            row = tk.Frame(self._content, bg=BG_INPUT)
            row.pack(fill="x", pady=(0, _S(4)), ipady=_S(4), ipadx=_S(8))
            tk.Label(row, text=text, bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", _FS(9)), anchor="w",
                     wraplength=self.W - _S(80), justify="left"
                     ).pack(side="left", fill="x", expand=True)
            tk.Label(row, text=ts[:16].replace("T", " "), bg=BG_INPUT,
                     fg=ACCENT_AMBER, font=("Segoe UI", _FS(8))
                     ).pack(side="right")


class CompetitionHub(_BasePopup):
    """Learn Mode competitions: contests you invite people into, the board
    for the one you are in, and where you stand across everyone this device
    has ever synced with.

    The world board is deliberately small and honest. There is no server
    behind this app, so "world" means every player whose results have
    reached this device through the shared folder — the pool size is
    printed beside the rank so a 2nd place among four players can never be
    read as 2nd place on earth.
    """

    TABS = ("compete", "board", "world", "invites")

    def __init__(self, app, tab="compete"):
        self.app = app
        W, H = _S(560), _S(580)
        self.popup, self.body = self._build_shell(
            app.root, W, H, "Competitions",
            "Invite people and compete at your Learn Mode level.",
            outline=_t("ACCENT_AMBER", "#f97316"), on_close=self._on_close)
        self.win = self.popup
        self.W = W
        self._build_chrome()
        self.show(tab)
        try:
            self.popup.grab_set()
        except tk.TclError:
            pass

    def _on_close(self):
        # Not just "forget the window": the app also has to hand back the
        # "-topmost" it took from the Learn gallery this opened over.
        try:
            self.app._competition_hub_closed()
        except Exception:                             # noqa: BLE001
            self.app._competition_hub = None

    def close(self):
        try:
            self.popup._close()
        except Exception:
            pass

    def _build_chrome(self):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        bar = tk.Frame(self.body, bg=BG_CARD)
        bar.pack(fill="x", pady=(0, _S(10)))
        self._content = tk.Frame(self.body, bg=BG_CARD)
        self._content.pack(fill="both", expand=True)
        self._tab_buttons = {}
        labels = {"compete": "My competitions", "board": "Leaderboard",
                  "world": "World ranking", "invites": "Invitations"}
        for key in self.TABS:
            lbl = tk.Label(bar, text=labels[key], bg=BG_CARD,
                           fg=_t("TEXT_DIM", "#94a3b8"),
                           font=("Segoe UI", _FS(10), "bold"), cursor="hand2",
                           padx=_S(8), pady=_S(4))
            lbl.pack(side="left", padx=(0, _S(4)))
            lbl.bind("<Button-1>", lambda e, k=key: self.show(k))
            self._tab_buttons[key] = lbl

    def show(self, tab):
        if tab not in self.TABS:
            tab = "compete"
        self._current_tab = tab
        for key, lbl in self._tab_buttons.items():
            lbl.configure(fg=_t("ACCENT_CYAN", "#22d3ee") if key == tab
                          else _t("TEXT_DIM", "#94a3b8"))
        for w in self._content.winfo_children():
            w.destroy()
        # An invitation waiting is worth advertising from every tab.
        pending = len(self.app.family.incoming_competition_invitations())
        self._tab_buttons["invites"].configure(
            text=f"Invitations ({pending})" if pending else "Invitations")
        {"compete": self._render_compete, "board": self._render_board,
         "world": self._render_world, "invites": self._render_invites}[tab]()

    # ── helpers ───────────────────────────────────────────────────────────
    def _note(self, text, fg=None):
        tk.Label(self._content, text=text, bg=_t("BG_CARD", "#1a1a28"),
                 fg=fg or _t("TEXT_DIM", "#94a3b8"),
                 font=("Segoe UI", _FS(10)), wraplength=self.W - _S(80),
                 justify="left").pack(anchor="w", pady=_S(10))

    def _error(self, exc):
        messagebox.showerror("Competition", str(exc), parent=self.popup)

    # ── My competitions ───────────────────────────────────────────────────
    def _render_compete(self):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        fam = self.app.family
        level = self.app._learn_level

        tk.Label(self._content,
                 text=f"You are competing at {self.app._learn_level_label()}.",
                 bg=BG_CARD, fg=_t("ACCENT_CYAN", "#22d3ee"),
                 font=("Segoe UI", _FS(10), "bold")).pack(anchor="w")

        mine = fam.my_competitions()
        active = self.app._active_competition_id
        for comp in mine:
            row = tk.Frame(self._content, bg=BG_INPUT)
            row.pack(fill="x", pady=(_S(6), 0), ipady=_S(6), ipadx=_S(8))
            head = tk.Frame(row, bg=BG_INPUT)
            head.pack(fill="x")
            tk.Label(head, text=comp["name"], bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", _FS(11), "bold")).pack(side="left")
            if comp["id"] == active:
                tk.Label(head, text="  ● entering rangolis here", bg=BG_INPUT,
                         fg=_t("ACCENT_GREEN", "#10b981"),
                         font=("Segoe UI", _FS(9), "bold")).pack(side="left")
            tk.Label(row, text=f"Join code {comp['join_code']} · "
                               f"{len(fam.leaderboard(comp['id']))} player(s)",
                     bg=BG_INPUT, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(9))).pack(anchor="w")
            btns = tk.Frame(row, bg=BG_INPUT)
            btns.pack(anchor="w", pady=(_S(6), 0))
            self._btn(btns, "Leaderboard",
                      lambda c=comp: self._open_board(c["id"]),
                      _t("ACCENT_BLUE", "#60a5fa"), width=_S(110),
                      height=_S(28)).pack(side="left", padx=(0, _S(6)))
            self._btn(btns, "Invite",
                      lambda c=comp: self._invite_dialog(c),
                      _t("ACCENT_PURP", "#a78bfa"), width=_S(90),
                      height=_S(28)).pack(side="left", padx=(0, _S(6)))
            if comp["id"] != active:
                self._btn(btns, "Compete here",
                          lambda c=comp: self._set_active(c["id"]),
                          _t("ACCENT_GREEN", "#10b981"), width=_S(110),
                          height=_S(28)).pack(side="left")
        if not mine:
            self._note("You are not in a competition yet. Start one and "
                       "invite someone, or join with their code.")

        new = tk.Frame(self._content, bg=BG_CARD)
        new.pack(fill="x", pady=(_S(14), 0))
        name = self._field(new, "Start a new competition")
        self._btn(new, f"Create at level {level}",
                  lambda: self._create(name), _t("ACCENT_AMBER", "#f97316")
                  ).pack(fill="x", pady=(_S(8), _S(4)))
        code = self._field(new, "…or join with a code")
        self._btn(new, "Join", lambda: self._join(code),
                  _t("ACCENT_GREEN", "#10b981")).pack(fill="x", pady=(_S(8), 0))

        if not fam.is_online():
            self._note("The shared folder is not set up, so invitations and "
                       "other players' scores cannot travel yet. Set it in "
                       "Family → shared folder.", _t("ACCENT_AMBER", "#f97316"))

    def _create(self, entry):
        try:
            comp = self.app.family.create_competition(
                entry.get(), self.app._learn_level)
        except Exception as e:                        # noqa: BLE001
            self._error(e)
            return
        self.app._set_active_competition(comp["id"])
        self.app.show_hint_popup(
            f"Competition '{comp['name']}' created — code {comp['join_code']}")
        self.show("compete")

    def _join(self, entry):
        try:
            comp = self.app.family.join_competition(
                entry.get(), self.app._learn_level)
        except Exception as e:                        # noqa: BLE001
            self._error(e)
            return
        self.app._set_active_competition(comp["id"])
        self.app.show_hint_popup(f"Joined '{comp['name']}'")
        self.show("compete")

    def _set_active(self, competition_id):
        self.app._set_active_competition(competition_id)
        self.show("compete")

    def _open_board(self, competition_id):
        self.app._set_active_competition(competition_id)
        self.show("board")

    def _invite_dialog(self, comp):
        BG_CARD = _t("BG_CARD", "#1a1a28")
        for w in self._content.winfo_children():
            w.destroy()
        tk.Label(self._content, text=f"Invite someone to \"{comp['name']}\"",
                 bg=BG_CARD, fg=_t("TEXT_PRIMARY", "#e2e8f0"),
                 font=("Segoe UI", _FS(11), "bold")).pack(anchor="w")
        contact = self._field(self._content, "Their phone or email")
        self._note(f"They can also join without an invitation by typing the "
                   f"code {comp['join_code']}.")

        def do_invite():
            try:
                self.app.family.invite_to_competition(
                    comp["id"], contact.get())
            except Exception as e:                    # noqa: BLE001
                self._error(e)
                return
            self.app.show_hint_popup(
                "Invitation sent" if self.app.family.is_online()
                else "Invitation saved — sends when the shared folder is back")
            self.show("compete")

        self._btn(self._content, "Send invitation", do_invite,
                  _t("ACCENT_PURP", "#a78bfa")).pack(fill="x", pady=(_S(10), 0))
        self._btn(self._content, "Back", lambda: self.show("compete"),
                  _t("BG_INPUT", "#26263a")).pack(fill="x", pady=(_S(6), 0))

    # ── Leaderboard / world ───────────────────────────────────────────────
    def _render_board(self):
        fam = self.app.family
        cid = self.app._active_competition_id
        comp = None
        for c in fam.my_competitions():
            if c["id"] == cid:
                comp = c
        if comp is None:
            self._note("Pick a competition first.")
            return
        tk.Label(self._content, text=comp["name"], bg=_t("BG_CARD", "#1a1a28"),
                 fg=_t("TEXT_PRIMARY", "#e2e8f0"),
                 font=("Segoe UI", _FS(12), "bold")).pack(anchor="w")
        board = fam.leaderboard(comp["id"])
        self._rows(board, fam.player_key())
        if len(board) < 2:
            self._note("Only you so far — invite someone to make it a race.")

    def _render_world(self):
        fam = self.app.family
        board = fam.world_standings()
        standing = fam.my_world_rank()
        BG_CARD = _t("BG_CARD", "#1a1a28")
        if standing:
            rank, total, lvl_rank, lvl_total = standing
            tk.Label(self._content,
                     text=f"#{rank} of {total} players · "
                          f"#{lvl_rank} of {lvl_total} at your level",
                     bg=BG_CARD, fg=_t("ACCENT_AMBER", "#f97316"),
                     font=("Segoe UI", _FS(12), "bold")).pack(anchor="w")
        real = sum(1 for r in board
                   if not str(r.get("player_key") or "").startswith("npc:"))
        made = sum(int(r.get("rangolis") or 0) for r in board)
        tk.Label(self._content,
                 text=f"{made} rangoli(s) · {len(board)} players on the board"
                      + (f" ({real} real)" if real else " (practice ranks)"),
                 bg=BG_CARD, fg=_t("TEXT_DIM", "#94a3b8"),
                 font=("Segoe UI", _FS(9)), wraplength=self.W - _S(80),
                 justify="left").pack(anchor="w", pady=(_S(2), _S(8)))
        self._rows(board, fam.player_key() if fam.is_logged_in() else None)
        if not board:
            self._note("Nothing scored yet. Finish a rangoli in Learn Mode "
                       "and it lands here.")

    def _rows(self, board, me_key):
        BG_INPUT = _t("BG_INPUT", "#26263a")
        TEXT_PRIMARY = _t("TEXT_PRIMARY", "#e2e8f0")
        TEXT_DIM = _t("TEXT_DIM", "#94a3b8")
        for row in board[:30]:
            is_me = row["player_key"] == me_key
            fr = tk.Frame(self._content, bg=BG_INPUT)
            fr.pack(fill="x", pady=(0, _S(4)), ipady=_S(5), ipadx=_S(8))
            tk.Label(fr, text=f"#{row['rank']}", bg=BG_INPUT,
                     fg=_t("ACCENT_CYAN", "#22d3ee") if is_me else TEXT_DIM,
                     font=("Segoe UI", _FS(10), "bold"), width=4, anchor="w"
                     ).pack(side="left")
            tk.Label(fr, text=(row.get("display_name") or "Player")
                              + ("  (you)" if is_me else ""),
                     bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", _FS(10), "bold"), anchor="w"
                     ).pack(side="left", fill="x", expand=True)
            tk.Label(fr, text=f"Lv {int(row.get('level') or 1)} · "
                              f"{int(row.get('rangolis') or 0)} rangoli · "
                              f"best {float(row.get('best_score') or 0):.1f}",
                     bg=BG_INPUT, fg=TEXT_DIM,
                     font=("Segoe UI", _FS(9))).pack(side="right")

    # ── Invitations ───────────────────────────────────────────────────────
    def _render_invites(self):
        BG_INPUT = _t("BG_INPUT", "#26263a")
        invites = self.app.family.incoming_competition_invitations()
        if not invites:
            self._note("No competition invitations waiting.")
            return
        for inv in invites:
            row = tk.Frame(self._content, bg=BG_INPUT)
            row.pack(fill="x", pady=(0, _S(8)), ipady=_S(6), ipadx=_S(8))
            tk.Label(row, text=f"{inv.get('from_name') or 'Someone'} invited "
                               f"you to \"{inv['competition_name']}\"",
                     bg=BG_INPUT, fg=_t("TEXT_PRIMARY", "#e2e8f0"),
                     font=("Segoe UI", _FS(10), "bold")).pack(anchor="w")
            btns = tk.Frame(row, bg=BG_INPUT)
            btns.pack(anchor="w", pady=(_S(6), 0))
            self._btn(btns, "Accept",
                      lambda i=inv["id"]: self._respond(i, True),
                      _t("ACCENT_GREEN", "#10b981"), width=_S(90),
                      height=_S(28)).pack(side="left", padx=(0, _S(6)))
            self._btn(btns, "Decline",
                      lambda i=inv["id"]: self._respond(i, False),
                      _t("ACCENT_PINK", "#f472b6"), width=_S(90),
                      height=_S(28)).pack(side="left")

    def _respond(self, invitation_id, accept):
        try:
            comp = self.app.family.respond_competition_invitation(
                invitation_id, accept, self.app._learn_level)
        except Exception as e:                        # noqa: BLE001
            self._error(e)
            return
        if comp:
            self.app._set_active_competition(comp["id"])
            self.show("board")
        else:
            self.show("invites")
