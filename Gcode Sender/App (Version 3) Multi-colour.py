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

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

#Add Slider for image import tuning.

MAX_X    = 28
MAX_Y    = 28

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
    ("Draw with AI Assistance", "",  ACCENT_CYAN),
    ("Import Designs",          "",  ACCENT_AMBER),
    ("Robot Test",              "",  ACCENT_GREEN),
]


class ShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Rangoli Maker")
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
        self.current_type         = tk.StringVar(value="Pre-designed")
        self.shape_type           = tk.StringVar(value="Select")
        self.feed_rate            = tk.StringVar(value="Medium (default)")
        self.port_var             = tk.StringVar()
        self.size_val             = tk.IntVar(value=50)
        self.is_moving            = False
        self.last_ports           = []
        self.is_sending           = False
        self.hint_popup           = None
        self.hint_after_id        = None
        self.progress_var         = tk.DoubleVar(value=0.0)

        self.multi_colour_var     = tk.BooleanVar(value=False)
        self.shape_colour_var     = tk.StringVar(value=next(iter(COLOUR_PALETTE)))
        self._colour_switch_event = None
        self._pending_colour_event = None

        self._radio_dots   = {}   # mode_label -> (canvas_widget, dot_color)
        self._radio_canvases = {}

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
        self.colour_emptied_btn = self._action_btn(
            btn_wrap, "Colour Emptied", self._on_colour_emptied_click, "#0f766e")
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#0f766e", text_color=TEXT_DIM)
        self.send_btn = self._action_btn(
            btn_wrap, "Send to Rangoli maker", self.start_gcode_streaming, "#0d9488")

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

        # Rangoli Maker in a warm gradient-looking colour using two overlapping labels
        title_c = tk.Canvas(text_fr, bg=BG_PANEL, highlightthickness=0,
                            width=250, height=34)
        title_c.pack(anchor="w")
        # Draw each character manually for a colour sweep effect
        title = "Rangoli Maker"
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
            height=46, corner_radius=0,
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
            row = tk.Frame(body, bg=BG_CARD, pady=2)
            row.pack(fill="x")

            # Custom radio dot canvas
            dot = tk.Canvas(row, width=18, height=18, bg=BG_CARD,
                            highlightthickness=0)
            dot.pack(side="left", padx=(0, 6))
            dot.create_oval(2, 2, 16, 16, fill=BG_INPUT, outline=col,
                            width=2, tags="dot")
            self._radio_dots[label] = (dot, col)

            tk.Label(row, text=label, bg=BG_CARD,
                     fg=TEXT_PRIMARY, font=("Segoe UI", 11, "bold")).pack(side="left")

            # Inline import button for Import Designs row
            if label == "Import Designs":
                imp_btn = ctk.CTkButton(
                    row, text="Browse", command=self.import_design,
                    fg_color="#d97706", hover_color="#b45309",
                    text_color="#ffffff",
                    font=("Segoe UI", 10, "bold"),
                    height=28, width=90, corner_radius=6)
                imp_btn.pack(side="right", padx=(4, 0))

            # Robot Test dropdown — created BEFORE the binding loop so we can
            # exclude it from the click-to-select-mode bindings
            if label == "Robot Test":
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
                self.shape_menu.pack(side="left", padx=(8, 0))

            # Bind row + non-combobox children to mode-select
            row.bind("<Button-1>", lambda e, lbl=label: self._select_mode(lbl))
            dot.bind("<Button-1>",  lambda e, lbl=label: self._select_mode(lbl))
            for child in row.winfo_children():
                if hasattr(self, 'shape_menu') and child is self.shape_menu:
                    continue
                if hasattr(self, '_import_btn') and child is self._import_btn:
                    continue
                child.bind("<Button-1>", lambda e, lbl=label: self._select_mode(lbl))

        # Initialise first dot as selected
        self._select_mode("Pre-designed", silent=True)

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
        self._select_mode("Robot Test", silent=True)
        self.on_shape_type_selected()

    def _on_slider(self, val):
        v = int(float(val))
        self.size_val.set(v)
        self.size_display.config(text=str(v))
        self.update_shape_size(v)

    def _select_mode(self, label, silent=False):
        self.current_type.set(label)
        for lbl, (dot, col) in self._radio_dots.items():
            dot.delete("dot")
            if lbl == label:
                dot.create_oval(2, 2, 16, 16, fill=col, outline=col, tags="dot")
            else:
                dot.create_oval(2, 2, 16, 16, fill=BG_INPUT, outline=col,
                                width=2, tags="dot")
        if not silent and label == "Robot Test":
            self._ai_flash_running = False
            self.canvas.delete("ai_flash")
            self.on_shape_type_selected()
        elif not silent and label == "AI Generated":
            self.show_ai_suggestion_popup()
            self._flash_ai_preview()
        elif not silent:
            self._ai_flash_running = False
            self.canvas.delete("ai_flash")

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

    # ── IMAGE IMPORT ──────────────────────────────────────────────────────────
    def import_design(self):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showerror("Missing Library",
                "Pillow is required.\nRun: pip install Pillow")
            return

        try:
            import cv2
            import numpy as np
        except ImportError:
            messagebox.showerror("Missing Library",
                "opencv-python is required.\nRun: pip install opencv-python")
            return

        path = filedialog.askopenfilename(
            title="Select Design Image",
            filetypes=[("Image Files", "*.jpg *.jpeg *.png *.bmp *.webp")])
        if not path:
            return

        self.log_to_console(f"Loading raw design: {os.path.basename(path)}", "info")

        try:
            pil_img = Image.open(path).convert("L")
        except Exception as e:
            self.log_to_console(f"Error loading image: {e}", "err")
            return

        pil_img  = pil_img.resize((GRAPH_W, GRAPH_H), Image.LANCZOS)
        alpha    = pil_img.point(lambda x: 0 if x > 65 else 255)
        rgba     = Image.new("RGBA", pil_img.size, (0, 0, 0, 0))
        rgba.putalpha(alpha)
        tk_img   = ImageTk.PhotoImage(rgba)
        self.log_to_console("Raw design loaded (black only).", "recv")

        try:
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError("OpenCV could not decode the image.")
        except Exception as e:
            self.log_to_console(f"Error processing image: {e}", "err")
            return

        ih, iw   = img.shape[:2]
        blurred2 = cv2.GaussianBlur(img, (5, 5), 0)
        _, binary = cv2.threshold(blurred2, 65, 255, cv2.THRESH_BINARY_INV)

        close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary  = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k)

        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        self.log_to_console(f"{len(contours)} raw contours found.", "recv")

        def img_to_canvas(iy_px, ix_px):
            cx = MARGIN_L + (ix_px / iw) * GRAPH_W
            cy = MARGIN_T  + (iy_px / ih) * GRAPH_H
            return cx, cy

        MIN_AREA = (iw * ih) * 0.0002

        canvas_paths = []
        for i, cnt in enumerate(contours):
            if hierarchy[0][i][3] != -1:
                continue
            if cv2.contourArea(cnt) < MIN_AREA:
                continue
            epsilon = max(1.5, 0.001 * cv2.arcLength(cnt, closed=True))
            approx  = cv2.approxPolyDP(cnt, epsilon, closed=True)
            if len(approx) < 3:
                continue
            path = [img_to_canvas(int(pt[0][1]), int(pt[0][0])) for pt in approx]
            path.append(path[0])
            canvas_paths.append(path)

        shape = {
            'type':   'Imported',
            'tk_img': tk_img,
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
            f"Imported {len(canvas_paths)} stroke paths ({total_pts} points). "
            f"Ready to generate G-code.", "recv")

    # ── Skeleton → ordered paths ──────────────────────────────────────────────
    def _skeleton_to_paths(self, skeleton, _np):
        import numpy as np

        h, w   = skeleton.shape
        visited = np.zeros((h, w), dtype=bool)
        paths   = []

        ys, xs = np.where(skeleton > 0)
        if len(ys) == 0:
            return paths

        def all_nbrs(r, c):
            result = []
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < h and 0 <= nc < w and skeleton[nr, nc] > 0:
                        result.append((nr, nc))
            return result

        def free_nbrs(r, c):
            return [(nr, nc) for nr, nc in all_nbrs(r, c) if not visited[nr, nc]]

        nbr_counts = {(int(r), int(c)): len(all_nbrs(int(r), int(c))) for r, c in zip(ys, xs)}
        endpoints  = [(r, c) for (r, c), n in nbr_counts.items() if n == 1]
        others     = [(r, c) for (r, c), n in nbr_counts.items() if n != 1]
        candidates = endpoints + others

        for sr, sc in candidates:
            if visited[sr, sc]:
                continue

            path = []
            cur  = (sr, sc)

            while True:
                r, c = cur
                if visited[r, c]:
                    break
                visited[r, c] = True
                path.append((r, c))

                nxt = free_nbrs(r, c)
                if not nxt:
                    break

                if len(path) >= 2:
                    pr, pc = path[-2]
                    dr, dc = r - pr, c - pc
                    def score(n):
                        nr2, nc2 = n
                        return (nr2 - r) * dr + (nc2 - c) * dc
                    nxt.sort(key=score, reverse=True)

                cur = nxt[0]

            if len(path) >= 2:
                paths.append(path)

        return paths

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
        cy = self.canvas.winfo_rooty() + 32
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

    def show_ai_suggestion_popup(self):
        self.root.update_idletasks()
        msg = "AI Suggestion: Try adding flowers outside the petals\nand a diya inside the main circle to make it look even better!"
        w, h = 480, 80
        cx = self.canvas.winfo_rootx() + self.canvas.winfo_width() // 2
        cy = self.canvas.winfo_rooty() + 50
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{w}x{h}+{cx - w//2}+{cy}")
        popup.configure(bg="#0d0b2b")
        glass = tk.Canvas(popup, width=w, height=h, bg="#0d0b2b", highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, w-4, h-4, radius=22,
                                fill="#1d1a4a", outline=ACCENT_PURP, width=2)
        # Shimmery inner highlight line
        self._draw_rounded_rect(glass, 6, 6, w-6, h//2, radius=18,
                                fill="#2d2860", outline="", width=0)
        # Sparkle emoji label
        glass.create_text(28, h//2, text="✨", font=("Segoe UI", 16), fill=ACCENT_PURP)
        glass.create_text(w//2 + 10, h//2, text=msg,
                          fill="#e0d4ff", font=("Segoe UI", 10, "bold"),
                          justify="center")
        self._fade(popup, 0.0, 0.95, 0.08)
        popup.after(5000, lambda: self._fade(popup, 0.95, 0.0, -0.08,
                                             on_done=lambda: self._destroy_popup(popup)))
        # Reuse hint_popup slot so it gets cleaned up properly
        if self.hint_popup:
            try: self.hint_popup.destroy()
            except Exception: pass
        self.hint_popup = popup

    def _flash_ai_preview(self):
        self._ai_flash_running = True
        flowers = [s for s in self.shapes if s['type'] in ('Flower', 'Complex Flower')]
        targets = flowers if flowers else [{'x': MARGIN_L + GRAPH_W // 2,
                                            'y': MARGIN_T  + GRAPH_H // 2,
                                            'size': 120}]
        self._do_flash_shrink(targets, scale=1.0, show=True)

    def _do_flash_shrink(self, targets, scale, show):
        if not getattr(self, '_ai_flash_running', False):
            self.canvas.delete("ai_flash")
            return
        tag = "ai_flash"
        self.canvas.delete(tag)
        if show:
            for s in targets:
                x, y = s['x'], s['y']
                R = (s['size'] / 2) * scale
                self._draw_diya_flash(x, y, max(R * 0.18, 1.5), tag)
                outer_r = R * 1.15
                for i in range(8):
                    a = math.radians(i * 45 + 22.5)
                    self._draw_mini_flower_flash(
                        x + outer_r * math.cos(a),
                        y + outer_r * math.sin(a),
                        max(R * 0.12, 1.5), tag)
        next_scale = max(scale * 0.965, 0.08)
        self.root.after(280, lambda: self._do_flash_shrink(targets, next_scale, not show))

    def _draw_diya_flash(self, cx, cy, r, tag):
        self.canvas.create_arc(cx - r, cy - r * 0.5, cx + r, cy + r,
                               start=0, extent=-180,
                               fill="#f97316", outline="#ffd700", width=1, tags=tag)
        self.canvas.create_polygon(
            [cx + r * 0.7, cy, cx + r * 1.2, cy - r * 0.3, cx + r * 0.9, cy + r * 0.2],
            fill="#f97316", outline="", tags=tag)
        self.canvas.create_oval(cx - r * 0.45, cy - r * 2.4,
                                cx + r * 0.45, cy - r * 0.7,
                                fill="#fde68a", outline="", tags=tag)
        self.canvas.create_oval(cx - r * 0.22, cy - r * 2.0,
                                cx + r * 0.22, cy - r * 0.9,
                                fill="#ffd700", outline="", tags=tag)
        self.canvas.create_oval(cx - r * 0.1, cy - r * 2.3,
                                cx + r * 0.1, cy - r * 1.5,
                                fill="#ff4d6d", outline="", tags=tag)

    def _draw_mini_flower_flash(self, cx, cy, r, tag):
        petal_cols = ["#f9a825", "#ec4899", "#a855f7", "#06b6d4", "#10b981"]
        for i in range(5):
            a  = math.radians(i * 72)
            px = cx + r * 0.75 * math.cos(a)
            py = cy + r * 0.75 * math.sin(a)
            pr = r * 0.5
            self.canvas.create_oval(px - pr, py - pr, px + pr, py + pr,
                                    fill=petal_cols[i], outline="", tags=tag)
        self.canvas.create_oval(cx - r * 0.35, cy - r * 0.35,
                                cx + r * 0.35, cy + r * 0.35,
                                fill="#ffd700", outline="", tags=tag)

    def _arm_colour_emptied_button(self, event):
        # No popup — just enable the sidebar button and hold the streaming
        # thread until the user confirms the nozzle has drained.
        self._pending_colour_event = event
        self.colour_emptied_btn.configure(
            state="normal", fg_color="#0d9488", text_color="#ffffff")

    def _on_colour_emptied_click(self):
        event = self._pending_colour_event
        if event is None:
            return
        self._pending_colour_event = None
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#0f766e", text_color=TEXT_DIM)
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

    # ── Canvas interactions ───────────────────────────────────────────────────
    def on_canvas_click(self, event):
        if self.is_moving:
            self.is_moving = False
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
    }
    _SELECTED_COLOR = "#ec4899"

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

            if selected:
                self.canvas.create_oval(x-4, y-4, x+4, y+4,
                                        fill=self._SELECTED_COLOR, outline="#ffffff",
                                        width=1, tags="shape")

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
        self.log_to_console("Canvas cleared.", "info")

    def _shape_paths(self, s):
        if s['type'] == 'Imported':
            return s['paths']
        elif s['type'] == "Complex Flower":
            return self._complex_flower_paths(s['x'], s['y'], s['size'])
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
                # Return to origin, lift the tool, and open the nozzle
                # (M8) so the currently loaded colour can drain out before
                # the next one is added. M9 (close nozzle) is only reached
                # once the app resumes streaming after the colour is added.
                lines += [f"G1 Z0.00 F{f}", "G1 X0 Y0", "M5", "M8",
                          f";COLOUR_SWITCH:{colour}", "M9"]
                for paths in groups[colour]:
                    lines += self._paths_gcode_lines(paths, f)
        else:
            for s in self.shapes:
                lines += self._shape_gcode_lines(s, f)

        lines += [f"G1 Z0.00 F{f}", "G1 X0 Y0"]
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
                        f"At origin — nozzle open. Empty out the current "
                        f"colour, then click 'Colour Emptied' to add {colour}.",
                        "info")
                    event = threading.Event()
                    self.root.after(0, self._arm_colour_emptied_button, event)
                    event.wait()
                    self.log_to_console(
                        f"Colour emptied — add {colour} now. Resuming in 4s...",
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
