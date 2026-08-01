"""Login / signup gate and the Family hub, drawn in Rangoli-Bot's own style.

Kept out of App_v16.py deliberately. The app file is already ~15k lines and
one class; adding five screens to it would bury the robot code that actually
has to be read at a judging table. The app imports this, and if the import
fails the app runs exactly as it did before -- family features simply are
not there. Nothing here is allowed to be load-bearing for drawing rangolis.

Two entry points:

    FamilyGate(app, on_done)   fullscreen sign-in shown at launch
    FamilyHub(app)             the family screen (members, invites, chat,
                               shared notebook pages, progress)

Everything on screen reads from the LOCAL database, always. That is what
makes the UI identical online and off: no screen waits on a network call to
render, and no button is disabled because the wifi is down. The connection
pill in the corner is the only thing that changes.
"""

import threading
import tkinter as tk

import customtkinter as ctk

import family_core as core

# Filled in by bind_theme() before any screen is built. These live in
# App_v16's module namespace and depend on UI_SCALE, which is only known
# once the app has measured the screen -- importing them at module load
# would freeze them at the wrong values.
S = FS = None
BG_DARK = BG_PANEL = BG_CARD = BG_INPUT = GLASS_EDGE = ""
ACCENT_BLUE = ACCENT_CYAN = ACCENT_GREEN = ACCENT_AMBER = ""
ACCENT_PINK = ACCENT_PURP = TEXT_PRIMARY = TEXT_DIM = ""

AVATARS = ["\U0001f469", "\U0001f468", "\U0001f475", "\U0001f474",
           "\U0001f467", "\U0001f466", "\U0001f338", "\U0001f31f",
           "\U0001f42f", "\U0001f984"]


def bind_theme(**names):
    """Hand this module the app's scaling helpers and palette."""
    globals().update(names)


# ── small shared widgets ──────────────────────────────────────────────────

def _ui_call(app, fn):
    """Run `fn` on the Tk thread from a worker thread, safely.

    Two ways this is called after the window is gone -- a probe thread
    finishing after the app quit (RuntimeError: main thread is not in main
    loop) and a widget destroyed mid-flight (TclError). Neither is worth a
    traceback in front of a judge.
    """
    try:
        app.root.after(0, fn)
    except (tk.TclError, RuntimeError):
        pass


def _label(parent, text, size=11, colour=None, bold=False, bg=None, **kw):
    return tk.Label(parent, text=text, bg=bg or BG_CARD,
                    fg=colour or TEXT_PRIMARY,
                    font=("Segoe UI", FS(size), "bold" if bold else "normal"),
                    **kw)


def _entry(parent, placeholder="", show=None, width=None):
    return ctk.CTkEntry(
        parent, placeholder_text=placeholder, show=show,
        width=width or S(300), height=S(38), corner_radius=S(9),
        fg_color=BG_INPUT, border_color=GLASS_EDGE, border_width=1,
        text_color=TEXT_PRIMARY, placeholder_text_color=TEXT_DIM,
        font=("Segoe UI", FS(12)))


def _button(app, parent, text, cmd, colour, width=None, height=None, size=12):
    return app._color_button(parent, text, cmd, colour,
                             width=width or S(140), height=height or S(38),
                             font_size=FS(size), corner_radius=S(10))


def _when(iso):
    """'2026-07-31T14:38:17Z' -> '31 Jul, 14:38'. Never raises on junk."""
    if not iso or len(iso) < 16:
        return ""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        return "%d %s, %s" % (int(iso[8:10]), months[int(iso[5:7]) - 1],
                              iso[11:16])
    except (ValueError, IndexError):
        return iso[:16].replace("T", " ")


# ── the sign-in gate ──────────────────────────────────────────────────────

class FamilyGate:
    """Fullscreen login / signup, shown once at launch.

    Runs before the design chooser so whatever the child draws is attributed
    to somebody from the first stroke. Signing in is required: there is no
    way past this screen except a valid account, so nothing the app records
    is ever anonymous. The only other button ends the app.
    """

    def __init__(self, app, on_done):
        self.app = app
        self.fam = app.family
        self.on_done = on_done
        self.mode = "login"          # or "signup"
        self.avatar = AVATARS[0]
        self._busy = False

        root = app.root
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG_DARK)
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        self.win.geometry("%dx%d+0+0" % (w, h))
        try:
            self.win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.win.transient(root)

        self.body = tk.Frame(self.win, bg=BG_DARK)
        self.body.place(relx=0.5, rely=0.5, anchor="center")
        self._build()
        app._fade(self.win, 0.0, 1.0, 0.10)
        self.win.lift()
        self.win.focus_force()

    # ── layout ────────────────────────────────────────────────────────────

    def _build(self):
        for child in self.body.winfo_children():
            child.destroy()

        W = S(430)
        card = tk.Frame(self.body, bg=BG_DARK)
        card.pack()

        head = tk.Frame(card, bg=BG_DARK)
        head.pack(pady=(0, S(14)))
        icon = tk.Canvas(head, width=S(52), height=S(52), bg=BG_DARK,
                         highlightthickness=0)
        icon.pack()
        self.app._draw_flower_icon(icon, S(26), S(26), S(21))
        _label(head, "Rangoli Bot", size=20, bold=True, bg=BG_DARK).pack(
            pady=(S(6), 0))
        _label(head, self.app.tr("Sign in so your rangolis are saved to you"),
               size=10, colour=TEXT_DIM, bg=BG_DARK).pack(pady=(S(2), 0))

        shell = tk.Frame(card, bg=BG_CARD, highlightbackground=GLASS_EDGE,
                         highlightthickness=1)
        shell.pack()
        inner = tk.Frame(shell, bg=BG_CARD)
        inner.pack(padx=S(26), pady=S(22))

        # Log in / Create account switch
        tabs = tk.Frame(inner, bg=BG_CARD)
        tabs.pack(fill="x", pady=(0, S(16)))
        for key, text in (("login", "Log in"), ("signup", "Create account")):
            active = self.mode == key
            b = self.app._color_button(
                tabs, self.app.tr(text), lambda k=key: self._switch(k),
                ACCENT_PURP if active else BG_INPUT,
                width=(W - S(52)) // 2 - S(4), height=S(34),
                font_size=FS(11), corner_radius=S(9),
                text_color="#ffffff" if active else TEXT_DIM)
            b.pack(side="left", padx=(0, S(6)) if key == "login" else 0)

        if self.mode == "signup":
            _label(inner, self.app.tr("Your name"), size=10,
                   colour=TEXT_DIM).pack(anchor="w")
            self.e_name = _entry(inner, "Ananya", width=W - S(52))
            self.e_name.pack(pady=(S(3), S(12)))

        _label(inner, self.app.tr("Phone number or email"), size=10,
               colour=TEXT_DIM).pack(anchor="w")
        self.e_contact = _entry(inner, "+91 98765 43210", width=W - S(52))
        self.e_contact.pack(pady=(S(3), S(12)))

        _label(inner, self.app.tr("Password"), size=10,
               colour=TEXT_DIM).pack(anchor="w")
        self.e_pass = _entry(inner, "at least 6 characters", show="•",
                             width=W - S(52))
        self.e_pass.pack(pady=(S(3), S(12)))

        if self.mode == "signup":
            _label(inner, self.app.tr("Pick a picture"), size=10,
                   colour=TEXT_DIM).pack(anchor="w", pady=(0, S(4)))
            row = tk.Frame(inner, bg=BG_CARD)
            row.pack(anchor="w", pady=(0, S(12)))
            self._avatar_btns = {}
            for a in AVATARS:
                lbl = tk.Label(row, text=a, bg=BG_INPUT, cursor="hand2",
                               font=("Segoe UI Emoji", FS(15)),
                               padx=S(4), pady=S(2))
                lbl.pack(side="left", padx=S(2))
                lbl.bind("<Button-1>", lambda e, v=a: self._pick_avatar(v))
                self._avatar_btns[a] = lbl
            self._paint_avatars()

            _label(inner, self.app.tr(
                "Family name (optional — leave blank if you were invited)"),
                size=10, colour=TEXT_DIM).pack(anchor="w")
            self.e_family = _entry(inner, "Swamy Family", width=W - S(52))
            self.e_family.pack(pady=(S(3), S(12)))

        self.msg = _label(inner, "", size=10, colour=ACCENT_PINK,
                          wraplength=W - S(52), justify="left")
        self.msg.pack(anchor="w", pady=(0, S(8)))

        go_text = "Log in" if self.mode == "login" else "Create account"
        self.go_btn = self.app._color_button(
            inner, self.app.tr(go_text), self._submit, ACCENT_GREEN,
            width=W - S(52), height=S(42), font_size=FS(13),
            corner_radius=S(10))
        self.go_btn.pack()

        self.e_pass.bind("<Return>", lambda e: self._submit())
        self.e_contact.bind("<Return>", lambda e: self._submit())

        # connection strip
        foot = tk.Frame(card, bg=BG_DARK)
        foot.pack(fill="x", pady=(S(14), 0))
        self.conn_lbl = _label(foot, "", size=9, colour=TEXT_DIM, bg=BG_DARK)
        self.conn_lbl.pack(side="left")
        tk.Label(foot, text=self.app.tr("Server settings"), bg=BG_DARK,
                 fg=ACCENT_BLUE, cursor="hand2",
                 font=("Segoe UI", FS(9), "underline")).pack(side="right")
        foot.winfo_children()[-1].bind("<Button-1>",
                                       lambda e: self._server_dialog())

        # Signing in is required — there is no "continue without an account"
        # path. Quit is offered instead so the gate is never a fullscreen
        # window with no way out; it ends the app rather than bypassing it.
        quit_lbl = tk.Label(card, text=self.app.tr("Quit"),
                            bg=BG_DARK, fg=TEXT_DIM, cursor="hand2",
                            font=("Segoe UI", FS(9), "underline"))
        quit_lbl.pack(pady=(S(10), 0))
        quit_lbl.bind("<Button-1>", lambda e: self._quit_app())

        self._refresh_conn()

    def _switch(self, mode):
        self.mode = mode
        self._build()

    def _pick_avatar(self, value):
        self.avatar = value
        self._paint_avatars()

    def _paint_avatars(self):
        for a, lbl in self._avatar_btns.items():
            lbl.configure(bg=ACCENT_PURP if a == self.avatar else BG_INPUT)

    def _refresh_conn(self):
        """Probe reachability off-thread; the label is the only thing it moves."""
        def probe():
            online = self.fam.is_online(force=True)
            url = self.fam.server_url or self.app.tr("not set")
            text = ("● %s  ·  %s" %
                    (self.app.tr("Online") if online else self.app.tr("Offline"),
                     url))
            colour = ACCENT_GREEN if online else TEXT_DIM

            def paint():
                try:
                    if self.conn_lbl.winfo_exists():
                        self.conn_lbl.configure(text=text, fg=colour)
                except tk.TclError:
                    pass
            _ui_call(self.app, paint)
        threading.Thread(target=probe, daemon=True).start()

    def _server_dialog(self):
        SimpleServerDialog(self.app, self.fam, on_saved=self._refresh_conn)

    # ── actions ───────────────────────────────────────────────────────────

    def _say(self, text, colour=None):
        # colour resolves HERE, not in the signature: bind_theme() fills the
        # palette in after this module is imported, so a default baked into
        # the signature would freeze at the empty string and every call
        # would raise TclError on an unknown colour name.
        try:
            self.msg.configure(text=text, fg=colour or ACCENT_PINK)
        except tk.TclError:
            pass

    def _submit(self):
        """Validate here, then do the slow part off the UI thread.

        PBKDF2 is deliberately expensive and a login runs it twice on a bad
        password (once for the decoy). On the UI thread that is a visible
        freeze, which reads as a crash to a child.
        """
        if self._busy:
            return
        contact = self.e_contact.get().strip()
        password = self.e_pass.get()
        name = self.e_name.get().strip() if self.mode == "signup" else ""
        family = self.e_family.get().strip() if self.mode == "signup" else ""

        try:
            core.normalise_contact(contact)
        except core.FamilyError as e:
            return self._say(e.message)
        if len(password) < 6:
            return self._say(self.app.tr(
                "Password must be at least 6 characters."))
        if self.mode == "signup" and not name:
            return self._say(self.app.tr("Enter your name."))

        self._busy = True
        self.go_btn.configure(text=self.app.tr("Please wait…"),
                              state="disabled")
        self._say(self.app.tr("Checking…"), TEXT_DIM)

        def work():
            try:
                if self.mode == "login":
                    self.fam.login(contact, password)
                else:
                    self.fam.signup(contact, password, name,
                                    role="adult", avatar=self.avatar,
                                    family_name=family)
                _ui_call(self.app, self._succeed)
            except core.FamilyError as e:
                _ui_call(self.app, lambda: self._fail(e.message))
            except Exception as e:                # noqa: BLE001
                _ui_call(self.app,
                         lambda: self._fail("%s: %s" % (type(e).__name__, e)))

        threading.Thread(target=work, daemon=True).start()

    def _fail(self, message):
        self._busy = False
        try:
            self.go_btn.configure(
                text=self.app.tr("Log in" if self.mode == "login"
                                 else "Create account"), state="normal")
        except tk.TclError:
            pass
        self._say(message)

    def _succeed(self):
        self._busy = False
        user = self.fam.current_user or {}
        self.app.log_to_console(
            "Signed in as %s (%s)."
            % (user.get("display_name", "?"), self.fam.status_line()), "info")
        self.fam.start_auto_sync()
        self._close()

    def _quit_app(self):
        """Close the app from the gate.

        Not a way past the sign-in — it ends the program. Without it the
        gate is a fullscreen, undecorated window with no exit, so a
        forgotten password would leave the machine needing Task Manager.
        """
        fam = getattr(self.app, "family", None)
        if fam is not None:
            try:
                fam.close()          # checkpoints the WAL before we go
            except Exception:        # noqa: BLE001
                pass
        self.app._window_close()

    def _close(self):
        def gone():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
            if self.on_done:
                self.on_done()
        self.app._fade(self.win, 1.0, 0.0, 0.12, on_done=gone)


class ConfirmDialog:
    """Small yes/no card. Used before anything that ends a session."""

    def __init__(self, app, title, message, confirm_text, on_confirm,
                 confirm_colour=None, warning=None):
        self.app = app
        self.on_confirm = on_confirm
        W = S(400)
        H = S(230) if warning else S(196)
        root = app.root
        win = tk.Toplevel(root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG_CARD)
        win.geometry("%dx%d+%d+%d" % (
            W, H, root.winfo_screenwidth() // 2 - W // 2,
            root.winfo_screenheight() // 2 - H // 2))

        wrap = tk.Frame(win, bg=BG_CARD, highlightbackground=ACCENT_PURP,
                        highlightthickness=2)
        wrap.pack(fill="both", expand=True)
        inner = tk.Frame(wrap, bg=BG_CARD)
        inner.pack(padx=S(22), pady=S(20), fill="both", expand=True)

        _label(inner, app.tr(title), size=14, bold=True).pack(anchor="w")
        _label(inner, app.tr(message), size=10, colour=TEXT_DIM,
               wraplength=W - S(48), justify="left").pack(
            anchor="w", pady=(S(6), 0))
        if warning:
            _label(inner, warning, size=10, colour=ACCENT_AMBER,
                   wraplength=W - S(48), justify="left").pack(
                anchor="w", pady=(S(8), 0))

        row = tk.Frame(inner, bg=BG_CARD)
        row.pack(side="bottom", fill="x", pady=(S(14), 0))
        _button(app, row, app.tr("Cancel"), self.close, BG_INPUT,
                width=S(150)).pack(side="left")
        _button(app, row, app.tr(confirm_text), self._go,
                confirm_colour or ACCENT_PINK, width=S(150)).pack(side="right")
        win.grab_set()
        win.focus_force()
        win.bind("<Escape>", lambda e: self.close())

    def _go(self):
        self.close()
        self.on_confirm()

    def close(self):
        try:
            self.win.grab_release()
            self.win.destroy()
        except tk.TclError:
            pass


class SimpleServerDialog:
    """Where the online database lives. Blank = this laptop only, forever."""

    def __init__(self, app, fam, on_saved=None):
        self.app, self.fam, self.on_saved = app, fam, on_saved
        W, H = S(430), S(250)
        root = app.root
        win = tk.Toplevel(root)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG_CARD)
        win.geometry("%dx%d+%d+%d" % (
            W, H, root.winfo_screenwidth() // 2 - W // 2,
            root.winfo_screenheight() // 2 - H // 2))

        wrap = tk.Frame(win, bg=BG_CARD, highlightbackground=ACCENT_PURP,
                        highlightthickness=2)
        wrap.pack(fill="both", expand=True)
        inner = tk.Frame(wrap, bg=BG_CARD)
        inner.pack(padx=S(22), pady=S(20), fill="both", expand=True)

        _label(inner, app.tr("Family server"), size=14, bold=True).pack(anchor="w")
        _label(inner, app.tr(
            "The address of the shared family database. Leave it blank to "
            "keep everything on this laptop only. Everything works offline "
            "either way — this only decides who else can see it."),
            size=9, colour=TEXT_DIM, wraplength=W - S(48),
            justify="left").pack(anchor="w", pady=(S(4), S(12)))

        self.entry = _entry(inner, "http://192.168.1.7:8765", width=W - S(48))
        self.entry.pack()
        if fam.server_url:
            self.entry.insert(0, fam.server_url)

        self.note = _label(inner, "", size=9, colour=TEXT_DIM)
        self.note.pack(anchor="w", pady=(S(8), 0))

        row = tk.Frame(inner, bg=BG_CARD)
        row.pack(side="bottom", fill="x", pady=(S(12), 0))
        _button(app, row, app.tr("Cancel"), self._close, BG_INPUT,
                width=S(110)).pack(side="left")
        _button(app, row, app.tr("Test"), self._test, ACCENT_BLUE,
                width=S(110)).pack(side="left", padx=S(8))
        _button(app, row, app.tr("Save"), self._save, ACCENT_GREEN,
                width=S(110)).pack(side="right")
        win.grab_set()
        win.focus_force()

    def _test(self):
        url = self.entry.get().strip()
        self.note.configure(text=self.app.tr("Testing…"), fg=TEXT_DIM)

        def work():
            old = self.fam.server_url
            self.fam.server_url = url.rstrip("/")
            ok = self.fam.is_online(force=True)
            self.fam.server_url = old

            def paint():
                try:
                    if self.note.winfo_exists():
                        self.note.configure(
                            text=self.app.tr("Server answered — looks good.")
                            if ok else
                            self.app.tr("No answer from that address."),
                            fg=ACCENT_GREEN if ok else ACCENT_PINK)
                except tk.TclError:
                    pass
            _ui_call(self.app, paint)
        threading.Thread(target=work, daemon=True).start()

    def _save(self):
        self.fam.set_server_url(self.entry.get().strip())
        self._close()
        if self.on_saved:
            self.on_saved()

    def _close(self):
        try:
            self.win.grab_release()
            self.win.destroy()
        except tk.TclError:
            pass


# ── the family hub ────────────────────────────────────────────────────────

class FamilyHub:
    """Members, invitations, chat, shared notebook pages and progress."""

    TABS = [("family", "\U0001f465", "Family"),
            ("invites", "✉", "Invitations"),
            ("chat", "\U0001f4ac", "Chat"),
            ("shared", "\U0001f4d6", "Shared"),
            ("progress", "\U0001f4c8", "Progress")]

    def __init__(self, app, tab="family"):
        self.app = app
        self.fam = app.family
        self.tab = tab
        self._chat_seen = None

        root = app.root
        W = min(S(940), root.winfo_screenwidth() - S(60))
        H = min(S(660), root.winfo_screenheight() - S(60))
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG_CARD)
        self.win.geometry("%dx%d+%d+%d" % (
            W, H, root.winfo_screenwidth() // 2 - W // 2,
            max(S(20), root.winfo_screenheight() // 2 - H // 2)))
        try:
            self.win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.W, self.H = W, H

        wrap = tk.Frame(self.win, bg=BG_CARD, highlightbackground=ACCENT_PURP,
                        highlightthickness=2)
        wrap.pack(fill="both", expand=True)

        self._build_header(wrap)
        mid = tk.Frame(wrap, bg=BG_CARD)
        mid.pack(fill="both", expand=True)
        self._build_nav(mid)
        self.content = tk.Frame(mid, bg=BG_PANEL)
        self.content.pack(side="left", fill="both", expand=True,
                          padx=(0, S(2)), pady=(0, S(2)))

        # A sync landing in the background should refresh whatever is open --
        # that is what makes the chat feel live rather than manual.
        self.fam.add_listener(self._on_sync)
        self._alive = True
        self.render()
        app._fade(self.win, 0.0, 0.98, 0.10)
        self.win.lift()
        self.win.focus_force()
        self._tick()

    # ── chrome ────────────────────────────────────────────────────────────

    def _build_header(self, parent):
        bar = tk.Frame(parent, bg=BG_CARD, height=S(54))
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.header = bar
        self._fill_header(bar)

    def _fill_header(self, bar):
        user = self.fam.current_user or {}
        left = tk.Frame(bar, bg=BG_CARD)
        left.place(x=S(18), rely=0.5, anchor="w")
        tk.Label(left, text=user.get("avatar") or "\U0001f464", bg=BG_CARD,
                 font=("Segoe UI Emoji", FS(17))).pack(side="left",
                                                       padx=(0, S(8)))
        col = tk.Frame(left, bg=BG_CARD)
        col.pack(side="left")
        _label(col, user.get("display_name", "—"), size=13,
               bold=True).pack(anchor="w")
        _label(col, self.fam.family_name() or self.app.tr("No family yet"),
               size=9, colour=TEXT_DIM).pack(anchor="w")

        right = tk.Frame(bar, bg=BG_CARD)
        right.place(relx=1.0, x=-S(16), rely=0.5, anchor="e")
        close = tk.Label(right, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                         cursor="hand2", font=("Segoe UI", FS(13), "bold"))
        close.pack(side="right", padx=(S(10), 0))
        close.bind("<Button-1>", lambda e: self.close())
        self.status_lbl = _label(right, "", size=9, colour=TEXT_DIM)
        self.status_lbl.pack(side="right", padx=(S(12), 0))

        # Sign out sits next to whose account this is, which is the only
        # place someone looks when they want to hand the laptop over.
        signout = ctk.CTkButton(
            right, text=self.app.tr("Sign out"), command=self._confirm_logout,
            width=S(84), height=S(28), corner_radius=S(8),
            fg_color="transparent", hover_color=BG_INPUT, border_width=1,
            border_color=GLASS_EDGE, text_color=TEXT_DIM,
            font=("Segoe UI", FS(10), "bold"))
        signout.pack(side="right")

    def _confirm_logout(self):
        """Confirm first, and say plainly what happens to unsent work."""
        waiting = self.fam.pending_count()
        warning = None
        if waiting:
            warning = self.app.tr(
                "%d change(s) have not reached the family server yet. They "
                "stay saved on this laptop and are sent the next time you "
                "sign in here — nothing is lost.") % waiting
        ConfirmDialog(
            self.app,
            title="Sign out?",
            message=self.app.tr(
                "You will need your phone/email and password to get back in. "
                "Your rangolis, notebook pages and messages stay on this "
                "laptop."),
            confirm_text="Sign out",
            confirm_colour=ACCENT_PINK,
            warning=warning,
            on_confirm=self.app._family_logout)

    def _build_nav(self, parent):
        nav = tk.Frame(parent, bg=BG_CARD, width=S(168))
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        self._nav_btns = {}
        for key, icon, text in self.TABS:
            b = tk.Label(nav, text="  %s   %s" % (icon, self.app.tr(text)),
                         bg=BG_CARD, fg=TEXT_DIM, anchor="w", cursor="hand2",
                         font=("Segoe UI", FS(11)), padx=S(10), pady=S(9))
            b.pack(fill="x", padx=S(10), pady=S(1))
            b.bind("<Button-1>", lambda e, k=key: self.show(k))
            self._nav_btns[key] = b

        note = _label(nav, "", size=8, colour=TEXT_DIM, wraplength=S(140),
                      justify="left")
        note.pack(side="bottom", anchor="w", padx=S(16), pady=S(12))
        self._nav_note = note
        self._paint_nav()

    def _paint_nav(self):
        pend = len(self.fam.incoming_invitations())
        for key, icon, text in self.TABS:
            b = self._nav_btns[key]
            label = "  %s   %s" % (icon, self.app.tr(text))
            if key == "invites" and pend:
                label += "  (%d)" % pend
            active = key == self.tab
            b.configure(text=label,
                        bg=ACCENT_PURP if active else BG_CARD,
                        fg="#ffffff" if active else
                        (ACCENT_AMBER if key == "invites" and pend else TEXT_DIM),
                        font=("Segoe UI", FS(11), "bold" if active else "normal"))

    def show(self, tab):
        self.tab = tab
        self.render()

    def render(self):
        if not self._alive:
            return
        self._paint_nav()
        try:
            self.status_lbl.configure(text=self.fam.status_line())
        except tk.TclError:
            return
        for child in self.content.winfo_children():
            child.destroy()
        {"family": self._tab_family, "invites": self._tab_invites,
         "chat": self._tab_chat, "shared": self._tab_shared,
         "progress": self._tab_progress}[self.tab]()

    def _scroll_area(self):
        f = ctk.CTkScrollableFrame(self.content, fg_color=BG_PANEL,
                                   scrollbar_button_color=GLASS_EDGE)
        f.pack(fill="both", expand=True, padx=S(14), pady=S(12))
        return f

    def _title(self, parent, text, sub=None):
        _label(parent, self.app.tr(text), size=15, bold=True,
               bg=BG_PANEL).pack(anchor="w")
        if sub:
            _label(parent, self.app.tr(sub), size=9, colour=TEXT_DIM,
                   bg=BG_PANEL, wraplength=self.W - S(230),
                   justify="left").pack(anchor="w", pady=(S(2), S(10)))

    def _empty(self, parent, text):
        _label(parent, self.app.tr(text), size=10, colour=TEXT_DIM,
               bg=BG_PANEL, wraplength=self.W - S(230),
               justify="left").pack(anchor="w", pady=S(16))

    # ── tab: family ───────────────────────────────────────────────────────

    def _tab_family(self):
        head = tk.Frame(self.content, bg=BG_PANEL)
        head.pack(fill="x", padx=S(14), pady=(S(12), 0))
        self._title(head, "Your family",
                    "Invite someone with their phone number or email. They get "
                    "an Accept or Reject card in their own app — no codes, no "
                    "OTP to type in.")

        row = tk.Frame(head, bg=BG_PANEL)
        row.pack(fill="x", pady=(0, S(6)))
        self.e_invite = _entry(row, "+91 98765 43210  or  nani@gmail.com",
                               width=S(320))
        self.e_invite.pack(side="left")
        _button(self.app, row, self.app.tr("Send invitation"), self._do_invite,
                ACCENT_GREEN, width=S(150)).pack(side="left", padx=S(8))
        self.invite_msg = _label(head, "", size=9, colour=TEXT_DIM, bg=BG_PANEL,
                                 wraplength=self.W - S(230), justify="left")
        self.invite_msg.pack(anchor="w", pady=(0, S(6)))
        self.e_invite.bind("<Return>", lambda e: self._do_invite())

        area = self._scroll_area()
        members = self.fam.members()
        if not members:
            self._empty(area, "You have not started a family yet. Send an "
                              "invitation above and one is created for you.")
        for m in members:
            self._member_card(area, m)

        sent = [i for i in self.fam.sent_invitations() if i["status"] == "pending"]
        if sent:
            _label(area, self.app.tr("Waiting for a reply"), size=11, bold=True,
                   bg=BG_PANEL).pack(anchor="w", pady=(S(14), S(6)))
            for inv in sent:
                self._pending_sent_card(area, inv)

    def _member_card(self, parent, m):
        me = (self.fam.current_user or {}).get("user_id") == m["user_id"]
        card = tk.Frame(parent, bg=BG_CARD)
        card.pack(fill="x", pady=S(4))
        inner = tk.Frame(card, bg=BG_CARD)
        inner.pack(fill="x", padx=S(14), pady=S(10))
        tk.Label(inner, text=m.get("avatar") or "\U0001f464", bg=BG_CARD,
                 font=("Segoe UI Emoji", FS(16))).pack(side="left",
                                                       padx=(0, S(10)))
        col = tk.Frame(inner, bg=BG_CARD)
        col.pack(side="left", fill="x", expand=True)
        name = m["display_name"] + (self.app.tr("  (you)") if me else "")
        _label(col, name, size=12, bold=True).pack(anchor="w")
        _label(col, m.get("email") or m.get("phone") or "", size=9,
               colour=TEXT_DIM).pack(anchor="w")
        if m.get("member_role") == "owner":
            _label(inner, self.app.tr("started it"), size=9,
                   colour=ACCENT_AMBER).pack(side="right")

    def _pending_sent_card(self, parent, inv):
        card = tk.Frame(parent, bg=BG_CARD)
        card.pack(fill="x", pady=S(3))
        inner = tk.Frame(card, bg=BG_CARD)
        inner.pack(fill="x", padx=S(14), pady=S(8))
        col = tk.Frame(inner, bg=BG_CARD)
        col.pack(side="left", fill="x", expand=True)
        _label(col, inv["to_contact"], size=11, bold=True).pack(anchor="w")
        _label(col, self.app.tr("invited %s") % _when(inv["created_at"]),
               size=9, colour=TEXT_DIM).pack(anchor="w")
        _button(self.app, inner, self.app.tr("Cancel"),
                lambda i=inv["invite_id"]: self._do_cancel(i), BG_INPUT,
                width=S(90), height=S(30), size=10).pack(side="right")

    def _do_invite(self):
        contact = self.e_invite.get().strip()
        try:
            inv = self.fam.invite(contact)
        except core.FamilyError as e:
            return self.invite_msg.configure(text=e.message, fg=ACCENT_PINK)
        note = self.app.tr(
            "Invitation sent to %s. It appears in their app as soon as they "
            "sign in.") % inv["to_contact"]
        if not self.fam.is_online():
            note = self.app.tr(
                "Invitation to %s saved. It goes out the moment this laptop "
                "is back online.") % inv["to_contact"]
        self.render()
        try:
            self.invite_msg.configure(text=note, fg=ACCENT_GREEN)
        except tk.TclError:
            pass

    def _do_cancel(self, invite_id):
        try:
            self.fam.cancel_invitation(invite_id)
        except core.FamilyError as e:
            self.app.log_to_console(e.message, "err")
        self.render()

    # ── tab: invitations ──────────────────────────────────────────────────

    def _tab_invites(self):
        head = tk.Frame(self.content, bg=BG_PANEL)
        head.pack(fill="x", padx=S(14), pady=(S(12), 0))
        self._title(head, "Invitations for you",
                    "Someone has asked you to join their family. Accepting "
                    "shares your rangolis, notebook pages and progress with "
                    "everyone in it.")
        area = self._scroll_area()
        invites = self.fam.incoming_invitations()
        if not invites:
            return self._empty(area, "Nothing waiting for you right now.")
        for inv in invites:
            self._invite_card(area, inv)

    def _invite_card(self, parent, inv):
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=ACCENT_AMBER,
                        highlightthickness=1)
        card.pack(fill="x", pady=S(5))
        inner = tk.Frame(card, bg=BG_CARD)
        inner.pack(fill="x", padx=S(16), pady=S(12))

        tk.Label(inner, text=inv.get("from_avatar") or "\U0001f4e8", bg=BG_CARD,
                 font=("Segoe UI Emoji", FS(20))).pack(side="left",
                                                       padx=(0, S(12)))
        col = tk.Frame(inner, bg=BG_CARD)
        col.pack(side="left", fill="x", expand=True)
        _label(col, self.app.tr("%s invited you to %s")
               % (inv.get("from_name") or self.app.tr("Someone"),
                  inv.get("family_name") or self.app.tr("their family")),
               size=12, bold=True).pack(anchor="w")
        _label(col, _when(inv["created_at"]), size=9,
               colour=TEXT_DIM).pack(anchor="w")

        btns = tk.Frame(inner, bg=BG_CARD)
        btns.pack(side="right")
        _button(self.app, btns, self.app.tr("Reject"),
                lambda i=inv["invite_id"]: self._respond(i, False), BG_INPUT,
                width=S(94), height=S(34), size=10).pack(side="left",
                                                         padx=(0, S(8)))
        _button(self.app, btns, self.app.tr("Accept"),
                lambda i=inv["invite_id"]: self._respond(i, True), ACCENT_GREEN,
                width=S(94), height=S(34), size=10).pack(side="left")

    def _respond(self, invite_id, accept):
        try:
            inv = self.fam.respond_invitation(invite_id, accept)
        except core.FamilyError as e:
            self.app.log_to_console(e.message, "err")
            return self.render()
        if accept:
            self.app.log_to_console(
                "Joined the family '%s'." % self.fam.family_name(), "info")
            self.app._kid_celebrate(self.app.tr("You joined the family!"))
            self.tab = "family"
        # The header carries the family name, which has just changed, so it
        # is rebuilt rather than only re-rendering the tab body. The banner
        # chip shows the same name, so it is repainted too instead of
        # waiting out its refresh timer.
        self._refresh_header()
        self.render()
        self.app._refresh_family_chip()

    def _refresh_header(self):
        try:
            for child in self.header.winfo_children():
                child.destroy()
            self._fill_header(self.header)
        except tk.TclError:
            pass

    # ── tab: chat ─────────────────────────────────────────────────────────

    def _tab_chat(self):
        if not self.fam.current_family_id:
            head = tk.Frame(self.content, bg=BG_PANEL)
            head.pack(fill="x", padx=S(14), pady=(S(12), 0))
            self._title(head, "Family chat")
            return self._empty(head, "Join or start a family first — the "
                                     "Family tab has an invitation box.")

        head = tk.Frame(self.content, bg=BG_PANEL)
        head.pack(fill="x", padx=S(14), pady=(S(12), S(4)))
        _label(head, self.app.tr("Family chat"), size=15, bold=True,
               bg=BG_PANEL).pack(side="left")
        _label(head, self.app.tr("messages you send with no internet are "
                                 "delivered when it comes back"),
               size=9, colour=TEXT_DIM, bg=BG_PANEL).pack(side="left",
                                                          padx=S(10))

        self.chat_area = ctk.CTkScrollableFrame(
            self.content, fg_color=BG_PANEL, scrollbar_button_color=GLASS_EDGE)
        self.chat_area.pack(fill="both", expand=True, padx=S(14), pady=(0, S(6)))

        row = tk.Frame(self.content, bg=BG_PANEL)
        row.pack(fill="x", padx=S(14), pady=(0, S(12)))
        self.e_chat = _entry(row, self.app.tr("Write a message…"),
                             width=self.W - S(340))
        self.e_chat.pack(side="left", fill="x", expand=True)
        _button(self.app, row, self.app.tr("Send"), self._do_send,
                ACCENT_BLUE, width=S(96)).pack(side="left", padx=S(8))
        self.e_chat.bind("<Return>", lambda e: self._do_send())
        self.e_chat.focus_set()

        self._paint_chat()

    def _paint_chat(self):
        msgs = self.fam.messages(limit=120)
        for child in self.chat_area.winfo_children():
            child.destroy()
        if not msgs:
            return _label(self.chat_area, self.app.tr(
                "No messages yet. Say hello!"), size=10, colour=TEXT_DIM,
                bg=BG_PANEL).pack(anchor="w", pady=S(10))

        me = (self.fam.current_user or {}).get("user_id")
        for m in msgs:
            mine = m["from_user_id"] == me
            line = tk.Frame(self.chat_area, bg=BG_PANEL)
            line.pack(fill="x", pady=S(3))
            bubble = tk.Frame(line, bg=ACCENT_PURP if mine else BG_CARD)
            bubble.pack(side="right" if mine else "left", padx=S(4))
            inner = tk.Frame(bubble, bg=bubble["bg"])
            inner.pack(padx=S(11), pady=S(7))
            if not mine:
                _label(inner, "%s %s" % (m.get("from_avatar") or "",
                                         m.get("from_name") or "—"),
                       size=9, colour=ACCENT_CYAN, bg=bubble["bg"]).pack(
                    anchor="w")
            _label(inner, m["body"], size=11,
                   colour="#ffffff" if mine else TEXT_PRIMARY,
                   bg=bubble["bg"], wraplength=self.W - S(420),
                   justify="left").pack(anchor="w")
            _label(inner, _when(m["created_at"]), size=8,
                   colour="#e9d5ff" if mine else TEXT_DIM,
                   bg=bubble["bg"]).pack(anchor="e")
        self._chat_seen = msgs[-1]["created_at"]
        self.chat_area.update_idletasks()
        try:
            self.chat_area._parent_canvas.yview_moveto(1.0)
        except Exception:                          # noqa: BLE001
            pass

    def _do_send(self):
        text = self.e_chat.get().strip()
        if not text:
            return
        try:
            self.fam.send_message(text)
        except core.FamilyError as e:
            return self.app.log_to_console(e.message, "err")
        self.e_chat.delete(0, "end")
        self._paint_chat()

    # ── tab: shared ───────────────────────────────────────────────────────

    def _tab_shared(self):
        head = tk.Frame(self.content, bg=BG_PANEL)
        head.pack(fill="x", padx=S(14), pady=(S(12), 0))
        self._title(head, "Shared with the family",
                    "Kolam notebook pages, designs and photos that anyone in "
                    "the family has shared.")
        area = self._scroll_area()
        shares = self.fam.shares()
        if not shares:
            return self._empty(area, "Nothing shared yet. Open a notebook "
                                     "page and use Share with family.")
        icons = {"notebook_page": "\U0001f4d6", "design": "❈",
                 "photo": "\U0001f4f7"}
        for sh in shares:
            card = tk.Frame(area, bg=BG_CARD)
            card.pack(fill="x", pady=S(4))
            inner = tk.Frame(card, bg=BG_CARD)
            inner.pack(fill="x", padx=S(14), pady=S(10))
            tk.Label(inner, text=icons.get(sh["kind"], "\U0001f4c4"),
                     bg=BG_CARD, font=("Segoe UI Emoji", FS(15))).pack(
                side="left", padx=(0, S(10)))
            col = tk.Frame(inner, bg=BG_CARD)
            col.pack(side="left", fill="x", expand=True)
            _label(col, sh.get("title") or self.app.tr("Untitled"), size=11,
                   bold=True).pack(anchor="w")
            _label(col, self.app.tr("from %s · %s")
                   % (sh.get("from_name") or "—", _when(sh["created_at"])),
                   size=9, colour=TEXT_DIM).pack(anchor="w")
            if sh["kind"] == "notebook_page":
                _button(self.app, inner, self.app.tr("Place on canvas"),
                        lambda s=sh: self._place_share(s), ACCENT_BLUE,
                        width=S(130), height=S(30), size=10).pack(side="right")

    def _place_share(self, share):
        """Drop a shared notebook page onto the canvas as a normal design."""
        handler = getattr(self.app, "_place_shared_payload", None)
        if handler is None:
            return self.app.log_to_console(
                "This build cannot place shared pages yet.", "err")
        try:
            handler(share)
            self.close()
        except Exception as e:                     # noqa: BLE001
            self.app.log_to_console("Could not place that page: %s" % e, "err")

    # ── tab: progress ─────────────────────────────────────────────────────

    def _tab_progress(self):
        head = tk.Frame(self.content, bg=BG_PANEL)
        head.pack(fill="x", padx=S(14), pady=(S(12), 0))
        self._title(head, "Progress",
                    "Every scored Learn Mode session in the family. The label "
                    "on each row says how it was scored — that stays attached "
                    "wherever the score travels.")
        area = self._scroll_area()
        rows = self.fam.family_progress()
        if not rows:
            return self._empty(area, "No scored sessions yet.")

        # Honesty badge. A sample or AI-fallback number must never be shown
        # as a measured one, including to a relative reading it on another
        # device, so the provenance is rendered on the row itself.
        badges = {"human": (self.app.tr("measured"), ACCENT_GREEN),
                  "ai": (self.app.tr("AI estimate"), ACCENT_AMBER),
                  "sample": (self.app.tr("sample data"), TEXT_DIM)}
        for r in reversed(rows):
            card = tk.Frame(area, bg=BG_CARD)
            card.pack(fill="x", pady=S(3))
            inner = tk.Frame(card, bg=BG_CARD)
            inner.pack(fill="x", padx=S(14), pady=S(9))
            col = tk.Frame(inner, bg=BG_CARD)
            col.pack(side="left", fill="x", expand=True)
            _label(col, "%s — %s" % (r.get("learner_name") or "—",
                                     r.get("design") or "—"),
                   size=11, bold=True).pack(anchor="w")
            _label(col, "%s · %s · %s"
                   % (self.app.tr("Level %d") % (r.get("level") or 1),
                      r.get("complexity") or "—", _when(r["timestamp"])),
                   size=9, colour=TEXT_DIM).pack(anchor="w")

            right = tk.Frame(inner, bg=BG_CARD)
            right.pack(side="right")
            score = r.get("score")
            _label(right, "%s/%s" % ("—" if score is None else
                                     ("%g" % score),
                                     "%g" % (r.get("out_of") or 10)),
                   size=14, bold=True, colour=ACCENT_CYAN).pack(anchor="e")
            text, colour = badges.get(r.get("scored_by"), badges["sample"])
            _label(right, text, size=8, colour=colour).pack(anchor="e")

    # ── lifecycle ─────────────────────────────────────────────────────────

    def _on_sync(self, what):
        """Called from the sync thread -- bounce onto the UI thread."""
        if not self._alive:
            return
        _ui_call(self.app, self._refresh_if_open)

    def _refresh_if_open(self):
        if not self._alive:
            return
        if self.tab == "chat" and getattr(self, "chat_area", None):
            try:
                self._paint_chat()
                self.status_lbl.configure(text=self.fam.status_line())
                self._paint_nav()
                return
            except tk.TclError:
                return
        self.render()

    def _tick(self):
        """Keep the connection pill honest even when nothing else moves."""
        if not self._alive:
            return
        try:
            self.status_lbl.configure(text=self.fam.status_line())
            self._paint_nav()
        except tk.TclError:
            return
        self.win.after(4000, self._tick)

    def close(self):
        self._alive = False
        try:
            self.fam._listeners.remove(self._on_sync)
        except ValueError:
            pass
        app = self.app
        if getattr(app, "_family_hub", None) is self:
            app._family_hub = None

        def gone():
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        app._fade(self.win, 0.98, 0.0, 0.12, on_done=gone)
