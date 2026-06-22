import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import serial
import serial.tools.list_ports
import math
import os
import time
import threading

MAX_X    = 28    # mm
MAX_Y    = 28    # mm

MARGIN_L = 50
MARGIN_B = 40
MARGIN_T = 10
MARGIN_R = 10
GRAPH_W  = 680
GRAPH_H  = int(GRAPH_W * MAX_Y / MAX_X)
CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

BG_DARK      = "#0f0f17"
BG_PANEL     = "#16161f"
BG_CARD      = "#1e1e2e"
BG_INPUT     = "#252535"
ACCENT_BLUE  = "#4dabf7"
ACCENT_CYAN  = "#22d3ee"
ACCENT_GREEN = "#2ecc71"
ACCENT_AMBER = "#f59e0b"
ACCENT_PINK  = "#f472b6"
ACCENT_PURP  = "#a78bfa"
TEXT_PRIMARY = "#e2e8f0"
TEXT_DIM     = "#94a3b8"
ORIGIN_RED   = "#ff4d6d"


class ShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ProLabs CNC Design & Control Center")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.configure(bg=BG_DARK)

        self.setup_styles()

        self.shapes               = []
        self.selected_shape_index = None
        self.current_type         = tk.StringVar(value="Pre-designed")
        self.shape_type           = tk.StringVar(value="Select")
        self.feed_rate            = tk.StringVar(value="200")
        self.port_var             = tk.StringVar()
        self.size_val             = tk.IntVar(value=50)
        self.is_moving            = False
        self.last_ports           = []
        self.is_sending           = False
        self.hint_popup           = None
        self.hint_after_id        = None

        self.setup_ui()
        self.setup_context_menu()
        self.poll_ports()

    # ── Styles ────────────────────────────────────────────────────────────────
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG_CARD, foreground=TEXT_PRIMARY, fieldbackground=BG_INPUT)
        style.configure("TLabel", background=BG_CARD, foreground=TEXT_PRIMARY, font=("Segoe UI", 10, "bold"))
        style.configure("TCombobox", arrowcolor=ACCENT_BLUE, font=("Segoe UI", 10))
        style.map("TCombobox",
            fieldbackground=[("readonly", BG_INPUT)],
            foreground=[("readonly", TEXT_PRIMARY)])

    # ── Main UI ───────────────────────────────────────────────────────────────
    def setup_ui(self):
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill="both", expand=True, padx=16, pady=16)

        # Left sidebar
        left = tk.Frame(main, bg=BG_DARK, width=300)
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)

        banner = tk.Frame(left, bg=BG_PANEL)
        banner.pack(fill="x", pady=(0, 10))
        tk.Label(banner, text="⬡  PROLABS", bg=BG_PANEL, fg=ACCENT_CYAN,
                 font=("Segoe UI", 15, "bold")).pack(side="left", padx=12, pady=10)
        tk.Label(banner, text="CNC CENTER", bg=BG_PANEL, fg=ACCENT_PURP,
                 font=("Segoe UI", 9, "bold")).pack(side="left")

        self._section(left, "◉  CONNECTION",    ACCENT_BLUE, self._build_connection)
        self._section(left, "✦  DESIGN OPTIONS", ACCENT_PURP, self._build_design)
        self._section(left, "◎  NOZZLE STATUS",  ACCENT_AMBER, self._build_nozzle)

        actions = tk.Frame(left, bg=BG_DARK)
        actions.pack(fill="x", side="bottom", pady=(8, 0))

        # ── Import Design button ──────────────────────────────────────────
        self._action_btn(actions, "📂  Import Design",  self.import_design,          ACCENT_PURP,  "#130e1f")
        self._action_btn(actions, "🗑  Clear Canvas",   self.clear_canvas,            ACCENT_PINK,  "#1e0e16")
        self.send_btn = self._action_btn(
            actions, "▶  Send to GRBL", self.start_gcode_streaming, ACCENT_GREEN, "#0a1f12")

        # Right pane
        right = tk.Frame(main, bg=BG_DARK)
        right.pack(side="right", fill="both", expand=True)

        canvas_wrap = tk.Frame(right, bg=ACCENT_CYAN, bd=1)
        canvas_wrap.pack(anchor="ne", padx=8, pady=8)

        self.canvas = tk.Canvas(canvas_wrap, width=CANVAS_W, height=CANVAS_H,
                                bg="#0b1120", highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-2>", self.on_right_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Motion>",   self.on_mouse_move)
        self.draw_grid()

        self.coord_label = tk.Label(right, text="X: 0.00  Y: 0.00",
                                    bg=BG_DARK, fg=ACCENT_AMBER,
                                    font=("Consolas", 10, "bold"))
        self.coord_label.pack(anchor="w", padx=16, pady=(0, 4))

        con_wrap = tk.Frame(right, bg=BG_PANEL)
        con_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        hdr = tk.Frame(con_wrap, bg=BG_PANEL)
        hdr.pack(fill="x")
        tk.Label(hdr, text="▸  REAL-TIME GRBL CONSOLE", bg=BG_PANEL, fg=ACCENT_CYAN,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=10, pady=6)
        tk.Button(hdr, text="✕ Clear", bg=BG_PANEL, fg=TEXT_DIM, bd=0,
                  font=("Segoe UI", 9), activebackground=BG_PANEL,
                  command=lambda: self.console.delete("1.0", tk.END)).pack(side="right", padx=10)

        self.console = tk.Text(con_wrap, bg="#080c14", fg="#a8d8a8",
                               font=("Consolas", 10), bd=0, highlightthickness=0,
                               height=8, insertbackground=ACCENT_GREEN)
        self.console.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        self.console.tag_config("send", foreground=ACCENT_CYAN)
        self.console.tag_config("recv", foreground=ACCENT_GREEN)
        self.console.tag_config("err",  foreground=ACCENT_PINK)
        self.console.tag_config("info", foreground=ACCENT_AMBER)

    # ── UI helpers ────────────────────────────────────────────────────────────
    def _section(self, parent, title, accent, builder):
        outer = tk.Frame(parent, bg=accent)
        outer.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(outer, bg=BG_CARD)
        inner.pack(fill="both", padx=1, pady=(0, 1))
        tk.Label(inner, text=title, bg=BG_CARD, fg=accent,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))
        body = tk.Frame(inner, bg=BG_CARD)
        body.pack(fill="x", padx=10, pady=(0, 10))
        builder(body, accent)

    def _action_btn(self, parent, text, cmd, fg, bg_dark):
        btn = tk.Button(parent, text=text, command=cmd,
                        bg=bg_dark, fg=fg, activebackground=bg_dark, activeforeground=fg,
                        font=("Segoe UI", 10, "bold"), bd=0, pady=9, cursor="hand2", relief="flat")
        btn.pack(fill="x", pady=3)
        btn.bind("<Enter>", lambda e: btn.config(bg=fg,     fg="#0a0a0f"))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg_dark, fg=fg))
        return btn

    def _label(self, parent, text, fg=TEXT_DIM):
        tk.Label(parent, text=text, bg=BG_CARD, fg=fg,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 1))

    def _combo(self, parent, var, values, accent, **kw):
        cb = ttk.Combobox(parent, textvariable=var, values=values,
                          state="readonly", font=("Segoe UI", 10), **kw)
        cb.pack(fill="x", pady=3)
        return cb

    # ── Section builders ──────────────────────────────────────────────────────
    def _build_connection(self, body, accent):
        self._label(body, "Serial Port:", ACCENT_BLUE)
        self.port_menu = self._combo(body, self.port_var, [], accent)

    def _build_design(self, body, accent):
        modes = [
            ("Pre-designed",            ACCENT_BLUE),
            ("AI Generated",            ACCENT_PURP),
            ("Draw with AI Assistance", ACCENT_CYAN),
            ("Import Designs",          ACCENT_AMBER),
            ("Robot Test",              ACCENT_GREEN),
        ]
        for s, col in modes:
            row = tk.Frame(body, bg=BG_CARD)
            row.pack(fill="x", pady=3)
            dot = tk.Canvas(row, width=14, height=14, bg=BG_CARD, highlightthickness=0)
            dot.pack(side="left", padx=(0, 6))
            dot.create_oval(2, 2, 12, 12, fill=BG_INPUT, outline=col, width=2, tags="dot")
            rb = tk.Radiobutton(
                row, text=s, variable=self.current_type, value=s,
                bg=BG_CARD, fg=TEXT_PRIMARY, selectcolor=BG_CARD,
                activebackground=BG_CARD, activeforeground=col,
                font=("Segoe UI", 9, "bold"), bd=0,
                command=lambda c=col, d=dot: self._radio_update(c, d))
            rb.pack(side="left")
            if s == "Robot Test":
                self.shape_menu = ttk.Combobox(
                    row, textvariable=self.shape_type, width=11, state="readonly",
                    values=["Select", "Square", "Rectangle", "Circle", "Triangle", "Flower", "Complex Flower"])
                self.shape_menu.pack(side="left", padx=(8, 0))
                self.shape_menu.bind("<<ComboboxSelected>>", self.on_shape_type_selected)

        self._label(body, "Size:", ACCENT_CYAN)
        self.size_slider = tk.Scale(
            body, from_=1, to=800, orient="horizontal",
            variable=self.size_val, command=self.update_shape_size,
            bg=BG_CARD, fg=ACCENT_CYAN, troughcolor=BG_INPUT,
            highlightthickness=0, activebackground=ACCENT_CYAN, font=("Segoe UI", 9))
        self.size_slider.pack(fill="x", pady=4)

        self._label(body, "Speed (mm/min):", ACCENT_AMBER)
        self._combo(body, self.feed_rate, ["100", "150", "200", "250", "300", "350"], accent)

    def _radio_update(self, col, dot_canvas):
        dot_canvas.delete("dot")
        dot_canvas.create_oval(2, 2, 12, 12, fill=col, outline=col, tags="dot")

    def _build_nozzle(self, body, accent):
        self.tool_status_box = tk.Label(body, text="Tool: OFF", width=18,
                                        font=("Segoe UI", 12, "bold"),
                                        bg=ORIGIN_RED, fg="#ffffff", pady=8)
        self.tool_status_box.pack(fill="x", pady=4)

    # ── Tool / console ────────────────────────────────────────────────────────
    def update_tool_box(self, status_text):
        if "ON" in status_text:
            self.tool_status_box.config(text=status_text, bg=ACCENT_GREEN, fg="#000000")
        else:
            self.tool_status_box.config(text=status_text, bg=ORIGIN_RED, fg="#ffffff")

    def log_to_console(self, msg, tag="info"):
        self.console.insert(tk.END, msg + "\n", tag)
        self.console.see(tk.END)

    # ── Context menu ──────────────────────────────────────────────────────────
    def setup_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=TEXT_PRIMARY,
                                    activebackground=ACCENT_BLUE, activeforeground=BG_DARK,
                                    font=("Segoe UI", 10))
        self.context_menu.add_command(label="✥  Move",   command=self.start_move)
        self.context_menu.add_command(label="✕  Delete", command=self.delete_shape)

    # ── Port polling ──────────────────────────────────────────────────────────
    def poll_ports(self):
        current_ports = [p.device for p in serial.tools.list_ports.comports()]
        if current_ports != self.last_ports:
            self.port_menu['values'] = current_ports
            if current_ports:
                new = list(set(current_ports) - set(self.last_ports))
                self.port_var.set(new[0] if new else
                                  (current_ports[0] if self.port_var.get() not in current_ports else self.port_var.get()))
            else:
                self.port_var.set("")
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

        c.create_rectangle(x0, y0, x1, y1, fill="#0b1120", outline="", tags="grid")

        for ix in range(0, MAX_X + 1, 5):
            px = x0 + (ix / MAX_X) * GRAPH_W
            c.create_line(px, y0, px, y1, fill="#141e2e", width=1, tags="grid")
        for iy in range(0, MAX_Y + 1, 5):
            py = y1 - (iy / MAX_Y) * GRAPH_H
            c.create_line(x0, py, x1, py, fill="#141e2e", width=1, tags="grid")

        for ix in range(0, MAX_X + 1, 10):
            px = x0 + (ix / MAX_X) * GRAPH_W
            c.create_line(px, y0, px, y1, fill="#1e3050", width=1, tags="grid")
            c.create_text(px, y1 + 14, text=str(ix), fill="#4a6080", font=("Consolas", 8), tags="grid")
            c.create_line(px, y1, px, y1 + 5, fill="#304060", width=1, tags="grid")

        for iy in range(0, MAX_Y + 1, 10):
            py = y1 - (iy / MAX_Y) * GRAPH_H
            c.create_line(x0, py, x1, py, fill="#1e3050", width=1, tags="grid")
            c.create_text(x0 - 8, py, text=str(iy), fill="#4a6080", font=("Consolas", 8), anchor="e", tags="grid")
            c.create_line(x0 - 5, py, x0, py, fill="#304060", width=1, tags="grid")

        c.create_line(x0, y1, x1, y1, fill=ORIGIN_RED, width=2, tags="grid")
        c.create_line(x0, y0, x0, y1, fill=ORIGIN_RED, width=2, tags="grid")
        c.create_text((x0 + x1) // 2, CANVAS_H - 4, text="X (mm)", fill=ACCENT_BLUE,
                      font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(10, (y0 + y1) // 2,      text="Y",     fill=ACCENT_GREEN, font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(10, (y0 + y1) // 2 - 14, text="(mm)",  fill=ACCENT_GREEN, font=("Segoe UI", 7),         tags="grid")

        ox, oy = x0, y1
        for hr, hcol in [(22, "#3a000e"), (15, "#7a0020"), (10, "#cc0035")]:
            c.create_oval(ox-hr, oy-hr, ox+hr, oy+hr, fill="", outline=hcol, width=1, tags="grid")
        arm, ah = 40, 6
        c.create_line(ox, oy-12, ox, oy-arm, fill=ORIGIN_RED, width=2, tags="grid")
        c.create_polygon(ox-ah, oy-arm+ah*2, ox+ah, oy-arm+ah*2, ox, oy-arm,
                         fill=ORIGIN_RED, outline="", tags="grid")
        c.create_line(ox+12, oy, ox+arm, oy, fill=ORIGIN_RED, width=2, tags="grid")
        c.create_polygon(ox+arm-ah*2, oy-ah, ox+arm-ah*2, oy+ah, ox+arm, oy,
                         fill=ORIGIN_RED, outline="", tags="grid")
        r = 7
        c.create_oval(ox-r, oy-r, ox+r, oy+r, fill=ORIGIN_RED, outline="#ffffff", width=2, tags="grid")
        c.create_oval(ox-3, oy-3, ox+3, oy+3, fill="#ffffff", outline="", tags="grid")
        c.create_text(ox-4, oy+16, text="(0,0)", fill=ORIGIN_RED, font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(ox+arm+14, oy-6,   text="X+", fill=ACCENT_BLUE,  font=("Segoe UI", 8, "bold"), tags="grid")
        c.create_text(ox+14, oy-arm-8,   text="Y+", fill=ACCENT_GREEN, font=("Segoe UI", 8, "bold"), tags="grid")

    # ── IMAGE IMPORT ──────────────────────────────────────────────────────────
    def import_design(self):
        """Open file dialog, display raw design on canvas, then extract skeleton paths."""
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

        self.log_to_console(f"📂  Loading raw design: {os.path.basename(path)}", "info")

        # ── Step 1: Import raw design — black pixels only (PIL) ──────────
        try:
            pil_img = Image.open(path).convert("L")   # grayscale
        except Exception as e:
            self.log_to_console(f"Error loading image: {e}", "err")
            return

        # Threshold: dark pixels (design) → opaque black; light → transparent
        pil_img  = pil_img.resize((GRAPH_W, GRAPH_H), Image.LANCZOS)
        alpha    = pil_img.point(lambda x: 0 if x > 65 else 255)  # 0=transparent, 255=opaque
        rgba     = Image.new("RGBA", pil_img.size, (0, 0, 0, 0))
        rgba.putalpha(alpha)
        tk_img   = ImageTk.PhotoImage(rgba)
        self.log_to_console("✓  Raw design loaded (black only).", "recv")

        # ── Step 2: Contour extraction from binary mask (OpenCV) ─────────────
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

        # Close small gaps so strokes are solid filled regions
        close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        binary  = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k)

        # RETR_CCOMP level-0 contours: one outer boundary per connected region,
        # no double-edges from thick strokes, no fragmentation from skeleton junctions
        contours, hierarchy = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        self.log_to_console(f"✓  {len(contours)} raw contours found.", "recv")

        def img_to_canvas(iy_px, ix_px):
            cx = MARGIN_L + (ix_px / iw) * GRAPH_W
            cy = MARGIN_T  + (iy_px / ih) * GRAPH_H
            return cx, cy

        MIN_AREA = (iw * ih) * 0.0002   # filter image-border noise and tiny specks

        canvas_paths = []
        for i, cnt in enumerate(contours):
            # Skip holes (level-1 in CCOMP hierarchy) — keep only outer boundaries
            if hierarchy[0][i][3] != -1:
                continue
            if cv2.contourArea(cnt) < MIN_AREA:
                continue
            # Epsilon scales with perimeter so detail is preserved proportionally
            epsilon = max(1.5, 0.001 * cv2.arcLength(cnt, closed=True))
            approx  = cv2.approxPolyDP(cnt, epsilon, closed=True)
            if len(approx) < 3:
                continue
            path = [img_to_canvas(int(pt[0][1]), int(pt[0][0])) for pt in approx]
            path.append(path[0])   # close the contour
            canvas_paths.append(path)

        # ── Step 4: Add shape with raw image + paths ──────────────────────
        shape = {
            'type':   'Imported',
            'tk_img': tk_img,               # raw display — kept alive here
            'paths':  canvas_paths,         # for G-code generation
            'x':      MARGIN_L + GRAPH_W // 2,
            'y':      MARGIN_T  + GRAPH_H // 2,
            'size':   0,
        }
        self.shapes.append(shape)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

        total_pts = sum(len(p) for p in canvas_paths)
        self.log_to_console(
            f"✓  Imported {len(canvas_paths)} stroke paths ({total_pts} points). "
            f"Ready to generate G-code.", "recv")

    # ── Skeleton → ordered paths ──────────────────────────────────────────────
    def _skeleton_to_paths(self, skeleton, _np):
        """
        Convert a skeleton (thinned binary image) into a list of ordered
        pixel-coordinate paths.  Each path is a list of (row, col) tuples.
        """
        import numpy as np

        h, w   = skeleton.shape
        visited = np.zeros((h, w), dtype=bool)
        paths   = []

        # Pre-compute neighbour count for every skeleton pixel
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

        # Sort start candidates: endpoints first (1 neighbour → natural stroke starts)
        nbr_counts = {(int(r), int(c)): len(all_nbrs(int(r), int(c))) for r, c in zip(ys, xs)}
        endpoints  = [(r, c) for (r, c), n in nbr_counts.items() if n == 1]
        others     = [(r, c) for (r, c), n in nbr_counts.items() if n != 1]
        candidates = endpoints + others

        for sr, sc in candidates:
            if visited[sr, sc]:
                continue

            # Walk greedily along the skeleton
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

                # Prefer the neighbour that continues most linearly
                if len(path) >= 2:
                    pr, pc = path[-2]
                    dr, dc = r - pr, c - pc
                    # Score each candidate by alignment with current direction
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
        glass.create_text(w//2, h//2, text=f"✦  {message}",
                          fill=ACCENT_CYAN, font=("Segoe UI", 10, "bold"))
        self.hint_popup = popup
        self._fade(popup, 0.0, 0.95, 0.08)
        self.hint_after_id = self.root.after(4500, self.hide_hint_popup)

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
        self.shapes.append({'type': self.shape_type.get(),
                            'x': event.x, 'y': event.y,
                            'size': self.size_val.get()})
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
            # If imported, shift all paths too
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
            self.redraw()

    def update_shape_size(self, val):
        if self.selected_shape_index is not None and not self.is_moving:
            s = self.shapes[self.selected_shape_index]
            if s['type'] != 'Imported':      # size doesn't apply to imports
                s['size'] = int(val)
                self.redraw()

    # ── Shape colours ─────────────────────────────────────────────────────────
    _SHAPE_COLORS = {
        "Square":    ACCENT_BLUE,
        "Rectangle": ACCENT_PURP,
        "Circle":    ACCENT_CYAN,
        "Triangle":  ACCENT_AMBER,
        "Flower":        ACCENT_PINK,
        "Complex Flower": "#ffd700",
        "Imported":  ACCENT_GREEN,
    }

    def redraw(self):
        self.canvas.delete("shape")
        for i, s in enumerate(self.shapes):
            selected = (i == self.selected_shape_index)
            col = "#ffffff" if selected else self._SHAPE_COLORS.get(s['type'], ACCENT_BLUE)
            lw  = 2 if selected else 1

            if s['type'] == 'Imported':
                for path in s['paths']:
                    if len(path) < 2:
                        continue
                    flat = [coord for pt in path for coord in pt]
                    self.canvas.create_line(flat, fill=col, width=lw,
                                            smooth=True, tags="shape")
                if selected:
                    all_x = [cx for path in s['paths'] for cx, _ in path]
                    all_y = [cy for path in s['paths'] for _, cy in path]
                    if all_x:
                        self.canvas.create_rectangle(
                            min(all_x)-4, min(all_y)-4, max(all_x)+4, max(all_y)+4,
                            outline=ACCENT_GREEN, width=1, dash=(4, 4), tags="shape")
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
                    self.canvas.create_line(flat, fill=col, width=lw, tags="shape", smooth=True)
            elif s['type'] == "Complex Flower":
                self._draw_complex_flower(x, y, sz, col, lw)

            if selected:
                self.canvas.create_oval(x-4, y-4, x+4, y+4,
                                        fill=col, outline="", tags="shape")


    def _complex_flower_paths(self, x, y, sz):
        """8 pointed leaf petals + 1 centre circle. Clean and simple."""
        R = sz / 2
        paths = []

        def _circle(cx, cy, r, n=64):
            return [(cx + r * math.cos(math.radians(i * 360 / n)),
                     cy + r * math.sin(math.radians(i * 360 / n)))
                    for i in range(n + 1)]

        # ── 8 pointed leaf petals ────────────────────────────────────────
        petal_outer = R * 1.30   # outer tip — longer
        petal_inner = R * 0.40   # inner tip = circle radius → petals just touch circle
        petal_d     = (petal_outer + petal_inner) / 2   # 0.85R midpoint
        petal_l     = (petal_outer - petal_inner) / 2   # 0.45R half-length (longer)
        petal_w     = R * 0.16                           # half-width — kept narrow (not wider)

        steps = 80
        for p in range(8):
            angle = math.radians(p * 45)
            pts   = []
            for i in range(steps + 1):
                t  = math.radians(i * 360 / steps)
                lx = petal_l * math.cos(t)
                ly = petal_w * math.sin(t) * abs(math.sin(t))  # tapers to true point
                rx = lx * math.cos(angle) - ly * math.sin(angle)
                ry = lx * math.sin(angle) + ly * math.cos(angle)
                pts.append((x + petal_d * math.cos(angle) + rx,
                            y + petal_d * math.sin(angle) + ry))
            paths.append(pts)

        # ── Single centre circle ─────────────────────────────────────────
        paths.append(_circle(x, y, R * 0.40))

        return paths


    def _draw_complex_flower(self, x, y, sz, col, lw):
        paths = self._complex_flower_paths(x, y, sz)
        for idx, path in enumerate(paths):
            flat = [c for pt in path for c in pt]
            if len(flat) < 4:
                continue
            if idx < 8:
                # Petals — closed polygon gives genuine sharp pointed tips
                self.canvas.create_polygon(flat, outline=col, fill="",
                                           width=lw, smooth=True, tags="shape")
            else:
                # Centre circle
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
        # Complex Flower returns None — handled separately in generate_gcode
        return res

    def clear_canvas(self):
        self.shapes = []
        self.selected_shape_index = None
        self.canvas.delete("shape")
        self.log_to_console("Canvas cleared.", "info")

    def generate_gcode(self):
        f     = self.feed_rate.get()
        lines = ["$X", "G21", "G90", f"F{f}"]

        for s in self.shapes:
            if s['type'] == 'Imported':
                # Each disconnected stroke path = separate pen-down segment
                for path in s['paths']:
                    if len(path) < 2:
                        continue
                    # Travel to start of path (pen up)
                    mx, my = self.to_machine(*path[0])
                    lines += [
                        f"G1 Z0.00 F{f}",
                        f"G1 X{mx:.2f} Y{my:.2f} F{f}",
                        "M3",
                        f"G1 Z0.05 F{f}",
                    ]
                    for cx, cy in path[1:]:
                        mx, my = self.to_machine(cx, cy)
                        lines.append(f"G1 X{mx:.2f} Y{my:.2f} F{f}")
                    lines.append("M5")
            elif s['type'] == "Complex Flower":
                for path in self._complex_flower_paths(s['x'], s['y'], s['size']):
                    if len(path) < 2:
                        continue
                    mx, my = self.to_machine(*path[0])
                    lines += [f"G1 Z0.00 F{f}",
                              f"G1 X{mx:.2f} Y{my:.2f} F{f}",
                              "M3", f"G1 Z0.05 F{f}"]
                    for px, py in path[1:]:
                        mx, my = self.to_machine(px, py)
                        lines.append(f"G1 X{mx:.2f} Y{my:.2f} F{f}")
                    lines.append("M5")
            else:
                coords = self.get_shape_coords(s)
                if not coords:
                    continue
                mx, my = self.to_machine(*coords[0])
                lines += [
                    f"G1 Z0.00 F{f}",
                    f"G1 X{mx:.2f} F{f}",
                    f"G1 Y{my:.2f} F{f}",
                    "M3",
                    f"G1 Z0.05 F{f}",
                ]
                for px, py in coords[1:]:
                    mx, my = self.to_machine(px, py)
                    lines.append(f"G1 X{mx:.2f} Y{my:.2f} F{f}")
                lines.append("M5")

        lines += [f"G1 Z0.00 F{f}", "G1 X0 Y0"]
        path_out = os.path.expanduser("~/Downloads/design.gcode")
        with open(path_out, "w") as fh:
            fh.write("\n".join(lines))
        self.log_to_console(f"G-code saved → {path_out}", "info")
        return path_out

    # ── GRBL streaming ────────────────────────────────────────────────────────
    def start_gcode_streaming(self):
        if self.is_sending: return
        if not self.port_var.get():
            self.log_to_console("Error: Choose a serial port first.", "err")
            return
        self.is_sending = True
        self.send_btn.config(state="disabled", bg="#0a1f12", fg=TEXT_DIM)
        threading.Thread(target=self.send_gcode, daemon=True).start()

    def send_gcode(self):
        self.log_to_console("⚙  Generating G-code...", "info")
        path = self.generate_gcode()
        self.log_to_console(f"✓  G-code ready → {path}", "recv")
        self.log_to_console("▶  Connecting to GRBL...", "info")
        try:
            ser = serial.Serial(self.port_var.get(), 115200, timeout=1)
            time.sleep(2)
            ser.write(b"\r\n\r\n")
            time.sleep(2)
            ser.reset_input_buffer()
            self.update_tool_box("Tool: OFF")

            with open(path, "r") as fh:
                for line in fh:
                    clean = line.strip()
                    if not clean: continue
                    if clean == "M3": self.update_tool_box("Tool: ON")
                    elif clean == "M5": self.update_tool_box("Tool: OFF")
                    self.log_to_console(f"→ {clean}", "send")
                    ser.write((clean + "\n").encode())
                    while True:
                        res = ser.readline().decode().strip()
                        if res:
                            self.log_to_console(f"← {res}", "recv" if "ok" in res.lower() else "err")
                        if "ok" in res.lower() or "error" in res.lower():
                            break

            ser.close()
            self.log_to_console("✓ Job complete.", "recv")
        except Exception as e:
            self.log_to_console(f"Connection Error: {e}", "err")
        finally:
            self.is_sending = False
            self.send_btn.config(state="normal", bg="#0a1f12", fg=ACCENT_GREEN)


if __name__ == "__main__":
    root = tk.Tk()
    app  = ShapeApp(root)
    root.mainloop()
