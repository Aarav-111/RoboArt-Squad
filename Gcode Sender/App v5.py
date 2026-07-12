import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import serial
import serial.tools.list_ports
import math
import os
import sys
import time
import threading
import json
import random
import urllib.request
import urllib.error

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

AI_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "openai_key.txt")

# Pool of prompt styles for the "AI Generated" rangoli image. One is picked
# at random every time the button is clicked, so consecutive designs vary.
# Target complexity: a clean centre circle + one ring of 6-9 petals (like a
# classic lotus/sunflower mandala), optionally a small ring of accent points
# just outside the petals. Thin, well-separated single-stroke outlines - not
# solid/filled shapes - and the whole motif should be large and centred,
# filling most of the frame.
RANGOLI_IMAGE_PROMPTS = [
    # Prompt 1 - Classic Lotus Flower
    "Generate a completely original rangoli in the style of a classic "
    "lotus/sunflower mandala: a small centre circle surrounded by ONE ring "
    "of 6-9 evenly spaced petals. Optionally add a ring of small pointed "
    "accents or dots just outside the petal tips. No second petal layer, "
    "no outer border. Thin, clean single-stroke black outlines with clear "
    "gaps between each petal - not thick or filled. Preserve perfect "
    "radial symmetry. The whole motif should be large and centred, filling "
    "most of the frame. Black outlines only on a white background. No "
    "fills, colors, shading, or 3D.",

    # Prompt 2 - Geometric Ring
    "Design a unique geometric rangoli using ONE simple shape repeated "
    "symmetrically (pick one: petals, a star, a hexagon, or diamonds) - "
    "6-9 repetitions arranged in a single ring around a small centre "
    "circle. Optionally add a ring of small accent points just outside. "
    "No extra layering. Keep outlines thin and clean with clear spacing "
    "between each repeated shape - not thick or filled. Maintain radial "
    "symmetry. The whole motif should be large and centred, filling most "
    "of the frame, on a white background.",

    # Prompt 3 - Traditional Lotus Motif
    "Create an original Indian rangoli built around ONE traditional lotus "
    "motif: a centre circle with 6-9 petals in a single ring, and "
    "optionally a thin outer ring of small dots or triangular accents just "
    "outside the petals. No second petal layer, no heavy border. Thin, "
    "well-separated black line art, single stroke weight - not thick or "
    "filled. Strong radial symmetry. The whole motif should be large and "
    "centred, filling most of the frame, on a white background.",

    # Prompt 4 - Random Lotus/Mandala Generator
    "Create a brand-new rangoli built from ONE ring of 6-9 petals or "
    "repeated shapes around a plain centre circle, with an optional thin "
    "outer ring of small accent points. No second layer, no heavy border, "
    "no fine internal detail. Keep every outline thin, crisp, and clearly "
    "separated from its neighbours - never touching or filled solid. The "
    "whole motif should be large and centred, filling most of the frame, "
    "while remaining unmistakably a traditional rangoli. Black vector "
    "outlines on a white background only. No colors, shading, gradients, "
    "textures, or 3D rendering.",
]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAX_X    = 28
MAX_Y    = 28

# Z-axis positions (mm) used to physically open/close the nozzle valve via
# the Z stepper during a colour change at the origin. Distinct from the
# small 0.05 mm pen-lift used while drawing.
NOZZLE_OPEN_Z   = 1.00
NOZZLE_CLOSED_Z = 0.00

MARGIN_L = 50
MARGIN_B = 40
MARGIN_T = 10
MARGIN_R = 10

# These are set dynamically in ShapeApp.__init__ once the screen height is known
GRAPH_W  = 680
GRAPH_H  = 680
CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

# ── Palette ────────────────────────────────────────────────────────────────────
BG_DARK      = "#0d0b2b"
BG_PANEL     = "#171440"
BG_CARD      = "#1d1a4a"
BG_INPUT     = "#262050"
ACCENT_BLUE  = "#60a5fa"
ACCENT_CYAN  = "#22d3ee"
ACCENT_GREEN = "#10b981"
ACCENT_AMBER = "#f97316"
ACCENT_PINK  = "#f472b6"
ACCENT_PURP  = "#a78bfa"
TEXT_PRIMARY = "#e2e8f0"
TEXT_DIM     = "#94a3b8"
ORIGIN_RED   = "#ff4d6d"
CANVAS_BG    = "#f4f4ff"

# Multi-colour palette: name -> hex swatch used on the canvas and in G-code prompts
COLOUR_PALETTE = {
    "Red":    "#ef4444",
    "Yellow": "#eab308",
    "Green":  "#22c55e",
    "Blue":   "#3b82f6",
    "White":  "#f8fafc",
    "Orange": "#f97316",
    "Pink":   "#ec4899",
    "Purple": "#a855f7",
}

# Mode definitions: (label, emoji, accent_color)
MODES = [
    ("Pre-designed",            "",  ACCENT_BLUE),
    ("AI Generated",            "",  ACCENT_PURP),
    ("Import Designs",          "",  ACCENT_AMBER),
    ("Robot Test",              "",  ACCENT_GREEN),
]

# ── AI Suggestions (diya + flower overlay) palette / timing ───────────────
FLAME_COLORS  = ["#fbbf24", "#f97316", "#fde047", "#f97316", "#fb923c"]
FLOWER_COLORS = ["#f472b6", "#a855f7", "#22d3ee", "#f9a825", "#fb7185", "#34d399"]
AI_FX_TICK_MS = 180

# ── Pre-designed rangoli pattern library (ported from Riya's work) ─────────
# Each raw generator below is a pure function: (size, params) -> list of
# paths, where each path is a list of (x, y) points normalized around the
# origin (0, 0). _translate() maps that normalized output onto the canvas at
# (cx, cy), so PRESET_DESIGNS exposes a generator of the (cx, cy, size) ->
# paths shape the rest of this app expects (thumbnails, on-canvas placement,
# and G-code export all call it that way).

def _translate(paths, cx, cy):
    return [[(cx + x, cy - y) for x, y in path] for path in paths]


def _circle_ring(size, rings=1, points_per_ring=64, inner_ratio=0.3):
    paths = []
    for r in range(rings):
        radius = inner_ratio + (1 - inner_ratio) * ((r + 1) / rings)
        pts = []
        for i in range(points_per_ring):
            a = 2 * math.pi * i / points_per_ring
            pts.append((math.cos(a) * radius * size, math.sin(a) * radius * size))
        pts.append(pts[0])
        paths.append(pts)
    return paths


def _mandala_star(size, spikes=8, inner=0.25, outer=1.0):
    pts = []
    for i in range(spikes * 2):
        r = outer if i % 2 == 0 else inner
        a = math.pi * i / spikes
        pts.append((math.cos(a) * r * size, math.sin(a) * r * size))
    pts.append(pts[0])
    return [pts]


def _petal_burst(size, petals=12, petal_len=1.0, petal_width=0.35, points=20):
    paths = []
    for p in range(petals):
        a0 = 2 * math.pi * p / petals
        pts = []
        for t in range(points + 1):
            tnorm = t / points
            r = petal_width * math.sin(math.pi * tnorm) * petal_len + 0.15
            a = a0 + (tnorm - 0.5) * 0.25
            pts.append((math.cos(a) * r * size, math.sin(a) * r * size))
        paths.append(pts)
    return paths


def _lotus_ring(size, petals=10, inner=0.35, outer=0.95):
    paths = []
    for p in range(petals):
        a0 = 2 * math.pi * p / petals
        pts = []
        for t in range(18):
            tt = t / 17
            r = inner + (outer - inner) * (0.5 - 0.5 * math.cos(math.pi * tt))
            a = a0 + (math.sin(math.pi * tt) * 0.12)
            pts.append((math.cos(a) * r * size, math.sin(a) * r * size))
        paths.append(pts)
    paths += _circle_ring(size, rings=1, points_per_ring=36, inner_ratio=0.2)
    return paths


def _diamond_grid(size, cols=6, rows=6, spacing=0.28):
    paths = []
    w = spacing * size
    h = spacing * size
    start_x = - (cols - 1) * w / 2
    start_y = - (rows - 1) * h / 2
    for r in range(rows):
        pts = []
        for c in range(cols):
            x = start_x + c * w
            y = start_y + r * h
            pts.append((x, y))
        paths.append(pts)
    diag1 = []
    diag2 = []
    for i in range(min(cols, rows)):
        diag1.append((start_x + i * w, start_y + i * h))
        diag2.append((start_x + (cols - 1 - i) * w, start_y + i * h))
    paths.append(diag1)
    paths.append(diag2)
    return paths


def _peacock_bloom(size, feathers=9, loops=3):
    paths = []
    for f in range(feathers):
        a0 = 2 * math.pi * f / feathers
        pts = []
        for t in range(40):
            tt = t / 39
            r = 0.2 + 0.8 * (tt ** 0.6) * (0.6 + 0.4 * math.sin(tt * loops * math.pi))
            a = a0 + (tt - 0.5) * 0.6
            pts.append((math.cos(a) * r * size, math.sin(a) * r * size))
        paths.append(pts)
    paths += _circle_ring(size * 0.35, rings=1, points_per_ring=20, inner_ratio=0.2)
    return paths


# Filter option lists shown (in this order) in the Gallery dropdowns.
FESTIVAL_OPTIONS   = ["All", "Diwali", "Onam", "Pongal/Sankranti", "Navratri", "Ugadi"]
STATE_OPTIONS      = ["All", "Tamil Nadu", "Andhra Pradesh", "Karnataka", "Kerala",
                       "Maharashtra", "Gujarat", "Rajasthan"]
DIFFICULTY_OPTIONS = ["All", "Easy", "Medium", "Hard"]

# The pre-designed pattern library.
PRESET_DESIGNS = {
    "Mandala Star": {
        "generator":  lambda cx, cy, size: _translate(
            _mandala_star(size, spikes=8, inner=0.25, outer=1.0), cx, cy),
        "festivals":  ["Diwali", "Navratri"],
        "states":     ["Rajasthan", "Gujarat"],
        "difficulty": "Medium",
        "petals": 8,
    },
    "Lotus Ring": {
        "generator":  lambda cx, cy, size: _translate(
            _lotus_ring(size, petals=10, inner=0.35, outer=0.95), cx, cy),
        "festivals":  ["Onam", "Pongal/Sankranti"],
        "states":     ["Kerala", "Tamil Nadu"],
        "difficulty": "Easy",
        "petals": 10,
    },
    "Diamond Grid": {
        "generator":  lambda cx, cy, size: _translate(
            _diamond_grid(size, cols=6, rows=6), cx, cy),
        "festivals":  ["Diwali", "Ugadi"],
        "states":     ["Karnataka", "Andhra Pradesh"],
        "difficulty": "Easy",
        "petals": 0,
    },
    "Peacock Bloom": {
        "generator":  lambda cx, cy, size: _translate(
            _peacock_bloom(size, feathers=9, loops=3), cx, cy),
        "festivals":  ["Navratri", "Diwali"],
        "states":     ["Maharashtra", "Gujarat"],
        "difficulty": "Hard",
        "petals": 9,
    },
    "Chakra Wheel": {
        "generator":  lambda cx, cy, size: _translate(
            _circle_ring(size, rings=3, points_per_ring=64, inner_ratio=0.15), cx, cy),
        "festivals":  ["Pongal/Sankranti", "Ugadi"],
        "states":     ["Tamil Nadu", "Andhra Pradesh"],
        "difficulty": "Medium",
        "petals": 0,
    },
    "Petal Burst": {
        "generator":  lambda cx, cy, size: _translate(
            _petal_burst(size, petals=12, petal_len=1.0), cx, cy),
        "festivals":  ["Diwali", "Onam", "Navratri"],
        "states":     ["Kerala", "Karnataka", "Rajasthan"],
        "difficulty": "Hard",
        "petals": 12,
    },
}

DIFFICULTY_COLORS = {"Easy": ACCENT_GREEN, "Medium": ACCENT_AMBER, "Hard": ACCENT_PINK}

class ShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Rangoli-Bot")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.configure(bg=BG_DARK)

        # Fit the canvas inside the available vertical space.
        # Reserve ~220 px for console (7 lines + header) + coord label + padding.
        global GRAPH_W, GRAPH_H, CANVAS_W, CANVAS_H
        screen_h = self.root.winfo_screenheight()
        graph_size = max(300, screen_h - 220)   # square since MAX_X == MAX_Y
        GRAPH_W  = graph_size
        GRAPH_H  = graph_size
        CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
        CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

        self.shapes               = []
        self.selected_shape_index = None
        self.shape_type           = tk.StringVar(value="Select")
        self.feed_rate            = tk.StringVar(value="Medium (default)")
        self.port_var             = tk.StringVar()
        self.size_val             = tk.IntVar(value=50)
        self.is_moving            = False
        self.last_ports           = []
        self.is_sending           = False
        self.hint_popup           = None
        self._dxf_preview_popup   = None
        self.hint_after_id        = None
        self.progress_var         = tk.DoubleVar(value=0.0)

        self.selected_preset      = tk.StringVar(value="")
        self._gallery_popup       = None

        self.multi_colour_var     = tk.BooleanVar(value=False)
        self.shape_colour_var     = tk.StringVar(value=next(iter(COLOUR_PALETTE)))
        self._colour_switch_event = None
        self._pending_colour_event = None

        self._ai_generating = False

        # ── AI Suggestions (diya + flower overlay) state ──────────────────
        self._ai_fx_running  = False
        self._ai_fx_loading  = False
        self._ai_fx_after_id = None
        self._flower_items   = []   # list of lists of canvas item ids
        self._diya_items     = []   # list of canvas item ids

        self.setup_ui()
        self.setup_context_menu()
        self.poll_ports()

    # ── Main UI ───────────────────────────────────────────────────────────────
    def setup_ui(self):
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Left sidebar ──────────────────────────────────────────────────────
        left = tk.Frame(main, bg=BG_DARK, width=340)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # Action buttons pinned at the very bottom, outside the scroll area,
        # so they're always reachable regardless of scroll position.
        btn_wrap = tk.Frame(left, bg=BG_DARK)
        btn_wrap.pack(side="bottom", fill="x")
        self._action_btn(btn_wrap, "Clear Canvas",    self.clear_canvas,         "#7c3aed")
        self.send_btn = self._action_btn(
            btn_wrap, "Send to Rangoli-Bot", self.start_gcode_streaming, "#0d9488")

        # Scrollable area for the banner + sections above the buttons.
        left_canvas = tk.Canvas(left, bg=BG_DARK, highlightthickness=0)
        left_scroll = tk.Scrollbar(left, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left_inner = tk.Frame(left_canvas, bg=BG_DARK)
        inner_win = left_canvas.create_window((0, 0), window=left_inner, anchor="nw")

        def _sync_scrollregion(_e=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        def _sync_inner_width(e):
            left_canvas.itemconfigure(inner_win, width=e.width)
        left_inner.bind("<Configure>", _sync_scrollregion)
        left_canvas.bind("<Configure>", _sync_inner_width)

        def _on_mousewheel(e):
            # macOS reports small raw delta values (no /120 scaling like
            # Windows), so scroll by the sign of the delta there instead.
            if sys.platform == "darwin":
                units = -1 if e.delta > 0 else 1
            else:
                units = int(-1 * (e.delta / 120))
            left_canvas.yview_scroll(units, "units")
        def _on_mousewheel_linux(e):
            left_canvas.yview_scroll(-1 if e.num == 4 else 1, "units")

        def _bind_wheel(_e=None):
            left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            left_canvas.bind_all("<Button-4>", _on_mousewheel_linux)
            left_canvas.bind_all("<Button-5>", _on_mousewheel_linux)
        def _unbind_wheel(_e=None):
            left_canvas.unbind_all("<MouseWheel>")
            left_canvas.unbind_all("<Button-4>")
            left_canvas.unbind_all("<Button-5>")

        left_canvas.bind("<Enter>", _bind_wheel)
        left_canvas.bind("<Leave>", _unbind_wheel)

        # Banner
        self._build_banner(left_inner)

        # Sections
        self._section(left_inner, "Connection",     ACCENT_AMBER, self._build_connection)
        self._section(left_inner, "Design Options",  ACCENT_BLUE,  self._build_design)
        self._section(left_inner, "Print Progress",   ACCENT_CYAN,  self._build_progress)

        # ── Right pane ────────────────────────────────────────────────────────
        right = tk.Frame(main, bg=BG_DARK)
        right.pack(side="right", fill="both", expand=True)

        # ── Console pinned to the bottom FIRST so pack reserves its space
        # before the canvas claims the rest of the height.
        con_wrap = tk.Frame(right, bg=BG_PANEL)
        con_wrap.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        hdr = tk.Frame(con_wrap, bg=BG_PANEL)
        hdr.pack(fill="x")
        tk.Label(hdr, text="REAL-TIME GRBL CONSOLE", bg=BG_PANEL, fg=ACCENT_PURP,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=10, pady=6)
        tk.Button(hdr, text="Clear", bg=BG_PANEL, fg=TEXT_DIM, bd=0,
                  font=("Segoe UI", 9), activebackground=BG_PANEL,
                  command=lambda: self.console.delete("1.0", tk.END)).pack(side="right", padx=10)

        self.console = tk.Text(con_wrap, bg="#110e2e", fg="#a8d8a8",
                               font=("Consolas", 10), bd=0, highlightthickness=0,
                               height=7, insertbackground=ACCENT_GREEN)
        self.console.pack(fill="x", padx=2, pady=(0, 2))
        self.console.tag_config("send", foreground=ACCENT_CYAN)
        self.console.tag_config("recv", foreground=ACCENT_GREEN)
        self.console.tag_config("err",  foreground=ACCENT_PINK)
        self.console.tag_config("info", foreground=ACCENT_AMBER)

        # ── Coord label above console ─────────────────────────────────────────
        self.coord_label = tk.Label(right, text="X: 0.00  Y: 0.00",
                                    bg=BG_DARK, fg=ACCENT_PURP,
                                    font=("Consolas", 10, "bold"))
        self.coord_label.pack(side="bottom", anchor="w", padx=16, pady=(0, 2))

        # ── Progress bar above the console ───────────────────────────────────
        prog_wrap = tk.Frame(right, bg=BG_DARK)
        prog_wrap.pack(side="bottom", fill="x", padx=16, pady=(0, 4))
        self.progress_bar = ctk.CTkProgressBar(
            prog_wrap, variable=self.progress_var,
            fg_color=BG_INPUT, progress_color=ACCENT_PURP,
            height=10, corner_radius=5)
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)

        # ── Canvas packed last so it fills remaining space (top area) ─────────
        canvas_outer = tk.Frame(right, bg=BG_DARK)
        canvas_outer.pack(side="top", fill="both", expand=True)

        canvas_wrap = tk.Frame(canvas_outer, bg="#c4b5fd", bd=1)
        canvas_wrap.place(relx=0.5, rely=0.5, anchor="center")

        self.canvas = tk.Canvas(canvas_wrap, width=CANVAS_W, height=CANVAS_H,
                                bg=CANVAS_BG, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-2>", self.on_right_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Motion>",   self.on_mouse_move)
        self.draw_grid()

        # Colourful "Colour Emptied" button, pinned above the canvas so it's
        # always visible when armed mid-print (rainbow palette makes it pop
        # against the muted sidebar/console colours).
        self.colour_emptied_btn = ctk.CTkButton(
            canvas_outer, text="🎨 Emptied", command=self._on_colour_emptied_click,
            fg_color="#f97316", hover_color="#fb923c",
            border_width=2, border_color="#facc15",
            text_color="#ffffff", font=("Segoe UI", 9, "bold"),
            height=24, width=100, corner_radius=12)
        self.colour_emptied_btn.place(relx=0.0, rely=0.0, anchor="nw", x=4, y=4)
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#4b5563", text_color=TEXT_DIM,
            border_color="#6b7280")

        self._sim_running = False
        self.simulate_btn = ctk.CTkButton(
            canvas_outer, text="▶ Simulate", command=self.simulate_pattern,
            fg_color=ACCENT_GREEN, hover_color="#15803d",
            text_color="#ffffff", font=("Segoe UI", 10, "bold"),
            height=30, width=110, corner_radius=6)
        self.simulate_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-10, y=10)

        # "AI Suggestions" toggle — sits just left of Simulate, above the
        # canvas. Click to flash a diya + a scattered ring of little
        # flowers over the current design; click again to turn it off.
        self.ai_fx_btn = ctk.CTkButton(
            canvas_outer, text="✨ AI Enhance", command=self.toggle_ai_effects,
            fg_color=ACCENT_PURP, hover_color="#8b5cf6",
            text_color="#ffffff", font=("Segoe UI", 10, "bold"),
            height=30, width=130, corner_radius=6)
        self.ai_fx_btn.place(relx=1.0, rely=0.0, anchor="ne", x=-130, y=10)

        for btn in (self.simulate_btn, self.ai_fx_btn):
            self._add_float_hover(btn)

    def _add_float_hover(self, btn, lift=3):
        """Gives a button a floating 'lift' on hover — it rises a few
        pixels and gains a bright border, so the whole raised shape reads
        as clickable rather than relying on hover colour alone."""
        info = {}

        def on_enter(_e=None):
            if not info:
                info["x"] = btn.place_info().get("x")
                info["y"] = btn.place_info().get("y")
                info["border_width"] = int(btn.cget("border_width") or 0)
                info["border_color"] = btn.cget("border_color")
            try:
                new_y = int(float(info["y"])) - lift
                btn.place_configure(y=new_y)
                btn.configure(border_width=2, border_color="#ffffff")
            except (tk.TclError, TypeError, ValueError):
                pass

        def on_leave(_e=None):
            if not info:
                return
            try:
                btn.place_configure(y=info["y"])
                btn.configure(border_width=info["border_width"], border_color=info["border_color"])
            except (tk.TclError, TypeError, ValueError):
                pass

        btn.configure(cursor="hand2")
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)

    # ── Banner ────────────────────────────────────────────────────────────────
    def _build_banner(self, parent):
        banner = tk.Frame(parent, bg=BG_PANEL)
        banner.pack(fill="x", pady=(0, 8))

        # Flower icon drawn on a small canvas
        icon_c = tk.Canvas(banner, width=60, height=60, bg=BG_PANEL,
                           highlightthickness=0)
        icon_c.pack(side="left", padx=(12, 4), pady=10)
        self._draw_flower_icon(icon_c, 30, 30, 24)

        text_fr = tk.Frame(banner, bg=BG_PANEL)
        text_fr.pack(side="left", pady=10)

        # Rangoli-Bot in a warm gradient-looking colour using two overlapping labels
        title_c = tk.Canvas(text_fr, bg=BG_PANEL, highlightthickness=0,
                            width=250, height=34)
        title_c.pack(anchor="w")
        # Draw each character manually for a colour sweep effect
        title = "Rangoli Bot"
        colors = ["#f9a825", "#f97316", "#ec4899", "#a855f7",
                  "#6366f1", "#3b82f6", "#06b6d4", "#10b981",
                  "#f9a825", "#f97316", "#ec4899", "#a855f7", "#6366f1"]
        _char_w = {'i': 7, 'l': 8, 'r': 9, 't': 9, 'f': 9, ' ': 11,
                   'a': 12, 'n': 12, 'g': 12, 'o': 12, 'e': 11, 'k': 11,
                   'R': 13, 'M': 15}
        x_off = 4
        for ch, col in zip(title, colors):
            title_c.create_text(x_off, 17, text=ch, fill=col,
                                font=("Georgia", 17, "bold italic"), anchor="w")
            x_off += _char_w.get(ch, 13)

        tk.Label(text_fr, text="Design Beautiful. Celebrate Tradition.",
                 bg=BG_PANEL, fg=TEXT_DIM, font=("Segoe UI", 7)).pack(anchor="w")

    def _draw_flower_icon(self, c, cx, cy, r):
        petal_colors = ["#f9a825", "#f97316", "#ec4899", "#a855f7",
                        "#6366f1", "#3b82f6", "#06b6d4", "#10b981"]
        for i in range(8):
            a = math.radians(i * 45)
            px = cx + r * 0.6 * math.cos(a)
            py = cy + r * 0.6 * math.sin(a)
            pr = r * 0.38
            col = petal_colors[i % len(petal_colors)]
            c.create_oval(px - pr, py - pr, px + pr, py + pr,
                          fill=col, outline="", stipple="")
        c.create_oval(cx - r*0.28, cy - r*0.28, cx + r*0.28, cy + r*0.28,
                      fill="#ffd700", outline="#fff8dc", width=1)

    # ── Rounded card section ──────────────────────────────────────────────────
    def _section(self, parent, title, accent, builder):
        # Outer frame with accent left border
        outer = tk.Frame(parent, bg=accent)
        outer.pack(fill="x", padx=10, pady=(0, 8))

        inner = tk.Frame(outer, bg=BG_CARD)
        inner.pack(fill="both", padx=(3, 1), pady=(0, 1))

        tk.Label(inner, text=title, bg=BG_CARD, fg=accent,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        body = tk.Frame(inner, bg=BG_CARD)
        body.pack(fill="x", padx=10, pady=(0, 10))
        builder(body, accent)

    # ── Action buttons ────────────────────────────────────────────────────────
    @staticmethod
    def _lighten(hex_col, amount=40):
        """Return a slightly lighter shade of hex_col for hover feedback."""
        r = min(255, int(hex_col[1:3], 16) + amount)
        g = min(255, int(hex_col[3:5], 16) + amount)
        b = min(255, int(hex_col[5:7], 16) + amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _small_btn(self, parent, text, cmd, fg_color, hover_color):
        """Small CTkButton wrapped in a click-forwarding frame so the entire
        row (including the padding around the button) fires cmd, not just
        the exact button pixels — makes small buttons much easier to hit."""
        wrap = tk.Frame(parent, bg=BG_CARD, cursor="hand2")
        wrap.pack(side="right", padx=(4, 0))
        btn = ctk.CTkButton(
            wrap, text=text, command=cmd,
            fg_color=fg_color, hover_color=hover_color,
            text_color="#ffffff",
            font=("Segoe UI", 10, "bold"),
            height=32, width=96, corner_radius=6)
        btn.pack(padx=4, pady=4)
        wrap.bind("<Button-1>", lambda e: cmd())
        return btn

    def _action_btn(self, parent, text, cmd, bg_color):
        hover = self._lighten(bg_color, 35)
        # Wrap in a full-width Frame so clicking anywhere in the row fires cmd
        wrap = tk.Frame(parent, bg=bg_color, cursor="hand2")
        wrap.pack(fill="x", padx=0, pady=3)
        btn = ctk.CTkButton(
            wrap, text=text, command=cmd,
            fg_color=bg_color, hover_color=hover,
            text_color="#ffffff",
            font=("Segoe UI", 11, "bold"),
            height=46, corner_radius=8,
            border_width=0)
        btn.pack(fill="x", expand=True)
        # Clicking the wrapper frame (padding zone) also fires cmd
        wrap.bind("<Button-1>", lambda e: cmd())
        return btn

    # ── Label helper ──────────────────────────────────────────────────────────
    def _label(self, parent, text, fg=TEXT_DIM):
        tk.Label(parent, text=text, bg=BG_CARD, fg=fg,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 1))

    def _make_combo(self, parent, var, values, btn_hover=ACCENT_PURP, **kw):
        cb = ctk.CTkComboBox(parent, variable=var, values=values,
                             state="readonly",
                             fg_color=BG_INPUT, border_color="#3d3880",
                             button_color="#3d3880", button_hover_color=btn_hover,
                             text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                             dropdown_text_color=TEXT_PRIMARY,
                             font=("Segoe UI", 11), **kw)
        cb.pack(fill="x", pady=3)
        # Clicking the text entry area should also open the dropdown.
        # CTkComboBox exposes _dropdown_button (a CTkButton); call its click handler.
        try:
            cb._entry.bind("<Button-1>",
                           lambda *_: cb._dropdown_button._clicked(event=None))
        except Exception:
            pass
        return cb

    def _combo(self, parent, var, values, **kw):
        return self._make_combo(parent, var, values, **kw)

    # ── Section builders ──────────────────────────────────────────────────────
    def _build_connection(self, body, accent):
        self._label(body, "Serial Port:", ACCENT_AMBER)
        self.port_combo = self._make_combo(
            body, self.port_var, [],
            btn_hover=ACCENT_AMBER)
        self.port_menu = self.port_combo   # alias for poll_ports compatibility

    def _build_design(self, body, accent):
        for label, _, col in MODES:
            row = tk.Frame(body, bg=BG_CARD, pady=4)
            row.pack(fill="x")

            tk.Label(row, text=label, bg=BG_CARD,
                     fg=TEXT_PRIMARY, font=("Segoe UI", 11, "bold")).pack(side="left")

            if label == "Import Designs":
                self._small_btn(row, "Browse", self.import_design,
                                 "#d97706", "#b45309")

            elif label == "AI Generated":
                self._small_btn(row, "Generate", self.generate_ai_design,
                                 ACCENT_PURP, "#8b5cf6")

            elif label == "Pre-designed":
                self._small_btn(row, "Gallery", self._open_gallery,
                                 ACCENT_BLUE, "#3b82f6")

            elif label == "Robot Test":
                self.shape_menu = ctk.CTkComboBox(
                    row, variable=self.shape_type,
                    values=["Select", "Square", "Rectangle", "Circle",
                            "Triangle", "Flower", "Complex Flower"],
                    state="readonly",
                    width=130,
                    fg_color=BG_INPUT, border_color="#3d3880",
                    button_color="#3d3880", button_hover_color=ACCENT_GREEN,
                    text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                    dropdown_text_color=TEXT_PRIMARY,
                    font=("Segoe UI", 10),
                    command=lambda v: self._on_shape_menu_select(v))
                self.shape_menu.pack(side="right", padx=(4, 0))

        # Size slider
        self._label(body, "Size:", ACCENT_CYAN)
        size_row = tk.Frame(body, bg=BG_CARD)
        size_row.pack(fill="x", pady=(0, 4))
        self.size_display = tk.Label(size_row, text="50", bg=BG_CARD,
                                     fg=ACCENT_CYAN, font=("Segoe UI", 10, "bold"),
                                     width=4)
        self.size_display.pack(side="right")
        self.size_slider = ctk.CTkSlider(
            size_row, from_=1, to=800, variable=self.size_val,
            command=self._on_slider,
            fg_color=BG_INPUT, progress_color=ACCENT_PURP,
            button_color=ACCENT_PINK, button_hover_color="#f9a8d4",
            width=180)
        self.size_slider.pack(side="left", fill="x", expand=True, pady=2)

        # Speed combobox
        self._label(body, "Speed:", ACCENT_AMBER)
        self._combo(body, self.feed_rate,
                    ["Low", "Medium (default)", "High"])

        # Multi-colour design toggle + per-shape colour picker
        mc_row = tk.Frame(body, bg=BG_CARD)
        mc_row.pack(fill="x", pady=(8, 2))
        self.multi_colour_switch = ctk.CTkSwitch(
            mc_row, text="Multi-colour design", variable=self.multi_colour_var,
            command=self._on_multi_colour_toggle,
            fg_color=BG_INPUT, progress_color=ACCENT_PINK,
            text_color=TEXT_PRIMARY, font=("Segoe UI", 11, "bold"))
        mc_row.bind("<Button-1>", lambda e: None)
        self.multi_colour_switch.pack(side="left")

        self._label(body, "Shape colour:", ACCENT_PINK)
        self.colour_combo = self._make_combo(
            body, self.shape_colour_var, list(COLOUR_PALETTE.keys()),
            btn_hover=ACCENT_PINK, command=self._on_colour_select)
        self.colour_combo.configure(state="disabled")

        # Complex Flower part picker — only shown when a Complex Flower is
        # selected; lets a petal or the centre be coloured independently.
        self.part_label = tk.Label(body, text="Colour part (Complex Flower):",
                                   bg=BG_CARD, fg=ACCENT_PURP,
                                   font=("Segoe UI", 11, "bold"))
        self.part_select_var = tk.StringVar(value="Whole shape")
        self.part_combo = self._make_combo(
            body, self.part_select_var, self._PART_OPTIONS,
            btn_hover=ACCENT_PURP, command=self._on_part_select)
        self.part_label.pack_forget()
        self.part_combo.pack_forget()

    _PART_OPTIONS = ["Whole shape"] + [f"Petal {i+1}" for i in range(8)] + ["Center"]

    def _on_multi_colour_toggle(self):
        enabled = self.multi_colour_var.get()
        self.colour_combo.configure(state="readonly" if enabled else "disabled")
        self._refresh_part_combo_visibility()
        self.log_to_console(
            "Multi-colour design enabled." if enabled else "Multi-colour design disabled.",
            "info")

    def _refresh_part_combo_visibility(self):
        s = (self.shapes[self.selected_shape_index]
             if self.selected_shape_index is not None else None)
        show = (self.multi_colour_var.get() and s is not None
                and s['type'] == "Complex Flower")
        if show:
            self.part_label.pack(anchor="w", pady=(6, 1))
            self.part_combo.pack(fill="x", pady=3)
        else:
            self.part_label.pack_forget()
            self.part_combo.pack_forget()
            self.part_select_var.set("Whole shape")

    def _part_key(self, part_label):
        if part_label == "Center":
            return 8
        return int(part_label.split()[1]) - 1

    def _on_part_select(self, value):
        # Reflect that part's current colour (if any) back into the colour combo.
        if self.selected_shape_index is None:
            return
        s = self.shapes[self.selected_shape_index]
        if value == "Whole shape":
            if s.get('colour'):
                self.shape_colour_var.set(s['colour'])
        else:
            part_col = s.get('path_colours', {}).get(self._part_key(value))
            if part_col:
                self.shape_colour_var.set(part_col)

    def _on_colour_select(self, value):
        self.shape_colour_var.set(value)
        if self.selected_shape_index is None:
            return
        s = self.shapes[self.selected_shape_index]
        part = self.part_select_var.get()
        if s['type'] == "Complex Flower" and part != "Whole shape":
            s.setdefault('path_colours', {})[self._part_key(part)] = value
        else:
            s['colour'] = value
        self.redraw()

    def _on_shape_menu_select(self, value):
        self.shape_type.set(value)
        self.selected_preset.set("")
        self.on_shape_type_selected()

    def _open_gallery(self):
        self.show_gallery_popup()

    def _on_slider(self, val):
        v = int(float(val))
        self.size_val.set(v)
        self.size_display.config(text=str(v))
        self.update_shape_size(v)

    def _build_progress(self, body, accent):
        self.sidebar_progress_var = tk.DoubleVar(value=0.0)
        self.sidebar_progress_bar = ctk.CTkProgressBar(
            body, variable=self.sidebar_progress_var,
            fg_color=BG_INPUT, progress_color=ACCENT_CYAN,
            height=14, corner_radius=6)
        self.sidebar_progress_bar.pack(fill="x", pady=(6, 2))
        self.sidebar_progress_bar.set(0)

        # Tick marks row
        marks_row = tk.Frame(body, bg=BG_CARD)
        marks_row.pack(fill="x")
        for pct in (0, 25, 50, 75, 100):
            tk.Label(marks_row, text=f"{pct}%", bg=BG_CARD, fg=TEXT_DIM,
                     font=("Consolas", 8)).pack(side="left", expand=True)

        self.sidebar_pct_label = tk.Label(
            body, text="0%", bg=BG_CARD, fg=ACCENT_CYAN,
            font=("Segoe UI", 11, "bold"))
        self.sidebar_pct_label.pack(anchor="center", pady=(4, 2))

    # ── Console ───────────────────────────────────────────────────────────────
    def log_to_console(self, msg, tag="info"):
        self.console.insert(tk.END, msg + "\n", tag)
        self.console.see(tk.END)

    # ── Context menu ──────────────────────────────────────────────────────────
    def setup_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=TEXT_PRIMARY,
                                    activebackground=ACCENT_BLUE, activeforeground=BG_DARK,
                                    font=("Segoe UI", 10))
        self.context_menu.add_command(label="Move",   command=self.start_move)
        self.context_menu.add_command(label="Delete", command=self.delete_shape)

    # ── Port polling ──────────────────────────────────────────────────────────
    def poll_ports(self):
        current_ports = [p.device for p in serial.tools.list_ports.comports()]
        if current_ports != self.last_ports:
            self.port_combo.configure(values=current_ports)
            if current_ports:
                new = list(set(current_ports) - set(self.last_ports))
                chosen = (new[0] if new else
                          (current_ports[0] if self.port_var.get() not in current_ports
                           else self.port_var.get()))
                self.port_var.set(chosen)
                self.port_combo.set(chosen)
            else:
                self.port_var.set("")
                self.port_combo.set("")
            self.last_ports = current_ports
        self.root.after(1000, self.poll_ports)

    # ── Coordinate helpers ────────────────────────────────────────────────────
    def to_machine(self, x, y):
        mx = ((x - MARGIN_L) / GRAPH_W) * MAX_X
        my = ((CANVAS_H - MARGIN_B - y) / GRAPH_H) * MAX_Y
        return mx, my

    # ── Grid ──────────────────────────────────────────────────────────────────
    def draw_grid(self):
        c  = self.canvas
        x0, x1 = MARGIN_L, CANVAS_W - MARGIN_R
        y0, y1 = MARGIN_T, CANVAS_H - MARGIN_B

        c.create_rectangle(x0, y0, x1, y1, fill=CANVAS_BG, outline="", tags="grid")

        DOT_COLOR  = "#c8c8e0"
        MAJOR_DOT  = "#9090c0"
        r_minor, r_major = 1, 2
        for ix in range(0, MAX_X + 1, 5):
            for iy in range(0, MAX_Y + 1, 5):
                px  = x0 + (ix / MAX_X) * GRAPH_W
                py  = y1 - (iy / MAX_Y) * GRAPH_H
                major = (ix % 10 == 0) and (iy % 10 == 0)
                r   = r_major if major else r_minor
                col = MAJOR_DOT if major else DOT_COLOR
                c.create_oval(px-r, py-r, px+r, py+r,
                              fill=col, outline="", tags="grid")

        for ix in range(0, MAX_X + 1, 10):
            px = x0 + (ix / MAX_X) * GRAPH_W
            c.create_text(px, y1 + 14, text=str(ix), fill="#8080a0",
                          font=("Consolas", 8), tags="grid")
        for iy in range(0, MAX_Y + 1, 10):
            py = y1 - (iy / MAX_Y) * GRAPH_H
            c.create_text(x0 - 8, py, text=str(iy), fill="#8080a0",
                          font=("Consolas", 8), anchor="e", tags="grid")

        c.create_line(x0, y1, x1, y1, fill="#c084fc", width=2, tags="grid")
        c.create_line(x0, y0, x0, y1, fill="#c084fc", width=2, tags="grid")
        c.create_text((x0 + x1) // 2, CANVAS_H - 4, text="X (mm)",
                      fill="#7c3aed", font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(10, (y0 + y1) // 2,      text="Y",
                      fill="#7c3aed", font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(10, (y0 + y1) // 2 - 14, text="(mm)",
                      fill="#7c3aed", font=("Segoe UI", 7), tags="grid")

        ox, oy = x0, y1
        for hr, hcol in [(22, "#e9d5ff"), (15, "#c084fc"), (10, "#7c3aed")]:
            c.create_oval(ox-hr, oy-hr, ox+hr, oy+hr, fill="", outline=hcol,
                          width=1, tags="grid")
        arm, ah = 40, 6
        c.create_line(ox, oy-12, ox, oy-arm, fill="#7c3aed", width=2, tags="grid")
        c.create_polygon(ox-ah, oy-arm+ah*2, ox+ah, oy-arm+ah*2, ox, oy-arm,
                         fill="#7c3aed", outline="", tags="grid")
        c.create_line(ox+12, oy, ox+arm, oy, fill="#7c3aed", width=2, tags="grid")
        c.create_polygon(ox+arm-ah*2, oy-ah, ox+arm-ah*2, oy+ah, ox+arm, oy,
                         fill="#7c3aed", outline="", tags="grid")
        r = 7
        c.create_oval(ox-r, oy-r, ox+r, oy+r, fill="#7c3aed",
                      outline="#ffffff", width=2, tags="grid")
        c.create_oval(ox-3, oy-3, ox+3, oy+3, fill="#ffffff", outline="", tags="grid")
        c.create_text(ox-4, oy+16, text="(0,0)", fill="#7c3aed",
                      font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(ox+arm+14, oy-6,   text="X+", fill="#7c3aed",
                      font=("Segoe UI", 8, "bold"), tags="grid")
        c.create_text(ox+14, oy-arm-8,   text="Y+", fill="#7c3aed",
                      font=("Segoe UI", 8, "bold"), tags="grid")

    # ── DXF IMPORT ────────────────────────────────────────────────────────────
    def import_design(self):
        self.selected_preset.set("")
        try:
            import ezdxf
            from ezdxf import path as ezpath
        except ImportError:
            messagebox.showerror("Missing Library",
                "ezdxf is required.\nRun: pip install ezdxf")
            return

        path = filedialog.askopenfilename(
            title="Select Design DXF",
            filetypes=[("DXF Files", "*.dxf")])
        if not path:
            return

        self.log_to_console(f"Loading DXF design: {os.path.basename(path)}", "info")

        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
        except Exception as e:
            self.log_to_console(f"Error loading DXF: {e}", "err")
            return

        raw_paths = []
        for entity in msp:
            try:
                p = ezpath.make_path(entity)
            except Exception:
                continue
            pts = [(v.x, v.y) for v in p.flattening(0.05)]
            if len(pts) >= 2:
                raw_paths.append(pts)

        if not raw_paths:
            self.log_to_console("No drawable entities found in DXF.", "err")
            return

        # Some DXFs (e.g. re-traced or double-exported drawings) contain the
        # same stroke twice as separate entities, sometimes with slightly
        # different point counts/spacing. Drop near-duplicate paths so each
        # line is only drawn once, at a single consistent width. Tolerance
        # scales with the drawing's own size so it works regardless of DXF
        # units (mm, inches, raw pixels, ...).
        all_x = [x for pts in raw_paths for x, _ in pts]
        all_y = [y for pts in raw_paths for _, y in pts]
        diag = math.hypot(max(all_x) - min(all_x), max(all_y) - min(all_y)) or 1.0
        TOL = max(diag * 0.01, 1e-6)

        def _resample(pts, samples=12):
            # Resample by arc length (not by point index) so paths with
            # different point densities still line up for comparison.
            dists = [0.0]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                dists.append(dists[-1] + math.hypot(x1 - x0, y1 - y0))
            total = dists[-1] or 1e-9
            out = []
            j = 0
            for i in range(samples):
                target = total * i / (samples - 1)
                while j < len(dists) - 2 and dists[j + 1] < target:
                    j += 1
                seg = dists[j + 1] - dists[j] or 1e-9
                t = (target - dists[j]) / seg
                x0, y0 = pts[j]
                x1, y1 = pts[j + 1] if j + 1 < len(pts) else pts[j]
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
            return out

        def _close(a, b):
            return all(math.hypot(pa[0]-pb[0], pa[1]-pb[1]) <= TOL for pa, pb in zip(a, b))

        resampled = [_resample(pts) for pts in raw_paths]
        keep = [True] * len(raw_paths)
        for i in range(len(raw_paths)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(raw_paths)):
                if not keep[j]:
                    continue
                if _close(resampled[i], resampled[j]) or \
                   _close(resampled[i], list(reversed(resampled[j]))):
                    keep[j] = False
        deduped = [pts for pts, k in zip(raw_paths, keep) if k]
        if len(deduped) < len(raw_paths):
            self.log_to_console(
                f"Removed {len(raw_paths) - len(deduped)} duplicate stroke(s) "
                "from DXF (single-stroke import).", "info")
        raw_paths = deduped

        # Chain strokes so the nozzle travels from one to its nearest
        # neighbour instead of jumping around in file order.
        def _chain(pieces):
            remaining = list(pieces)
            ordered = [remaining.pop(0)]
            while remaining:
                last_pt = ordered[-1][-1]
                best_i, best_d, best_rev = None, None, False
                for idx, pts in enumerate(remaining):
                    d_start = math.hypot(last_pt[0] - pts[0][0], last_pt[1] - pts[0][1])
                    d_end   = math.hypot(last_pt[0] - pts[-1][0], last_pt[1] - pts[-1][1])
                    if best_d is None or d_start < best_d:
                        best_i, best_d, best_rev = idx, d_start, False
                    if d_end < best_d:
                        best_i, best_d, best_rev = idx, d_end, True
                nxt = remaining.pop(best_i)
                ordered.append(list(reversed(nxt)) if best_rev else nxt)
            return ordered

        raw_paths = _chain(raw_paths)
        self._show_dxf_preview_popup(os.path.basename(path), raw_paths)

    # ── Fit a DXF component list into the main canvas and add it ──────────────
    def _finalize_dxf_import(self, filename, raw_paths):
        if not raw_paths:
            self.log_to_console("Import cancelled — no components left.", "err")
            return

        # Fit the design's bounding box into the canvas graph area,
        # preserving aspect ratio and centring it.
        all_x = [x for pts in raw_paths for x, _ in pts]
        all_y = [y for pts in raw_paths for _, y in pts]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        scale = min(GRAPH_W / span_x, GRAPH_H / span_y)
        off_x = MARGIN_L + (GRAPH_W - span_x * scale) / 2
        off_y = MARGIN_T  + (GRAPH_H - span_y * scale) / 2

        def dxf_to_canvas(x, y):
            cx = off_x + (x - min_x) * scale
            cy = off_y + (GRAPH_H - (y - min_y) * scale)  # DXF Y grows up, canvas Y grows down
            return cx, cy

        canvas_paths = [[dxf_to_canvas(x, y) for x, y in pts] for pts in raw_paths]

        # An import replaces any previously imported design rather than
        # stacking on top of it — re-confirming a DXF (or importing again)
        # would otherwise leave two overlapping copies of the design in
        # self.shapes, which looks exactly like a doubled stroke.
        removed = sum(1 for s in self.shapes if s['type'] == 'Imported')
        self.shapes = [s for s in self.shapes if s['type'] != 'Imported']
        if removed:
            self.log_to_console(
                f"Replaced previous imported design ({removed} removed).", "info")

        shape = {
            'type':   'Imported',
            'paths':  canvas_paths,
            'x':      MARGIN_L + GRAPH_W // 2,
            'y':      MARGIN_T  + GRAPH_H // 2,
            'size':   0,
            'colour': self.shape_colour_var.get() if self.multi_colour_var.get() else None,
        }
        self.shapes.append(shape)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

        total_pts = sum(len(p) for p in canvas_paths)
        self.log_to_console(
            f"Imported {filename}: {len(canvas_paths)} stroke paths "
            f"({total_pts} points). Ready to generate G-code.", "recv")

    # ── DXF preview / edit popup ───────────────────────────────────────────────
    def _show_dxf_preview_popup(self, filename, raw_paths):
        self.root.update_idletasks()

        W, H  = 640, 760
        CW    = 560          # preview canvas square
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        # A single Toplevel (matching the other popups in this app) rather
        # than a separate backdrop + card pair — two stacked overrideredirect
        # Toplevels are unreliable on macOS Tk (unpredictable z-order and
        # click-through), which is what made the previous version of this
        # popup unresponsive and stuck semi-transparent.
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=24,
                                fill=BG_CARD, outline=ACCENT_PURP, width=2)
        glass.create_text(28, 30, text=f"Preview: {filename}", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"))
        glass.create_text(28, 54, text="Click Edit, then click a stroke to remove it.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))

        prev_x = (W - CW) // 2
        prev_y = 76
        preview = tk.Canvas(popup, width=CW, height=CW, bg=CANVAS_BG, highlightthickness=0)
        preview.place(x=prev_x, y=prev_y)

        status_lbl = tk.Label(popup, text="", bg=BG_CARD, fg=TEXT_DIM,
                              font=("Segoe UI", 9, "bold"))
        status_lbl.place(x=28, y=prev_y + CW + 10)

        state = {
            'remaining': list(raw_paths),
            'edit': False,
            'items': [],   # list of (canvas_item_id, path)
        }

        # Fixed fit (computed once from the FULL original set) so strokes
        # don't jump around the preview as components are deleted.
        all_x = [x for pts in raw_paths for x, _ in pts]
        all_y = [y for pts in raw_paths for _, y in pts]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        span_x = max(max_x - min_x, 1e-9)
        span_y = max(max_y - min_y, 1e-9)
        pad = 20
        pscale = min((CW - 2*pad) / span_x, (CW - 2*pad) / span_y)

        def to_preview(x, y):
            px = pad + (x - min_x) * pscale
            py = pad + (CW - 2*pad - (y - min_y) * pscale)
            return px, py

        def redraw_preview():
            preview.delete("stroke")
            state['items'] = []
            for pts in state['remaining']:
                flat = [c for x, y in pts for c in to_preview(x, y)]
                if len(flat) < 4:
                    continue
                item = preview.create_line(flat, fill=ACCENT_PINK, width=2,
                                           smooth=True, tags="stroke")
                state['items'].append((item, pts))
            status_lbl.config(
                text=f"{len(state['remaining'])} component(s)"
                     + ("  —  edit mode: click a stroke to delete" if state['edit'] else ""))

        def on_preview_click(e):
            if not state['edit'] or not state['items']:
                return
            closest = preview.find_closest(e.x, e.y)
            if not closest:
                return
            item_id = closest[0]
            for entry in state['items']:
                if entry[0] == item_id:
                    state['remaining'].remove(entry[1])
                    break
            redraw_preview()

        preview.bind("<Button-1>", on_preview_click)
        redraw_preview()

        edit_btn = ctk.CTkButton(
            popup, text="Edit: OFF", width=110, height=32,
            fg_color="#3d3880", hover_color="#4d4790",
            text_color="#ffffff", font=("Segoe UI", 10, "bold"))
        def toggle_edit():
            state['edit'] = not state['edit']
            edit_btn.configure(text=f"Edit: {'ON' if state['edit'] else 'OFF'}",
                               fg_color=ACCENT_PINK if state['edit'] else "#3d3880")
            preview.configure(cursor="hand2" if state['edit'] else "arrow")
            redraw_preview()
        edit_btn.configure(command=toggle_edit)
        edit_btn.place(x=28, y=H - 56)

        cancel_btn = ctk.CTkButton(
            popup, text="Cancel", width=110, height=32,
            fg_color="#6b7280", hover_color="#4b5563",
            text_color="#ffffff", font=("Segoe UI", 10, "bold"),
            command=lambda: self._dxf_preview_cancel())
        cancel_btn.place(x=W - 260, y=H - 56)

        confirm_btn = ctk.CTkButton(
            popup, text="Confirm Import", width=120, height=32,
            fg_color=ACCENT_GREEN, hover_color="#15803d",
            text_color="#ffffff", font=("Segoe UI", 10, "bold"),
            command=lambda: self._dxf_preview_confirm(filename, state))
        confirm_btn.place(x=W - 140, y=H - 56)

        self._dxf_preview_popup = popup
        self._fade(popup, 0.0, 0.96, 0.08)
        # Force this window above the main app and give it exclusive input
        # focus so clicks/edit-mode actually land on it instead of passing
        # through to whatever's underneath.
        popup.lift()
        popup.focus_force()
        popup.grab_set()

    def _dxf_preview_confirm(self, filename, state):
        remaining = state['remaining']
        self._close_dxf_preview_popup()
        self._finalize_dxf_import(filename, remaining)

    def _dxf_preview_cancel(self):
        self._close_dxf_preview_popup()
        self.log_to_console("DXF import cancelled.", "info")

    def _close_dxf_preview_popup(self):
        win = self._dxf_preview_popup
        if win is not None:
            try: win.grab_release()
            except Exception: pass
            try: win.destroy()
            except Exception: pass
            self._dxf_preview_popup = None

    # ── AI GENERATED DESIGN (OpenAI) ──────────────────────────────────────
    def _get_openai_api_key(self):
        """Hardcoded API key — no need to paste it."""
        HARDCODED_API_KEY = "ADD YOUR OPENAI API KEY HERE"
        return HARDCODED_API_KEY

    def _forget_openai_api_key(self):
        try:
            if os.path.exists(AI_KEY_FILE):
                os.remove(AI_KEY_FILE)
        except Exception:
            pass

    def _open_rangoli_quiz_dialog(self):
        """Pre-generation quiz: ask user about design preferences."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Rangoli Design Preferences")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(pad, text="Tell us about your design", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 12))

        def add_field(label_text, options):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 2))
            var = tk.StringVar(value=options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=340, fg_color=BG_INPUT, border_color="#3d3880",
                button_color="#3d3880", button_hover_color=ACCENT_AMBER,
                text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10))
            combo.pack(anchor="w", pady=(0, 8))
            return var

        state_var = add_field(
            "1. Which Indian state/region style?",
            ["Any / Surprise me", "Kolam (Tamil Nadu / Kerala)",
             "Alpana (Bengal / Maharashtra)", "Mandana (Rajasthan)",
             "Rangoli (North / Central India)", "Pookalam (Kerala)"])

        occasion_var = add_field(
            "2. What occasion?",
            ["Everyday / No specific occasion", "Diwali", "Pongal / Sankranti",
             "Onam", "Navratri", "Wedding", "Housewarming"])

        complexity_var = add_field(
            "3. Complexity level?",
            ["Simple", "Medium", "Complex"])

        colour_var = add_field(
            "4. Preferred colours?",
            ["Surprise me / Any colours", "Traditional (turmeric, vermillion, white)",
             "Bright and vibrant", "Pastel and soft", "Monochrome"])

        material_var = add_field(
            "5. Material preference?",
            ["Any / Mixed materials", "Rice flour", "Turmeric",
             "Vermillion", "Flower petals"])

        def submit():
            dlg.destroy()
            prefs = [
                state_var.get(),
                occasion_var.get(),
                complexity_var.get(),
                colour_var.get(),
                material_var.get()
            ]
            self._start_ai_generation_with_quiz(prefs)

        ctk.CTkButton(pad, text="Generate Design", command=submit,
                      fg_color=ACCENT_PURP, hover_color="#8b5cf6",
                      text_color="#ffffff", font=("Segoe UI", 11, "bold"),
                      height=38, corner_radius=8).pack(fill="x", pady=(12, 0))

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _start_ai_generation_with_quiz(self, quiz_answers):
        """Start AI generation with quiz preferences bundled into the prompt."""
        theme = (
            f"A rangoli in the {quiz_answers[0]} style, "
            f"made for {quiz_answers[1]}, "
            f"with {quiz_answers[2].lower()} complexity, "
            f"featuring {quiz_answers[3].lower()} colours, "
            f"using {quiz_answers[4].lower()} materials."
        )
        self._start_ai_generation(custom_theme=theme)

    def generate_ai_design(self):
        """Entry point: Show choice popup (Quiz / Surprise Me / Prompt)."""
        if self._ai_generating:
            return
        self.selected_preset.set("")
        self._open_design_choice_dialog()

    def _open_design_choice_dialog(self):
        """Popup asking user to choose: Quiz, Surprise Me, or Prompt."""
        dlg = tk.Toplevel(self.root)
        dlg.title("How do you want to generate?")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(pad, text="How do you want to generate your rangoli?", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 16))

        def on_close():
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row, text="Answer Questions",
            command=lambda: (dlg.destroy(), self._open_rangoli_quiz_dialog()),
            fg_color=ACCENT_PURP, hover_color="#8b5cf6",
            text_color="#ffffff", font=("Segoe UI", 11, "bold"),
            height=40, corner_radius=8
        ).pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            btn_row, text="Surprise Me",
            command=lambda: (dlg.destroy(), self._start_ai_generation()),
            fg_color=ACCENT_CYAN, hover_color="#0891b2",
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=40, corner_radius=8
        ).pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            btn_row, text="Type Your Idea",
            command=lambda: (dlg.destroy(), self._open_ai_prompt_dialog()),
            fg_color=ACCENT_AMBER, hover_color="#b45309",
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=40, corner_radius=8
        ).pack(fill="x")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _open_ai_prompt_dialog(self):
        existing = getattr(self, '_ai_dialog', None)
        if existing is not None:
            try:
                existing.lift()
                existing.focus_force()
                return
            except Exception:
                self._ai_dialog = None

        dlg = tk.Toplevel(self.root)
        dlg.title("AI Generated Rangoli")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._ai_dialog = dlg

        def on_close():
            self._ai_dialog = None
            try:
                dlg.destroy()
            except Exception:
                pass

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(pad, text="Describe the rangoli you'd like",
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(pad, text="e.g. \"peacock feathers and lotus\", \"Diwali diyas\", "
                            "\"simple geometric with stars\"",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 10),
                 justify="left", wraplength=360).pack(anchor="w", pady=(2, 10))

        entry = ctk.CTkEntry(
            pad, width=360, height=36,
            placeholder_text="Type your rangoli idea here...",
            fg_color=BG_INPUT, border_color="#3d3880",
            text_color=TEXT_PRIMARY, font=("Segoe UI", 11))
        entry.pack(fill="x")

        note = tk.Label(
            pad, text="Only rangoli / mandala designs can be generated here — "
                       "anything you type is used as a theme for a rangoli, "
                       "not a literal picture.",
            bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 9),
            justify="left", wraplength=360)
        note.pack(anchor="w", pady=(6, 14))

        def start_custom():
            text = entry.get().strip()
            if not text:
                note.configure(text="Type an idea above, or tap 'Surprise Me' "
                                     "for a random design instead.",
                                fg="#f97316")
                return
            on_close()
            self._start_ai_generation(custom_theme=text)

        def start_random():
            on_close()
            self._start_ai_generation(custom_theme=None)

        entry.bind("<Return>", lambda e: start_custom())

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row, text="Generate my idea", command=start_custom,
            fg_color=ACCENT_CYAN, hover_color=self._lighten(ACCENT_CYAN, -30),
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=38, corner_radius=8
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))

        ctk.CTkButton(
            btn_row, text="Surprise Me", command=start_random,
            fg_color=ACCENT_PURP, hover_color=self._lighten(ACCENT_PURP, -30),
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=38, corner_radius=8
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        tk.Label(pad, text="— or —", bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", 9)).pack(pady=(10, 6))

        def start_guided():
            on_close()
            self._open_guided_dialog()

        ctk.CTkButton(
            pad, text="Answer 4 Quick Questions", command=start_guided,
            fg_color=ACCENT_AMBER, hover_color=self._lighten(ACCENT_AMBER, -30),
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=38, corner_radius=8
        ).pack(fill="x")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")

        entry.focus_set()
        dlg.grab_set()

    def _open_guided_dialog(self):
        """4-question guided flow: builds a theme description from structured
        answers, then reuses the exact same generation pipeline as the free-text
        and Surprise Me options."""
        existing = getattr(self, '_ai_dialog', None)
        if existing is not None:
            try:
                existing.lift()
                existing.focus_force()
                return
            except Exception:
                self._ai_dialog = None

        dlg = tk.Toplevel(self.root)
        dlg.title("Guided Rangoli Design")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._ai_dialog = dlg

        def on_close():
            self._ai_dialog = None
            try:
                dlg.destroy()
            except Exception:
                pass

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(pad, text="A few quick questions", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))

        def add_field(label_text, options, default=None):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 2))
            var = tk.StringVar(value=default or options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=360, fg_color=BG_INPUT, border_color="#3d3880",
                button_color="#3d3880", button_hover_color=ACCENT_AMBER,
                text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10))
            combo.pack(anchor="w")
            return var

        festival_var = add_field(
            "1. What festival or occasion is this for?",
            ["Everyday / no specific occasion", "Diwali", "Pongal / Sankranti",
             "Onam", "Navratri", "Ugadi / Gudi Padwa", "Wedding",
             "Housewarming", "Other festival / celebration"])

        style_var = add_field(
            "2. Which regional style would you like?",
            ["Surprise me / any style", "Kolam (Tamil Nadu / Kerala)",
             "Alpana (Bengal / Maharashtra)", "Mandana (Rajasthan)",
             "Rangoli (North / Central India, classic floral-geometric)",
             "Pookalam (Kerala, flower-petal style)"])

        setting_var = add_field(
            "3. Is this for your household or a community space?",
            ["Household (front entrance / courtyard)",
             "Community / temple (larger, more elaborate)"])

        colour_var = add_field(
            "4. Any colours or materials you'd like featured?",
            ["Surprise me / any colours",
             "Traditional (turmeric yellow, vermillion red, rice-flour white)"]
            + list(COLOUR_PALETTE.keys()))

        def submit():
            on_close()
            parts = []
            fest = festival_var.get()
            if not fest.startswith("Everyday"):
                parts.append(f"made for the {fest} occasion")

            style = style_var.get()
            if not style.startswith("Surprise me"):
                parts.append(f"in the {style} regional style")

            setting = setting_var.get()
            if setting.startswith("Community"):
                parts.append("designed at a larger, more elaborate "
                              "community/temple scale with extra detail")
            else:
                parts.append("sized and styled for a home entrance/courtyard")

            colour = colour_var.get()
            if not colour.startswith("Surprise me"):
                parts.append(f"featuring {colour.lower()} tones")

            theme = "A rangoli " + ", ".join(parts) if parts else None
            self._start_ai_generation(custom_theme=theme)

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(16, 0))

        ctk.CTkButton(
            btn_row, text="Generate", command=submit,
            fg_color=ACCENT_AMBER, hover_color=self._lighten(ACCENT_AMBER, -30),
            text_color="#0d0b2b", font=("Segoe UI", 11, "bold"),
            height=38, corner_radius=8
        ).pack(fill="x")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")

        dlg.grab_set()

    def _sanitize_theme(self, text):
        """Best-effort cleanup so a typed theme can only steer the STYLE of a
        rangoli, never override what's actually being asked for."""
        text = text.replace('"', "'").replace("\n", " ").replace("\r", " ").strip()
        lowered = text.lower()
        banned_snippets = [
            "ignore previous", "ignore all previous", "disregard the above",
            "disregard previous", "system prompt", "you are now", "act as",
            "jailbreak", "pretend you are", "new instructions",
        ]
        for snippet in banned_snippets:
            idx = lowered.find(snippet)
            if idx != -1:
                text = text[:idx].strip()
                lowered = text.lower()
        text = text[:150].strip()
        if not text:
            text = "traditional rangoli"
        return text

    def _start_ai_generation(self, custom_theme=None):
        if self._ai_generating:
            return
        api_key = self._get_openai_api_key()
        if not api_key:
            self.log_to_console(
                "AI Generated: no API key entered, so nothing was generated.", "err")
            return

        self._ai_generating = True
        if custom_theme:
            self.log_to_console(
                f"AI Generated: asking OpenAI to draw a rangoli themed \"{custom_theme}\"...",
                "info")
            self.show_hint_popup("Asking AI to draw your rangoli...")
        else:
            self.log_to_console("AI Generated: asking OpenAI to draw a rangoli image...", "info")
            self.show_hint_popup("Asking AI to draw a rangoli image...")
        threading.Thread(
            target=self._ai_generate_worker, args=(api_key, custom_theme), daemon=True
        ).start()

    def _ai_generate_worker(self, api_key, custom_theme=None):
        try:
            img_bytes = self._call_openai_for_rangoli_image(api_key, custom_theme)
            canvas_paths, tk_img = self._extract_paths_from_image_bytes(img_bytes)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            if e.code == 401:
                self._forget_openai_api_key()
                msg = ("AI Generated: that API key was rejected. "
                       "Click 'AI Generated' again to enter a fresh one.")
            else:
                msg = f"AI Generated: OpenAI returned an error ({e.code}). {body[:200]}"
            self.root.after(0, lambda: self.log_to_console(msg, "err"))
        except ImportError as e:
            err = str(e)
            self.root.after(0, lambda: self.log_to_console(
                f"AI Generated: missing library ({err}).", "err"))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self.log_to_console(
                f"AI Generated: something went wrong ({err}).", "err"))
        else:
            self.root.after(0, lambda: self._apply_ai_design(canvas_paths, tk_img))
        finally:
            self._ai_generating = False
            self.root.after(0, lambda: self.hide_hint_popup(instant=True))

    def _call_openai_for_rangoli_image(self, api_key, custom_theme=None):
        """Asks OpenAI's image model to draw a rangoli as clean black-on-white
        line art, and returns the raw PNG bytes."""
        if custom_theme:
            theme = self._sanitize_theme(custom_theme)
            base_prompt = (
                "Create an original traditional Indian rangoli / mandala "
                "floor-art design. Use the following only as loose stylistic "
                f"inspiration for its motifs, shapes, and mood: \"{theme}\". "
                "The result must still be unmistakably a rangoli: a radially "
                "symmetric pattern built from petal, floral, or geometric "
                "motifs arranged around a centre point — not a realistic "
                "illustration, portrait, scene, logo, object, or anything "
                "other than a rangoli/mandala pattern. Black outlines only "
                "on a white background. No fills, colors, shading, "
                "gradients, textures, or 3D rendering."
            )
        else:
            base_prompt = random.choice(RANGOLI_IMAGE_PROMPTS)
        prompt = (
            base_prompt +
            " This design will be physically drawn at a small 28mm x 28mm "
            "scale by a powder-dispensing robot, so keep it to ONE motif "
            "only: a centre circle with ONE ring of 6-9 petals (or a single "
            "shape repeated 6-9 times), plus optionally a thin outer ring "
            "of small accent points - nothing more. No second petal layer, "
            "no heavy border, no dense fine detail. Every outline must be a "
            "single thin, clean stroke - never a thick marker line, never a "
            "filled/solid shape - with a clear visible gap between each "
            "petal/shape so they don't touch or overlap. The whole motif "
            "should be large and centred, filling roughly 75% of the frame "
            "with only a small margin of white space around the edge. "
            "Viewed straight-on from directly above, like a coloring-book "
            "page or stencil. No text, no watermark, no signature. "
            f"Design variation seed: {random.randint(1, 999999)}."
        )
        body = {
            "model": "gpt-image-1",
            "prompt": prompt,
            "size": "1024x1024",
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        last_err = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    raw = resp.read().decode("utf-8")
                result = json.loads(raw)
                break
            except json.JSONDecodeError as e:
                last_err = e
                time.sleep(1.5)
                continue
        else:
            raise ValueError(
                "OpenAI's response could not be read (bad/incomplete data "
                f"from the server): {last_err}")

        item = result["data"][0]
        if item.get("b64_json"):
            import base64
            return base64.b64decode(item["b64_json"])
        elif item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=60) as img_resp:
                return img_resp.read()
        else:
            raise ValueError("OpenAI response did not include an image.")

    def _extract_paths_from_image_bytes(self, img_bytes):
        """Runs the AI-generated image through the same outline-tracing
        pipeline used by 'Import Designs', so the two features behave
        identically once a picture exists."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            raise ImportError("Pillow is required. Run: pip install Pillow")
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise ImportError("opencv-python is required. Run: pip install opencv-python")
        import io

        pil_img = Image.open(io.BytesIO(img_bytes)).convert("L")
        pil_img = pil_img.resize((GRAPH_W, GRAPH_H), Image.LANCZOS)
        alpha = pil_img.point(lambda x: 0 if x > 65 else 255)
        rgba = Image.new("RGBA", pil_img.size, (0, 0, 0, 0))
        rgba.putalpha(alpha)
        tk_img = ImageTk.PhotoImage(rgba)

        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError("Could not decode the AI-generated image.")

        ih, iw = img.shape[:2]
        blurred2 = cv2.GaussianBlur(img, (5, 5), 0)
        _, binary = cv2.threshold(blurred2, 65, 255, cv2.THRESH_BINARY_INV)

        close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k)

        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)

        def img_to_canvas(iy_px, ix_px):
            cx = MARGIN_L + (ix_px / iw) * GRAPH_W
            cy = MARGIN_T + (iy_px / ih) * GRAPH_H
            return cx, cy

        MIN_AREA = (iw * ih) * 0.00015
        MAX_SHAPES = 16

        candidates = []
        for i, cnt in enumerate(contours):
            if hierarchy[0][i][3] != -1:
                continue
            area = cv2.contourArea(cnt)
            if area < MIN_AREA:
                continue
            candidates.append((area, cnt))

        candidates.sort(key=lambda pair: pair[0], reverse=True)
        candidates = candidates[:MAX_SHAPES]

        canvas_paths = []
        for area, cnt in candidates:
            epsilon = max(2.0, 0.005 * cv2.arcLength(cnt, closed=True))
            approx = cv2.approxPolyDP(cnt, epsilon, closed=True)
            if len(approx) < 3:
                continue
            path = [img_to_canvas(int(pt[0][1]), int(pt[0][0])) for pt in approx]
            path.append(path[0])
            canvas_paths.append(path)

        if not canvas_paths:
            raise ValueError("No outlines could be traced from the AI image.")

        SHRINK = 0.95
        cx0 = MARGIN_L + GRAPH_W / 2
        cy0 = MARGIN_T + GRAPH_H / 2
        canvas_paths = [
            [(cx0 + (x - cx0) * SHRINK, cy0 + (y - cy0) * SHRINK) for x, y in path]
            for path in canvas_paths
        ]

        return canvas_paths, tk_img

    def _apply_ai_design(self, canvas_paths, tk_img=None):
        shape = {
            'type':   'Imported',
            'tk_img': tk_img,
            'paths':  canvas_paths,
            'x':      MARGIN_L + GRAPH_W // 2,
            'y':      MARGIN_T + GRAPH_H // 2,
            'size':   0,
            'colour': self.shape_colour_var.get() if self.multi_colour_var.get() else None,
        }
        self.shapes.append(shape)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

        total_pts = sum(len(p) for p in canvas_paths)
        self.log_to_console(
            f"AI Generated: new design added ({len(canvas_paths)} outlines, "
            f"{total_pts} points). Ready to send.", "recv")

    # ── Hint popup ────────────────────────────────────────────────────────────
    def on_shape_type_selected(self, event=None):
        if self.shape_type.get() == "Select":
            self.hide_hint_popup(instant=True)
        else:
            self.show_hint_popup("Click anywhere on the canvas to place shape")

    def _draw_rounded_rect(self, canvas, x1, y1, x2, y2, radius=18, **kw):
        pts = [x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
               x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
               x1, y2, x1, y2-radius, x1, y1+radius, x1, y1]
        return canvas.create_polygon(pts, smooth=True, **kw)

    def show_hint_popup(self, message):
        self.hide_hint_popup(instant=True)
        self.root.update_idletasks()
        w, h = 400, 60
        cx = self.canvas.winfo_rootx() + self.canvas.winfo_width() // 2
        # Sits below the Simulate / AI Enhance buttons row (which occupies
        # roughly y=10..40 above the canvas) — previously this used y=32,
        # a strip that overlapped the top of those buttons and silently
        # ate clicks on that sliver, forcing users to click lower/elsewhere
        # on the button to actually hit it.
        cy = self.canvas.winfo_rooty() + 50
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{w}x{h}+{cx - w//2}+{cy}")
        popup.configure(bg=BG_DARK)
        glass = tk.Canvas(popup, width=w, height=h, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, w-4, h-4, radius=20,
                                fill=BG_CARD, outline=ACCENT_CYAN, width=1)
        glass.create_text(w//2, h//2, text=message,
                          fill=ACCENT_CYAN, font=("Segoe UI", 10, "bold"))
        self.hint_popup = popup
        self._fade(popup, 0.0, 0.95, 0.08)
        self.hint_after_id = self.root.after(4500, self.hide_hint_popup)

    def _arm_colour_emptied_button(self, event):
        # No popup — just enable the sidebar button and hold the streaming
        # thread until the user confirms the nozzle has drained.
        self._pending_colour_event = event
        self.colour_emptied_btn.configure(
            state="normal", fg_color="#f97316", hover_color="#fb923c",
            border_color="#facc15", text_color="#ffffff")

    def _on_colour_emptied_click(self):
        event = self._pending_colour_event
        if event is None:
            return
        self._pending_colour_event = None
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#4b5563", text_color=TEXT_DIM,
            border_color="#6b7280")
        event.set()

    def hide_hint_popup(self, instant=False):
        if self.hint_after_id is not None:
            try: self.root.after_cancel(self.hint_after_id)
            except Exception: pass
            self.hint_after_id = None
        popup = self.hint_popup
        if popup is None: return
        if instant:
            try: popup.destroy()
            except Exception: pass
            self.hint_popup = None
            return
        self._fade(popup, 0.95, 0.0, -0.1, on_done=lambda: self._destroy_popup(popup))

    def _destroy_popup(self, popup):
        try: popup.destroy()
        except Exception: pass
        if self.hint_popup is popup:
            self.hint_popup = None

    def _fade(self, win, current, target, step, on_done=None):
        try:
            nxt  = current + step
            done = (step > 0 and nxt >= target) or (step < 0 and nxt <= target)
            val  = target if done else nxt
            win.attributes("-alpha", val)
        except tk.TclError:
            done = True
        if done:
            if on_done: on_done()
            return
        win.after(15, lambda: self._fade(win, val, target, step, on_done))

    # ── Pre-designed gallery ─────────────────────────────────────────────────
    def _draw_preset_thumbnail(self, canvas, name, cx, cy, size):
        paths = PRESET_DESIGNS[name]['generator'](cx, cy, size)
        for path in paths:
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in pt]
            canvas.create_line(flat, fill=ACCENT_BLUE, width=2, smooth=True)

    def show_gallery_popup(self):
        self._close_gallery_popup()
        self.root.update_idletasks()

        W, H = 860, 680
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._gallery_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=24,
                                fill=BG_CARD, outline=ACCENT_BLUE, width=2)
        glass.create_text(28, 30, text="Rangoli Gallery", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 16, "bold"))
        glass.create_text(28, 54, text="Pick a design, then click the canvas to place it.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 14, "bold"), cursor="hand2")
        close_lbl.place(x=W-44, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._close_gallery_popup())

        # ── Filters ──────────────────────────────────────────────────────────
        filt = tk.Frame(popup, bg=BG_CARD)
        filt.place(x=26, y=78, width=W-52, height=36)

        festival_var   = tk.StringVar(value="All")
        state_var      = tk.StringVar(value="All")
        difficulty_var = tk.StringVar(value="All")

        tk.Label(filt, text="Festival:", bg=BG_CARD, fg=ACCENT_AMBER,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 4))
        fest_combo = ctk.CTkComboBox(
            filt, variable=festival_var, values=FESTIVAL_OPTIONS, state="readonly",
            width=150, fg_color=BG_INPUT, border_color="#3d3880",
            button_color="#3d3880", button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10),
            command=lambda v: refresh())
        fest_combo.pack(side="left", padx=(0, 14))

        tk.Label(filt, text="State:", bg=BG_CARD, fg=ACCENT_CYAN,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 4))
        state_combo = ctk.CTkComboBox(
            filt, variable=state_var, values=STATE_OPTIONS, state="readonly",
            width=170, fg_color=BG_INPUT, border_color="#3d3880",
            button_color="#3d3880", button_hover_color=ACCENT_CYAN,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10),
            command=lambda v: refresh())
        state_combo.pack(side="left", padx=(0, 14))

        tk.Label(filt, text="Difficulty:", bg=BG_CARD, fg=ACCENT_PINK,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 4))
        diff_combo = ctk.CTkComboBox(
            filt, variable=difficulty_var, values=DIFFICULTY_OPTIONS, state="readonly",
            width=120, fg_color=BG_INPUT, border_color="#3d3880",
            button_color="#3d3880", button_hover_color=ACCENT_PINK,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10),
            command=lambda v: refresh())
        diff_combo.pack(side="left")

        count_label = tk.Label(popup, text="", bg=BG_DARK, fg=TEXT_DIM,
                               font=("Segoe UI", 10, "bold"))
        count_label.place(x=28, y=120)

        reset_btn = ctk.CTkButton(
            popup, text="Reset Filters", command=lambda: _reset(),
            fg_color="#475569", hover_color="#334155", text_color="#ffffff",
            font=("Segoe UI", 10, "bold"), height=30, width=130, corner_radius=6)

        # ── Scrollable thumbnail grid ───────────────────────────────────────
        grid_outer = tk.Frame(popup, bg=BG_CARD)
        grid_top = 148
        grid_outer.place(x=26, y=grid_top, width=W-52, height=H-grid_top-24)

        grid_canvas = tk.Canvas(grid_outer, bg=BG_CARD, highlightthickness=0)
        grid_scroll = tk.Scrollbar(grid_outer, orient="vertical", command=grid_canvas.yview)
        grid_canvas.configure(yscrollcommand=grid_scroll.set)
        grid_scroll.pack(side="right", fill="y")
        grid_canvas.pack(side="left", fill="both", expand=True)

        grid_inner = tk.Frame(grid_canvas, bg=BG_CARD)
        inner_win = grid_canvas.create_window((0, 0), window=grid_inner, anchor="nw")

        def _sync_scrollregion(_e=None):
            grid_canvas.configure(scrollregion=grid_canvas.bbox("all"))
        grid_inner.bind("<Configure>", _sync_scrollregion)

        def _wheel(e):
            units = (-1 if e.delta > 0 else 1) if sys.platform == "darwin" \
                    else int(-1 * (e.delta / 120))
            grid_canvas.yview_scroll(units, "units")
        def _wheel_linux(e):
            grid_canvas.yview_scroll(-1 if e.num == 4 else 1, "units")
        def _bind_wheel(_e=None):
            grid_canvas.bind_all("<MouseWheel>", _wheel)
            grid_canvas.bind_all("<Button-4>", _wheel_linux)
            grid_canvas.bind_all("<Button-5>", _wheel_linux)
        def _unbind_wheel(_e=None):
            grid_canvas.unbind_all("<MouseWheel>")
            grid_canvas.unbind_all("<Button-4>")
            grid_canvas.unbind_all("<Button-5>")
        grid_canvas.bind("<Enter>", _bind_wheel)
        grid_canvas.bind("<Leave>", _unbind_wheel)

        def _reset():
            festival_var.set("All");   fest_combo.set("All")
            state_var.set("All");      state_combo.set("All")
            difficulty_var.set("All"); diff_combo.set("All")
            refresh()

        def refresh():
            for w in grid_inner.winfo_children():
                w.destroy()
            reset_btn.place_forget()

            matches = []
            for name, meta in PRESET_DESIGNS.items():
                if festival_var.get() != "All" and festival_var.get() not in meta["festivals"]:
                    continue
                if state_var.get() != "All" and state_var.get() not in meta["states"]:
                    continue
                if difficulty_var.get() != "All" and difficulty_var.get() != meta["difficulty"]:
                    continue
                matches.append((name, meta))

            n = len(matches)
            count_label.config(text=f"{n} design{'s' if n != 1 else ''} found")

            if not matches:
                tk.Label(grid_inner, text="No designs match these filters.",
                         bg=BG_CARD, fg=TEXT_DIM,
                         font=("Segoe UI", 12, "bold")).pack(pady=(50, 10))
                reset_btn.place(x=W//2 - 65, y=H - 60)
                grid_canvas.configure(scrollregion=(0, 0, 0, 0))
                return

            cols = 3
            for idx, (name, meta) in enumerate(matches):
                r, c = divmod(idx, cols)
                card = tk.Frame(grid_inner, bg=BG_INPUT, cursor="hand2")
                card.grid(row=r, column=c, padx=8, pady=8, sticky="n")

                thumb = tk.Canvas(card, width=140, height=140, bg=CANVAS_BG,
                                  highlightthickness=0)
                thumb.pack(padx=6, pady=(6, 2))
                self._draw_preset_thumbnail(thumb, name, 70, 70, 56)

                tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                         font=("Segoe UI", 10, "bold")).pack()
                tk.Label(card, text=meta["difficulty"], bg=BG_INPUT,
                         fg=DIFFICULTY_COLORS.get(meta["difficulty"], TEXT_DIM),
                         font=("Segoe UI", 9, "bold")).pack()
                tags_txt = ", ".join(meta["festivals"]) + "\n" + ", ".join(meta["states"])
                tk.Label(card, text=tags_txt, bg=BG_INPUT, fg=TEXT_DIM,
                         font=("Segoe UI", 8), wraplength=138,
                         justify="center").pack(pady=(0, 8))

                for widget in [card, thumb] + list(card.winfo_children()):
                    widget.bind("<Button-1>", lambda e, nm=name: self._choose_preset(nm))

            grid_inner.update_idletasks()
            grid_canvas.configure(scrollregion=grid_canvas.bbox("all"))

        refresh()
        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        popup.grab_set()

    def _close_gallery_popup(self):
        popup = self._gallery_popup
        if popup is None:
            return
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self._gallery_popup = None
        self.root.focus_force()

    def _choose_preset(self, name):
        self.selected_preset.set(name)
        self.shape_type.set("Select")
        self._close_gallery_popup()
        self.log_to_console(f"Pre-designed pattern selected: {name}", "info")
        self.show_hint_popup(f"Click canvas to place '{name}'")

    # ── Canvas interactions ───────────────────────────────────────────────────
    def on_canvas_click(self, event):
        if self.is_moving:
            self.is_moving = False
            return
        preset = self.selected_preset.get()
        if preset:
            self.hide_hint_popup()
            colour = self.shape_colour_var.get() if self.multi_colour_var.get() else None
            self.shapes.append({'type': 'Preset', 'preset': preset,
                                'x': event.x, 'y': event.y,
                                'size': self.size_val.get(),
                                'colour': colour})
            self.selected_shape_index = len(self.shapes) - 1
            self.redraw()
            return
        if self.shape_type.get() == "Select":
            self.show_hint_popup("Select a shape from the dropdown first")
            return
        self.hide_hint_popup()
        colour = self.shape_colour_var.get() if self.multi_colour_var.get() else None
        self.shapes.append({'type': self.shape_type.get(),
                            'x': event.x, 'y': event.y,
                            'size': self.size_val.get(),
                            'colour': colour})
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

    def on_right_click(self, event):
        if not self.shapes: return
        closest_idx, min_dist = None, float('inf')
        for i, s in enumerate(self.shapes):
            d = math.hypot(s['x'] - event.x, s['y'] - event.y)
            if d < min_dist:
                min_dist, closest_idx = d, i
        if closest_idx is not None and min_dist < 60:
            self.selected_shape_index = closest_idx
            self.size_val.set(self.shapes[closest_idx]['size'])
            self._refresh_part_combo_visibility()
            shape_colour = self.shapes[closest_idx].get('colour')
            if self.multi_colour_var.get() and shape_colour:
                self.shape_colour_var.set(shape_colour)
            self.redraw()
            self.context_menu.post(event.x_root, event.y_root)

    def on_mouse_move(self, event):
        mx = max(0, min(MAX_X, ((event.x - MARGIN_L) / GRAPH_W) * MAX_X))
        my = max(0, min(MAX_Y, ((CANVAS_H - MARGIN_B - event.y) / GRAPH_H) * MAX_Y))
        self.coord_label.config(text=f"X: {mx:.2f}  Y: {my:.2f}")
        if self.is_moving and self.selected_shape_index is not None:
            s  = self.shapes[self.selected_shape_index]
            dx = event.x - s['x']
            dy = event.y - s['y']
            s['x'] = event.x
            s['y'] = event.y
            if s['type'] == 'Imported':
                s['paths'] = [[(cx + dx, cy + dy) for cx, cy in path]
                              for path in s['paths']]
            self.redraw()

    def start_move(self):
        if self.selected_shape_index is not None:
            self.is_moving = True

    def delete_shape(self):
        if self.selected_shape_index is not None:
            del self.shapes[self.selected_shape_index]
            self.selected_shape_index = None
            self._refresh_part_combo_visibility()
            self.redraw()

    def update_shape_size(self, val):
        if self.selected_shape_index is not None and not self.is_moving:
            s = self.shapes[self.selected_shape_index]
            if s['type'] != 'Imported':
                s['size'] = int(val)
                self.redraw()

    # ── Shape colours ─────────────────────────────────────────────────────────
    _SHAPE_COLORS = {
        "Square":         "#7c3aed",
        "Rectangle":      "#6d28d9",
        "Circle":         "#9333ea",
        "Triangle":       "#7c3aed",
        "Flower":         "#ec4899",
        "Complex Flower": "#ec4899",
        "Imported":       "#ec4899",
        "Preset":         ACCENT_BLUE,
    }
    _SELECTED_COLOR = "#ec4899"

    def simulate_pattern(self):
        if self._sim_running:
            self._sim_running = False
            self.canvas.delete("sim_dot")
            self.simulate_btn.configure(text="▶ Simulate")
            return

        points = []
        for s in self.shapes:
            for path in self._shape_paths(s):
                points.extend(path)
        if not points:
            self.log_to_console("Nothing to simulate — draw or import a design first.", "err")
            return

        self._sim_running = True
        self.simulate_btn.configure(text="■ Stop")
        self.canvas.delete("sim_dot")

        def step(i=0):
            if not self._sim_running:
                return
            if i >= len(points):
                self._sim_running = False
                self.simulate_btn.configure(text="▶ Simulate")
                self.canvas.delete("sim_dot")
                return
            x, y = points[i]
            self.canvas.delete("sim_dot")
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                                    fill=ACCENT_GREEN, outline="#ffffff",
                                    width=1, tags="sim_dot")
            self.canvas.after(4, step, i + 1)

        step()

    # ── AI Suggestions (diya + twinkling flowers overlay) ─────────────────
    # Placement is decided by a real OpenAI vision call: the current design
    # is rendered to an image, sent to the model along with a description
    # of a 20x20 grid (columns A-T left→right, rows 1-20 top→bottom), and
    # the model replies with grid cells for the diya and each flower. Those
    # cells are converted to canvas coordinates, drawn onto both the live
    # tkinter canvas (for the flashing animation) and a composited PNG that
    # gets saved to the Downloads folder as puung.png.

    AI_FX_GRID_COLS = 20   # A .. T
    AI_FX_GRID_ROWS = 20   # 1 .. 20

    def _grid_code_to_canvas_xy(self, code):
        """'H5' -> (x, y) in canvas pixel space, clamped to the grid."""
        code = str(code).strip().upper()
        col_letter = code[0]
        row_num = int(''.join(ch for ch in code[1:] if ch.isdigit()) or 1)
        col_idx = max(0, min(self.AI_FX_GRID_COLS - 1, ord(col_letter) - ord('A')))
        row_idx = max(0, min(self.AI_FX_GRID_ROWS - 1, row_num - 1))
        cell_w = GRAPH_W / self.AI_FX_GRID_COLS
        cell_h = GRAPH_H / self.AI_FX_GRID_ROWS
        x = MARGIN_L + (col_idx + 0.5) * cell_w
        y = MARGIN_T + (row_idx + 0.5) * cell_h
        return x, y

    def _local_xy_to_grid_code(self, lx, ly):
        """Inverse of _grid_code_to_canvas_xy, for local (unmargined) coords."""
        cell_w = GRAPH_W / self.AI_FX_GRID_COLS
        cell_h = GRAPH_H / self.AI_FX_GRID_ROWS
        col_idx = max(0, min(self.AI_FX_GRID_COLS - 1, int(lx / cell_w)))
        row_idx = max(0, min(self.AI_FX_GRID_ROWS - 1, int(ly / cell_h)))
        return f"{chr(ord('A') + col_idx)}{row_idx + 1}"

    def _render_design_image(self):
        """Rasterize the current design (strokes only) to a PIL image the
        same pixel size as the drawing grid, for sending to the AI and for
        compositing the final saved PNG."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (GRAPH_W, GRAPH_H), "#ffffff")
        draw = ImageDraw.Draw(img)
        for s in self.shapes:
            for path in self._shape_paths(s):
                pts = [(x - MARGIN_L, y - MARGIN_T) for x, y in path]
                if len(pts) >= 2:
                    draw.line(pts, fill=(30, 30, 30), width=2)
        return img

    def _design_points_local(self):
        """All drawn-path points in local (design-image) pixel space, used
        to geometrically verify AI-chosen flower cells actually sit outside
        the rangoli rather than trusting the model's grid reading blindly."""
        pts = []
        for s in self.shapes:
            for path in self._shape_paths(s):
                for x, y in path:
                    pts.append((x - MARGIN_L, y - MARGIN_T))
        return pts

    def _design_outline_radius_at_angle(self, ang, points, cx, cy):
        """Max distance from (cx, cy) among design points within a narrow
        angular window around `ang` — i.e. how far out the outline extends
        at that angle."""
        max_r = 0.0
        for px, py in points:
            pa = math.atan2(py - cy, px - cx)
            diff = abs((pa - ang + math.pi) % (2 * math.pi) - math.pi)
            if diff < math.radians(8):
                pr = math.hypot(px - cx, py - cy)
                if pr > max_r:
                    max_r = pr
        return max_r

    def _evenly_space_flowers_outside(self, flower_codes, margin=15):
        """Takes the AI's rough flower cell picks and re-derives their
        angles around the design centre, then snaps those angles to be
        perfectly evenly spaced (keeping the AI's overall starting
        rotation), and places each flower just outside the outline at its
        new angle. Returns (local_xy_list, grid_code_list)."""
        points = self._design_points_local()
        n = len(flower_codes)
        if not points or n == 0:
            xy = [self._grid_code_to_canvas_xy(c) for c in flower_codes]
            xy = [(x - MARGIN_L, y - MARGIN_T) for x, y in xy]
            return xy, list(flower_codes)

        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)

        angles = []
        for code in flower_codes:
            x, y = self._grid_code_to_canvas_xy(code)
            lx, ly = x - MARGIN_L, y - MARGIN_T
            angles.append(math.atan2(ly - cy, lx - cx))
        angles.sort()
        base_ang = angles[0]

        xy, codes = [], []
        for i in range(n):
            ang = base_ang + i * (2 * math.pi / n)
            r = self._design_outline_radius_at_angle(ang, points, cx, cy) + margin
            lx, ly = cx + r * math.cos(ang), cy + r * math.sin(ang)
            xy.append((lx, ly))
            codes.append(self._local_xy_to_grid_code(lx, ly))
        return xy, codes

    def _render_grid_overlay_image(self, design_img):
        """Returns a copy of design_img with the 20x20 A-T / 1-20 reference
        grid drawn in red, each cell labelled with its small bold grid
        code (e.g. G1, H6, J8), so the AI can read coordinates directly
        off the image instead of guessing them from text alone."""
        from PIL import ImageDraw
        img = design_img.copy()
        draw = ImageDraw.Draw(img)
        RED = (220, 0, 0)
        cell_w = GRAPH_W / self.AI_FX_GRID_COLS
        cell_h = GRAPH_H / self.AI_FX_GRID_ROWS

        for col in range(self.AI_FX_GRID_COLS + 1):
            x = col * cell_w
            draw.line([(x, 0), (x, GRAPH_H)], fill=RED, width=1)
        for row in range(self.AI_FX_GRID_ROWS + 1):
            y = row * cell_h
            draw.line([(0, y), (GRAPH_W, y)], fill=RED, width=1)

        for col in range(self.AI_FX_GRID_COLS):
            for row in range(self.AI_FX_GRID_ROWS):
                code = f"{chr(ord('A') + col)}{row + 1}"
                tx = col * cell_w + 1
                ty = row * cell_h + 1
                for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                    draw.text((tx + ox, ty + oy), code, fill=RED)
        return img

    def _ask_ai_for_fx_coordinates(self, api_key, design_img):
        """Sends the rendered design to OpenAI and asks for grid coordinates
        for the diya and flower placements. Returns dict with 'diya' (str)
        and 'flowers' (list[str]), all in A1-T20 grid notation."""
        import io, base64
        buf = io.BytesIO()
        design_img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        prompt = """This image is a rangoli floor-art design drawn on a 20x20 grid. Columns are lettered A to T, left to right. Rows are numbered 1 to 20, top to bottom. So A1 is the top-left cell, T1 is the top-right cell, A20 is the bottom-left cell, and T20 is the bottom-right cell. The centre of the grid is around J10/K10/J11/K11.

            Choose grid cells to decorate THIS SPECIFIC design, based only on what you actually see in the image:
            1. ONE cell for a single diya (oil lamp), placed as close to the exact centre of the design as possible (around J10-K11), since there should be exactly one diya sitting in the middle of the whole rangoli.
            2. SIX to TEN cells for small flower accents, placed COMPLETELY OUTSIDE the rangoli's outermost drawn boundary, each one tucked into the notch/gap directly between two neighbouring petals or points — i.e. just past the tip of the V-shaped gap where two petals meet, in the blank space right outside the design at that gap. Do not place a flower directly in front of a petal tip or in the open background away from any gap. Look at each notch between adjacent petals around the outside of the shape and pick a cell just beyond it. Spread these flowers out so they land in different, evenly-spaced notches around the design, not bunched up on one side.

            Every chosen cell must fall on blank white space, not on top of a drawn line, and no two chosen cells should be adjacent to each other.

            Reply with ONLY compact JSON, no markdown, no commentary. Use this exact key structure — the values below are an EXAMPLE ONLY, showing the format, not the answer. Replace them with your own real cell choices based on the image:
            EXAMPLE FORMAT (do not copy these values): {"diyas": ["J11"], "flowers": ["C5", "Q5", "F15", "O15", "J3", "J18"]}"""
        body = {
            "model": "gpt-5.4",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                ],
            }],
            "max_completion_tokens": 250,
            "temperature": 0.7,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"].strip()
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)

        diyas = [str(d).strip() for d in data.get("diyas", []) if str(d).strip()]
        if not diyas:
            single = str(data.get("diya", "")).strip()
            diyas = [single] if single else []
        diyas = diyas[:1]
        flowers = [str(f).strip() for f in data.get("flowers", []) if str(f).strip()]
        if not diyas or not flowers:
            raise ValueError("AI response did not include usable coordinates.")
        return {"diyas": diyas, "flowers": flowers}

    def _save_augmented_png(self, design_img, diya_codes, flower_codes, flower_xy=None):
        """Draws the diya + flower glyphs (at their AI-chosen grid cells)
        onto a copy of the rendered design and saves it to Downloads as
        puung.png."""
        from PIL import ImageDraw
        img = design_img.copy()
        draw = ImageDraw.Draw(img)

        def to_local(code):
            x, y = self._grid_code_to_canvas_xy(code)
            return x - MARGIN_L, y - MARGIN_T

        RED = (220, 0, 0)

        for diya_code in diya_codes:
            dx, dy = to_local(diya_code)
            r = 13
            draw.ellipse([dx - r, dy - r * 0.55, dx + r, dy + r * 0.55],
                         fill=(124, 45, 18), outline=(69, 26, 3))
            draw.polygon([(dx, dy - r * 1.7), (dx - r * 0.32, dy - r * 0.45),
                          (dx + r * 0.32, dy - r * 0.45)], fill=(251, 191, 36))
            draw.text((dx + r + 2, dy - r * 1.7), diya_code.upper(), fill=RED)

        flower_xy = flower_xy or [to_local(c) for c in flower_codes]
        for i, code in enumerate(flower_codes):
            fx, fy = flower_xy[i]
            fr = 11
            color = FLOWER_COLORS[i % len(FLOWER_COLORS)]
            color_rgb = tuple(int(color[j:j+2], 16) for j in (1, 3, 5))
            for p in range(5):
                a = 2 * math.pi * p / 5
                px, py = fx + fr * 0.62 * math.cos(a), fy + fr * 0.62 * math.sin(a)
                draw.ellipse([px - fr * 0.42, py - fr * 0.42,
                              px + fr * 0.42, py + fr * 0.42], fill=color_rgb)
            draw.ellipse([fx - fr * 0.32, fy - fr * 0.32,
                          fx + fr * 0.32, fy + fr * 0.32], fill=(253, 224, 71))
            draw.text((fx + fr + 2, fy - fr), code.upper(), fill=RED)

        out_path = os.path.expanduser("~/Downloads/puung.png")
        img.save(out_path)
        return out_path

    def _ai_fx_worker(self, api_key):
        try:
            design_img = self._render_design_image()
            grid_img = self._render_grid_overlay_image(design_img)
            grid_img.save(os.path.expanduser("~/Downloads/puung_input.png"))
            coords = self._ask_ai_for_fx_coordinates(api_key, grid_img)
            flower_xy, flower_codes = self._evenly_space_flowers_outside(coords["flowers"])
            coords["flowers"] = flower_codes
            out_path = self._save_augmented_png(
                design_img, coords["diyas"], coords["flowers"], flower_xy)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            msg = f"AI Enhance: OpenAI returned an error ({e.code}). {body[:200]}"
            self.root.after(0, lambda: self._ai_fx_failed(msg))
        except ImportError:
            self.root.after(0, lambda: self._ai_fx_failed(
                "AI Enhance: Pillow is required. Run: pip install Pillow"))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self._ai_fx_failed(
                f"AI Enhance: something went wrong ({err})."))
        else:
            self.root.after(0, lambda: self._apply_ai_fx_coords(coords, out_path, flower_xy))

    def _ai_fx_failed(self, msg):
        self._ai_fx_loading = False
        self.hide_hint_popup(instant=True)
        self.ai_fx_btn.configure(text="✨ AI Enhance",
                                 fg_color=ACCENT_PURP, hover_color="#8b5cf6",
                                 state="normal")
        self.log_to_console(msg, "err")

    def _apply_ai_fx_coords(self, coords, out_path, flower_xy):
        self._ai_fx_loading = False
        self.hide_hint_popup(instant=True)
        self.canvas.delete("ai_fx")
        self._flower_items = []
        self._diya_items = []

        for code in coords["diyas"]:
            dx, dy = self._grid_code_to_canvas_xy(code)
            self._diya_items.append(self._draw_diya_glyph(dx, dy))

        for i, (lx, ly) in enumerate(flower_xy):
            fx, fy = lx + MARGIN_L, ly + MARGIN_T

            self._flower_items.append(
                self._draw_flower_glyph(fx, fy, color=FLOWER_COLORS[i % len(FLOWER_COLORS)]))

        self._ai_fx_running = True
        self.ai_fx_btn.configure(text="✨ Stop Enhance",
                                 fg_color="#f97316", hover_color="#fb923c",
                                 state="normal")
        self.log_to_console(
            f"AI Enhance: placed 1 diya at "
            f"{coords['diyas'][0]} and "
            f"{len(coords['flowers'])} flower(s) — saved to {out_path}.", "recv")

    def _draw_diya_glyph(self, x, y, r=13):
        items = []
        items.append(self.canvas.create_oval(
            x - r, y - r * 0.55, x + r, y + r * 0.55,
            fill="#7c2d12", outline="#451a03", width=1, tags=("ai_fx", "diya_base")))
        items.append(self.canvas.create_polygon(
            x, y - r * 1.7, x - r * 0.32, y - r * 0.45, x + r * 0.32, y - r * 0.45,
            fill=FLAME_COLORS[0], outline="", smooth=True, tags=("ai_fx", "diya_flame")))
        return items

    def _draw_flower_glyph(self, x, y, r=11, petals=5, color=None):
        color = color or random.choice(FLOWER_COLORS)
        items = []
        for p in range(petals):
            a = 2 * math.pi * p / petals
            px, py = x + r * 0.62 * math.cos(a), y + r * 0.62 * math.sin(a)
            items.append(self.canvas.create_oval(
                px - r * 0.42, py - r * 0.42, px + r * 0.42, py + r * 0.42,
                fill=color, outline="", tags=("ai_fx", "flower")))
        items.append(self.canvas.create_oval(
            x - r * 0.32, y - r * 0.32, x + r * 0.32, y + r * 0.32,
            fill="#fde047", outline="", tags=("ai_fx", "flower")))
        return items

    def toggle_ai_effects(self):
        if self._ai_fx_running:
            self._ai_fx_running = False
            if self._ai_fx_after_id is not None:
                try: self.root.after_cancel(self._ai_fx_after_id)
                except Exception: pass
                self._ai_fx_after_id = None
            self.canvas.delete("ai_fx")
            self._flower_items = []
            self._diya_items = []
            self.ai_fx_btn.configure(text="✨ AI Enhance",
                                     fg_color=ACCENT_PURP, hover_color="#8b5cf6")
            self.log_to_console("AI Suggestions turned off.", "info")
            return

        if getattr(self, "_ai_fx_loading", False):
            return

        if not self.shapes:
            self.log_to_console(
                "Nothing to enhance yet — place or generate a rangoli first.", "err")
            return

        api_key = self._get_openai_api_key()
        if not api_key:
            self.log_to_console(
                "AI Enhance: no API key available, so nothing was generated.", "err")
            return

        self._ai_fx_loading = True
        self.ai_fx_btn.configure(text="✨ Thinking...", state="disabled")
        self.log_to_console(
            "AI Enhance: asking OpenAI to choose diya + flower placements...", "info")
        self.show_hint_popup("Asking AI to place a diya and flowers...")
        threading.Thread(target=self._ai_fx_worker, args=(api_key,), daemon=True).start()

    def redraw(self):
        self.canvas.delete("shape")
        multi = self.multi_colour_var.get()
        for i, s in enumerate(self.shapes):
            selected = (i == self.selected_shape_index)
            # The shape's own (or chosen) colour is always used for the
            # stroke — selection is shown via extra line width + the
            # centre handle dot, not by overriding the colour.
            if multi and s.get('colour'):
                col = COLOUR_PALETTE.get(s['colour'], "#7c3aed")
            else:
                col = self._SHAPE_COLORS.get(s['type'], "#7c3aed")
            lw  = 3 if selected else 1

            if s['type'] == 'Imported':
                path_colours = s.get('path_colours', {}) if multi else {}
                for pidx, path in enumerate(s['paths']):
                    if len(path) < 2:
                        continue
                    flat = [coord for pt in path for coord in pt]
                    pcol = COLOUR_PALETTE.get(path_colours.get(pidx), col) \
                        if path_colours.get(pidx) else col
                    self.canvas.create_line(flat, fill=pcol, width=lw,
                                            smooth=True, tags="shape")
                if selected:
                    all_x = [cx for path in s['paths'] for cx, _ in path]
                    all_y = [cy for path in s['paths'] for _, cy in path]
                    if all_x:
                        self.canvas.create_rectangle(
                            min(all_x)-4, min(all_y)-4, max(all_x)+4, max(all_y)+4,
                            outline=self._SELECTED_COLOR, width=1, dash=(4, 4), tags="shape")
                continue

            sz   = s['size']
            x, y = s['x'], s['y']

            if s['type'] == "Square":
                self.canvas.create_rectangle(x-sz/2, y-sz/2, x+sz/2, y+sz/2,
                                             outline=col, width=lw, tags="shape")
            elif s['type'] == "Rectangle":
                self.canvas.create_rectangle(x-sz, y-sz/2, x+sz, y+sz/2,
                                             outline=col, width=lw, tags="shape")
            elif s['type'] == "Circle":
                self.canvas.create_oval(x-sz/2, y-sz/2, x+sz/2, y+sz/2,
                                        outline=col, width=lw, tags="shape")
            elif s['type'] == "Triangle":
                self.canvas.create_polygon([x, y-sz/2, x-sz/2, y+sz/2, x+sz/2, y+sz/2],
                                           outline=col, fill="", width=lw, tags="shape")
            elif s['type'] == "Flower":
                coords = self.get_shape_coords(s)
                flat   = [c for pt in coords for c in pt]
                if len(flat) >= 4:
                    self.canvas.create_line(flat, fill=col, width=lw,
                                            tags="shape", smooth=True)
            elif s['type'] == "Complex Flower":
                self._draw_complex_flower(s, lw, multi)
            elif s['type'] == "Preset":
                self._draw_preset_shape(s, lw, multi)

            if selected:
                self.canvas.create_oval(x-4, y-4, x+4, y+4,
                                        fill=self._SELECTED_COLOR, outline="#ffffff",
                                        width=1, tags="shape")

        # Keep the AI-enhance overlay drawn above the design if it's active
        # (shapes are recreated above via delete("shape"), which leaves the
        # separately-tagged "ai_fx" items untouched and already on top).

    def _complex_flower_paths(self, x, y, sz):
        R = sz / 2
        paths = []

        def _circle(cx, cy, r, n=64):
            return [(cx + r * math.cos(math.radians(i * 360 / n)),
                     cy + r * math.sin(math.radians(i * 360 / n)))
                    for i in range(n + 1)]

        petal_outer = R * 1.30
        petal_inner = R * 0.40
        petal_d     = (petal_outer + petal_inner) / 2
        petal_l     = (petal_outer - petal_inner) / 2
        petal_w     = R * 0.16

        steps = 80
        for p in range(8):
            angle = math.radians(p * 45)
            pts   = []
            for i in range(steps + 1):
                t  = math.radians(i * 360 / steps)
                lx = petal_l * math.cos(t)
                ly = petal_w * math.sin(t) * abs(math.sin(t))
                rx = lx * math.cos(angle) - ly * math.sin(angle)
                ry = lx * math.sin(angle) + ly * math.cos(angle)
                pts.append((x + petal_d * math.cos(angle) + rx,
                            y + petal_d * math.sin(angle) + ry))
            paths.append(pts)

        paths.append(_circle(x, y, R * 0.40))
        return paths

    def _draw_complex_flower(self, s, lw, multi):
        x, y, sz = s['x'], s['y'], s['size']
        paths = self._complex_flower_paths(x, y, sz)
        path_colours = s.get('path_colours', {})
        if multi and s.get('colour'):
            base_col = COLOUR_PALETTE.get(s['colour'], "#ec4899")
        else:
            base_col = self._SHAPE_COLORS.get(s['type'], "#ec4899")

        selected_part = None
        if (self.selected_shape_index is not None
                and self.shapes[self.selected_shape_index] is s
                and self.part_select_var.get() != "Whole shape"):
            selected_part = self._part_key(self.part_select_var.get())

        for idx, path in enumerate(paths):
            flat = [c for pt in path for c in pt]
            if len(flat) < 4:
                continue
            part_col = path_colours.get(idx) if multi else None
            col = COLOUR_PALETTE.get(part_col, base_col) if part_col else base_col
            part_lw = lw + 2 if idx == selected_part else lw
            if idx < 8:
                self.canvas.create_polygon(flat, outline=col, fill="",
                                           width=part_lw, smooth=True, tags="shape")
            else:
                self.canvas.create_line(flat, fill=col, width=part_lw,
                                        smooth=True, tags="shape")

    def _draw_preset_shape(self, s, lw, multi):
        x, y, sz = s['x'], s['y'], s['size']
        paths = PRESET_DESIGNS[s['preset']]['generator'](x, y, sz)
        if multi and s.get('colour'):
            col = COLOUR_PALETTE.get(s['colour'], self._SHAPE_COLORS.get('Preset'))
        else:
            col = self._SHAPE_COLORS.get('Preset', ACCENT_BLUE)
        for path in paths:
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in pt]
            self.canvas.create_line(flat, fill=col, width=lw,
                                    smooth=True, tags="shape")

    # ── G-code helpers ────────────────────────────────────────────────────────
    def get_shape_coords(self, s):
        sz   = s['size']
        x, y = s['x'], s['y']
        res  = []
        if s['type'] == "Square":
            res = [(x-sz/2, y-sz/2), (x+sz/2, y-sz/2),
                   (x+sz/2, y+sz/2), (x-sz/2, y+sz/2), (x-sz/2, y-sz/2)]
        elif s['type'] == "Rectangle":
            res = [(x-sz, y-sz/2), (x+sz, y-sz/2),
                   (x+sz, y+sz/2), (x-sz, y+sz/2), (x-sz, y-sz/2)]
        elif s['type'] == "Triangle":
            res = [(x, y-sz/2), (x-sz/2, y+sz/2), (x+sz/2, y+sz/2), (x, y-sz/2)]
        elif s['type'] == "Circle":
            for i in range(33):
                a = math.radians(i * (360 / 32))
                res.append((x + (sz/2)*math.cos(a), y + (sz/2)*math.sin(a)))
        elif s['type'] == "Flower":
            for i in range(121):
                a = math.radians(i * 3)
                r = sz*0.15 + sz*0.45 * abs(math.cos(5*a/2))
                res.append((x + r*math.cos(a), y + r*math.sin(a)))
        return res

    def clear_canvas(self):
        self.shapes = []
        self.selected_shape_index = None
        self.canvas.delete("shape")
        if self._ai_fx_running:
            self.toggle_ai_effects()
        self.log_to_console("Canvas cleared.", "info")

    def _shape_paths(self, s):
        if s['type'] == 'Imported':
            return s['paths']
        elif s['type'] == "Complex Flower":
            return self._complex_flower_paths(s['x'], s['y'], s['size'])
        elif s['type'] == "Preset":
            return PRESET_DESIGNS[s['preset']]['generator'](s['x'], s['y'], s['size'])
        else:
            return [self.get_shape_coords(s)]

    def _paths_gcode_lines(self, paths, f):
        lines = []
        for path in paths:
            if len(path) < 2:
                continue
            mx, my = self.to_machine(*path[0])
            lines += [
                f"G1 Z0.00 F{f}",
                f"G1 X{mx:.2f} F{f}",
                f"G1 Y{my:.2f} F{f}",
                "M3",
                f"G1 Z0.05 F{f}",
            ]
            for px, py in path[1:]:
                mx, my = self.to_machine(px, py)
                lines.append(f"G1 X{mx:.2f} Y{my:.2f} F{f}")
            lines.append("M5")
        return lines

    def _shape_gcode_lines(self, s, f):
        return self._paths_gcode_lines(self._shape_paths(s), f)

    def generate_gcode(self):
        _SPEED_MAP = {"Low": 150, "Medium (default)": 200, "High": 250}
        f = _SPEED_MAP.get(self.feed_rate.get(), 200)
        lines = ["$X", "G21", "G90", f"F{f}"]

        has_colour = any(s.get('colour') or s.get('path_colours')
                          for s in self.shapes)
        if self.multi_colour_var.get() and has_colour:
            # Group individual path-lists by colour (in order of first
            # appearance) so the robot draws everything of one colour
            # before switching. A Complex Flower with per-part colours is
            # split so each coloured part lands in its own colour group.
            ordered_colours = []
            groups = {}

            def _add(colour, paths):
                colour = colour or "Uncoloured"
                if colour not in groups:
                    groups[colour] = []
                    ordered_colours.append(colour)
                groups[colour].append(paths)

            for s in self.shapes:
                path_colours = s.get('path_colours')
                if path_colours:
                    for idx, path in enumerate(self._shape_paths(s)):
                        _add(path_colours.get(idx, s.get('colour')), [path])
                else:
                    _add(s.get('colour'), self._shape_paths(s))

            for colour in ordered_colours:
                # Return to origin, then drive the Z stepper to the "open"
                # position so the currently loaded colour can drain out
                # before the next one is added. The Z move back to
                # NOZZLE_CLOSED_Z is only reached once the app resumes
                # streaming after the colour is added (see send_gcode()).
                lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0", "M5",
                          f"G1 Z{NOZZLE_OPEN_Z:.2f} F{f}",
                          f";COLOUR_SWITCH:{colour}",
                          f"G1 Z{NOZZLE_CLOSED_Z:.2f} F{f}"]
                for paths in groups[colour]:
                    lines += self._paths_gcode_lines(paths, f)
        else:
            for s in self.shapes:
                lines += self._shape_gcode_lines(s, f)

        lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0"]
        path_out = os.path.expanduser("~/Downloads/design.gcode")
        with open(path_out, "w") as fh:
            fh.write("\n".join(lines))
        self.log_to_console(f"G-code saved -> {path_out}", "info")
        return path_out

    # ── GRBL streaming ────────────────────────────────────────────────────────
    def start_gcode_streaming(self):
        if self.is_sending: return
        if not self.port_var.get():
            self.log_to_console("Error: Choose a serial port first.", "err")
            return
        self.is_sending = True
        self.send_btn.configure(state="disabled", fg_color="#0f766e", text_color=TEXT_DIM)
        threading.Thread(target=self.send_gcode, daemon=True).start()

    def send_gcode(self):
        self.log_to_console("Generating G-code...", "info")
        path = self.generate_gcode()
        self.log_to_console(f"G-code ready -> {path}", "recv")
        self.log_to_console("Connecting to GRBL...", "info")
        self.progress_var.set(0.0)
        self.progress_bar.set(0.0)
        try:
            ser = serial.Serial(self.port_var.get(), 115200, timeout=1)
            time.sleep(2)
            ser.write(b"\r\n\r\n")
            time.sleep(2)
            ser.reset_input_buffer()
            with open(path, "r") as fh:
                lines = [l.strip() for l in fh if l.strip()]
            total = max(len(lines), 1)

            for idx, clean in enumerate(lines):
                if clean.startswith(";COLOUR_SWITCH:"):
                    colour = clean.split(":", 1)[1]
                    self.log_to_console(
                        f"At origin — Z stepper opened the nozzle. Empty out "
                        f"the current colour, then click 'Colour Emptied' to "
                        f"add {colour}.",
                        "info")
                    event = threading.Event()
                    self.root.after(0, self._arm_colour_emptied_button, event)
                    event.wait()
                    self.log_to_console(
                        f"Colour emptied — closing nozzle and adding {colour} "
                        f"now. Resuming in 4s...",
                        "info")
                    time.sleep(4)
                    progress = (idx + 1) / total
                    self.progress_var.set(progress)
                    self.root.after(0, lambda p=progress: (
                        self.progress_bar.set(p),
                        self.sidebar_progress_bar.set(p),
                        self.sidebar_pct_label.config(text=f"{int(p * 100)}%"),
                    ))
                    continue
                self.log_to_console(f"→ {clean}", "send")
                ser.write((clean + "\n").encode())
                while True:
                    res = ser.readline().decode().strip()
                    if res:
                        self.log_to_console(f"← {res}",
                            "recv" if "ok" in res.lower() else "err")
                    if "ok" in res.lower() or "error" in res.lower():
                        break
                progress = (idx + 1) / total
                self.progress_var.set(progress)
                self.root.after(0, lambda p=progress: (
                    self.progress_bar.set(p),
                    self.sidebar_progress_bar.set(p),
                    self.sidebar_pct_label.config(text=f"{int(p * 100)}%"),
                ))

            ser.close()
            self.progress_bar.set(1.0)
            self.sidebar_progress_bar.set(1.0)
            self.root.after(0, lambda: self.sidebar_pct_label.config(text="100%"))
            self.log_to_console("Job complete.", "recv")
        except Exception as e:
            self.log_to_console(f"Connection Error: {e}", "err")
        finally:
            self.is_sending = False
            self.send_btn.configure(state="normal", fg_color="#0d9488", text_color="#ffffff")


if __name__ == "__main__":
    root = tk.Tk()
    app  = ShapeApp(root)
    root.mainloop()
