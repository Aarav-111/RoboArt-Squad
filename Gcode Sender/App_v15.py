import copy
import tkinter as tk
import tkinter.font as tkfont
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
import datetime
import urllib.request
import urllib.error
import base64
import hashlib
import shutil
import zlib

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

def _patch_ctk_button_full_click():
    """Make CTkButton clicks reliable on macOS / popup overlays.

    CTk only runs the command on <ButtonRelease-1> when ``_mouse_inside``
    is True, and that flag is set only by <Enter>. If a popup appears under
    the cursor (common for design-options), Enter never fires and the first
    click is ignored — buttons feel finicky / half-working.

    Fix: on release, decide "inside" from the real pointer position over the
    button's screen geometry instead of relying on <Enter>. Still cancels the
    click if the user presses then drags off the button.
    """
    import tkinter as _tk

    def _pointer_over_button(self):
        try:
            px, py = self.winfo_pointerxy()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            w, h = self.winfo_width(), self.winfo_height()
            return x <= px < x + w and y <= py < y + h
        except _tk.TclError:
            return False

    def _on_release(self, event=None):
        if self._state == _tk.DISABLED:
            return
        if not _pointer_over_button(self):
            self._mouse_inside = False
            return
        self._mouse_inside = True
        # click animation (same as stock CTk)
        self._on_leave()
        self._click_animation_running = True
        self.after(100, self._click_animation)
        if self._command is not None:
            self._command()

    ctk.CTkButton._on_release = _on_release


def _patch_ctk_scaling_alpha():
    """Stop the whole app from going see-through on Windows.

    CustomTkinter's ScalingTracker watches every window that contains a CTk
    widget. When it notices the monitor's DPI scaling has changed it does:

        window.attributes("-alpha", 0.15)     # hide the resize flicker
        window.block_update_dimensions_event()
        ...rescale...
        window.attributes("-alpha", 1)        # put it back

    Our windows are plain tk.Tk / tk.Toplevel with CTk widgets inside them,
    and ``block_update_dimensions_event`` only exists on ctk.CTk. On a plain
    tk window tkinter forwards the unknown attribute to the interpreter and
    raises AttributeError — *after* the window has been faded to 15% and
    before it is faded back. The app is left fully working but almost
    invisible, and nothing ever restores it.

    It fires when a CTkToplevel (e.g. the Kolam Notebook name prompt) makes
    the tracker re-measure the DPI. Two guards:

      1. Give plain tk windows the two no-op methods the tracker expects.
      2. Wrap the tracker's check so that whatever goes wrong inside it, no
         tracked window is ever left faded out.
    """
    import tkinter as _tk
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker \
            import ScalingTracker
    except Exception:
        return

    for _cls in (_tk.Tk, _tk.Toplevel):
        for _name in ("block_update_dimensions_event",
                      "unblock_update_dimensions_event"):
            if not hasattr(_cls, _name):
                setattr(_cls, _name, lambda self: None)

    _orig_check = ScalingTracker.check_dpi_scaling

    def _safe_check_dpi_scaling(cls=ScalingTracker):
        try:
            _orig_check()
        finally:
            # Belt and braces: even if a future CustomTkinter breaks somewhere
            # else mid-fade, never leave a window the user cannot see. Only
            # 0.15 is undone — that is the tracker's own value, so this can
            # never fight our popups' deliberate fade-ins.
            for window in list(cls.window_widgets_dict):
                try:
                    if window.winfo_exists() and \
                            abs(float(window.attributes("-alpha")) - 0.15) < 1e-6:
                        window.attributes("-alpha", 1)
                except Exception:
                    pass

    ScalingTracker.check_dpi_scaling = staticmethod(_safe_check_dpi_scaling)


_patch_ctk_button_full_click()
_patch_ctk_scaling_alpha()

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

AI_KEY_FILE = os.path.join(_APP_DIR, "openai_key.txt")

# Learn Mode opens with a silent "how to make a rangoli" clip, decoded frame
# by frame with OpenCV and shown inside the Learn Mode popup.
LEARN_VIDEO_FILE = os.path.join(_APP_DIR, "learn_intro.mov")

# Learn Mode closes with a USB camera photographing the finished rangoli on the
# floor. No camera is mounted on the rig yet, so only the *install* half is
# built: scan the USB video devices, let the user pick one, test it, and
# remember the choice. The capture itself runs off whatever this installs.
CAMERA_CONFIG_FILE = os.path.join(_APP_DIR, "camera_config.json")
LEARN_PHOTO_DIR    = os.path.join(_APP_DIR, "learn_photos")
CAMERA_PROBE_COUNT = 6      # device indices tried by a scan (0 … N-1)
CAMERA_WARMUP_FRAMES = 3    # USB webcams hand back a black first frame or two

# Learn Mode remembers how good the child has actually got. The profile holds
# every scored attempt plus the level derived from them, so difficulty carries
# over between runs instead of restarting at "hold my hand" every session.
# Deliberately a separate file from App_v10's learn_progress.json: that one is
# the Progress & Impact history and has its own schema, and two apps rewriting
# one file would each silently drop the other's keys.
LEARNER_PROFILE_FILE = os.path.join(_APP_DIR, "learner_profile.json")

# ── Kid Mode ───────────────────────────────────────────────────────────────
# A cartoon skin over Learn Mode for roughly ages 7-12: bright card, chunky
# buttons, a peacock mascot that reacts, a powder-bottle character walking the
# simulated line, confetti on a finished part, and stickers/streaks instead of a
# bare score. Deliberately scoped to Learn Mode and stored in its own file, so
# flipping it changes nothing about the machine, the canvas, the G-code or any
# other setting: the same install is a game for a child and stays exactly as it
# was for an elder using Pulli Mode.
KID_MODE_CONFIG_FILE = os.path.join(_APP_DIR, "kid_mode_config.json")

KID_THEME = {
    "card":    "#fffaf0",   # warm cream instead of near-black
    "input":   "#ffe9c7",
    "ink":     "#4a3728",   # soft dark brown reads friendlier than white
    "dim":     "#9c8776",
    "canvas":  "#fffdf8",
    "outline": "#ff8fab",
    "cheer":   "#22c55e",
    "oops":    "#f59e0b",
}
# Tried in order; the first family actually installed wins. Comic Sans is the
# obvious kid face on Windows, but never assume a font exists — an unavailable
# family silently falls back to something arbitrary and can break layout.
KID_FONT_CANDIDATES = ["Comic Sans MS", "Chalkboard SE", "Baloo 2",
                       "Segoe Print", "Verdana", "Segoe UI"]
KID_CONFETTI_COLORS = ["#ef4444", "#f97316", "#eab308", "#22c55e",
                       "#3b82f6", "#a855f7", "#ec4899", "#22d3ee"]
KID_SPARKLE_COLORS  = ["#fde047", "#fca5a5", "#a5f3fc", "#d8b4fe", "#bbf7d0"]
KID_MAX_SPARKLES    = 26        # older sparkles are culled so the canvas stays light

# Stars are the child-facing form of the AI score. Same number underneath — the
# real score stays on the card so an adult can still read it.
KID_STAR_THRESHOLDS = [2, 4, 6, 8, 9]   # score >= t earns that many stars

# Stickers. Every rule is checked against the learner profile, so a sticker is
# only ever shown as earned when the evidence for it is actually on disk.
KID_BADGES = [
    {"key": "first",    "icon": "🌸", "name": "First Rangoli",
     "how": "Finish your first rangoli."},
    {"key": "steady",   "icon": "✋", "name": "Steady Hand",
     "how": "Score 9 or more on any rangoli."},
    {"key": "symmetry", "icon": "🦋", "name": "Symmetry Star",
     "how": "Score 8+ on three symmetry challenges."},
    {"key": "oneline",  "icon": "🪄", "name": "One-Line Wizard",
     "how": "Find a rangoli that draws in one unbroken line."},
    {"key": "pulli",    "icon": "🎯", "name": "Pulli Pro",
     "how": "Reach Level 5 — the robot lays only dots."},
    {"key": "streak",   "icon": "🔥", "name": "Week Warrior",
     "how": "Practise on 7 different days in a row."},
]

# ── Language / translation ──────────────────────────────────────────────
# UI strings are written in English in the source and machine-translated on
# demand (Google's public "translate_a/single" endpoint — no API key, same
# unauthenticated call googletrans wraps). Translations are cached to disk
# forever: once a string has been seen in a language it never needs the
# network again, and the app still works offline after the first run.
# The debug Log panel is deliberately never routed through tr() — log lines
# are diagnostic/technical and stay in English regardless of UI language.
LANGUAGE_CONFIG_FILE    = os.path.join(_APP_DIR, "language_config.json")
TRANSLATION_CACHE_FILE  = os.path.join(_APP_DIR, "translation_cache.json")

LANGUAGES = {
    "en":  "English",
    "hi":  "हिंदी",
    "kn":  "ಕನ್ನಡ",
    "ta":  "தமிழ்",
    "te":  "తెలుగు",
    "ml":  "മലയാളം",
    "bn":  "বাংলা",
    "mr":  "मराठी",
    "gu":  "ગુજરાતી",
    "gom": "कोंकणी",
}

RANGOLI_IMAGE_PROMPTS = [
    "Generate a completely original rangoli in the style of a classic "
    "lotus/sunflower mandala: a small centre circle surrounded by ONE ring "
    "of 6-9 evenly spaced petals. Optionally add a ring of small pointed "
    "accents or dots just outside the petal tips. No second petal layer, "
    "no outer border. Thin, clean single-stroke black outlines with clear "
    "gaps between each petal - not thick or filled. Preserve perfect "
    "radial symmetry. The whole motif should be large and centred, filling "
    "most of the frame. Black outlines only on a white background. No "
    "fills, colors, shading, or 3D.",
    "Design a unique geometric rangoli using ONE simple shape repeated "
    "symmetrically (pick one: petals, a star, a hexagon, or diamonds) - "
    "6-9 repetitions arranged in a single ring around a small centre "
    "circle. Optionally add a ring of small accent points just outside. "
    "No extra layering. Keep outlines thin and clean with clear spacing "
    "between each repeated shape - not thick or filled. Maintain radial "
    "symmetry. The whole motif should be large and centred, filling most "
    "of the frame, on a white background.",
    "Create an original Indian rangoli built around ONE traditional lotus "
    "motif: a centre circle with 6-9 petals in a single ring, and "
    "optionally a thin outer ring of small dots or triangular accents just "
    "outside the petals. No second petal layer, no heavy border. Thin, "
    "well-separated black line art, single stroke weight - not thick or "
    "filled. Strong radial symmetry. The whole motif should be large and "
    "centred, filling most of the frame, on a white background.",
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

MAX_X    = 4
MAX_Y    = 4

NOZZLE_OPEN_Z   = 0.05
NOZZLE_CLOSED_Z = 0.00

# Axis margins as measured on the 2560x1600 reference screen. The live
# MARGIN_* values below are rescaled from these in ShapeApp.__init__ so the
# plot frame stays the same fraction of the canvas on every display; these
# module-level values are just the pre-scaling defaults.
REF_MARGIN_L = 40
REF_MARGIN_B = 32
REF_MARGIN_T = 4
REF_MARGIN_R = 4

MARGIN_L = REF_MARGIN_L
MARGIN_B = REF_MARGIN_B
MARGIN_T = REF_MARGIN_T
MARGIN_R = REF_MARGIN_R

GRAPH_W  = 680
GRAPH_H  = 680
CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

# Predesigned rangoli loaded/sent via Cmd+J — exact G-code, verbatim.
RANGOLI_GCODE = [
    '$X',
    'G21',
    'G90',
    'F200',
    'G1 Z0.00 F200',
    'G1 X25.88 F200',
    'G1 Y15.05 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X25.79 Y14.48 F200',
    'G1 X25.54 Y13.94 F200',
    'G1 X25.12 Y13.44 F200',
    'G1 X24.56 Y13.00 F200',
    'G1 X23.86 Y12.66 F200',
    'G1 X23.05 Y12.41 F200',
    'G1 X22.15 Y12.28 F200',
    'G1 X21.18 Y12.27 F200',
    'G1 X20.16 Y12.38 F200',
    'G1 X19.13 Y12.62 F200',
    'G1 X18.12 Y12.98 F200',
    'G1 X17.14 Y13.44 F200',
    'G1 X17.88 Y12.66 F200',
    'G1 X18.54 Y11.80 F200',
    'G1 X19.08 Y10.89 F200',
    'G1 X19.50 Y9.97 F200',
    'G1 X19.79 Y9.04 F200',
    'G1 X19.95 Y8.14 F200',
    'G1 X19.96 Y7.29 F200',
    'G1 X19.85 Y6.52 F200',
    'G1 X19.61 Y5.85 F200',
    'G1 X19.26 Y5.30 F200',
    'G1 X18.82 Y4.89 F200',
    'G1 X18.31 Y4.64 F200',
    'G1 X17.74 Y4.54 F200',
    'G1 X17.14 Y4.61 F200',
    'G1 X16.54 Y4.85 F200',
    'G1 X15.95 Y5.25 F200',
    'G1 X15.41 Y5.81 F200',
    'G1 X14.92 Y6.51 F200',
    'G1 X14.52 Y7.32 F200',
    'G1 X14.21 Y8.24 F200',
    'G1 X14.00 Y9.24 F200',
    'G1 X13.91 Y10.29 F200',
    'G1 X13.94 Y11.37 F200',
    'G1 X14.08 Y12.45 F200',
    'G1 X13.56 Y11.50 F200',
    'G1 X12.94 Y10.61 F200',
    'G1 X12.25 Y9.81 F200',
    'G1 X11.50 Y9.12 F200',
    'G1 X10.71 Y8.56 F200',
    'G1 X9.90 Y8.14 F200',
    'G1 X9.10 Y7.86 F200',
    'G1 X8.33 Y7.73 F200',
    'G1 X7.62 Y7.75 F200',
    'G1 X6.99 Y7.91 F200',
    'G1 X6.46 Y8.20 F200',
    'G1 X6.06 Y8.61 F200',
    'G1 X5.79 Y9.13 F200',
    'G1 X5.68 Y9.72 F200',
    'G1 X5.72 Y10.36 F200',
    'G1 X5.92 Y11.05 F200',
    'G1 X6.28 Y11.74 F200',
    'G1 X6.79 Y12.41 F200',
    'G1 X7.45 Y13.05 F200',
    'G1 X8.22 Y13.63 F200',
    'G1 X9.11 Y14.13 F200',
    'G1 X10.08 Y14.54 F200',
    'G1 X11.12 Y14.85 F200',
    'G1 X12.18 Y15.05 F200',
    'G1 X11.12 Y15.25 F200',
    'G1 X10.08 Y15.56 F200',
    'G1 X9.11 Y15.97 F200',
    'G1 X8.22 Y16.48 F200',
    'G1 X7.45 Y17.06 F200',
    'G1 X6.79 Y17.69 F200',
    'G1 X6.28 Y18.37 F200',
    'G1 X5.92 Y19.06 F200',
    'G1 X5.72 Y19.74 F200',
    'G1 X5.68 Y20.39 F200',
    'G1 X5.79 Y20.98 F200',
    'G1 X6.06 Y21.49 F200',
    'G1 X6.46 Y21.90 F200',
    'G1 X6.99 Y22.20 F200',
    'G1 X7.62 Y22.36 F200',
    'G1 X8.33 Y22.38 F200',
    'G1 X9.10 Y22.25 F200',
    'G1 X9.90 Y21.97 F200',
    'G1 X10.71 Y21.54 F200',
    'G1 X11.50 Y20.98 F200',
    'G1 X12.25 Y20.29 F200',
    'G1 X12.94 Y19.50 F200',
    'G1 X13.56 Y18.61 F200',
    'G1 X14.08 Y17.66 F200',
    'G1 X13.94 Y18.73 F200',
    'G1 X13.91 Y19.81 F200',
    'G1 X14.00 Y20.86 F200',
    'G1 X14.21 Y21.86 F200',
    'G1 X14.52 Y22.78 F200',
    'G1 X14.92 Y23.60 F200',
    'G1 X15.41 Y24.30 F200',
    'G1 X15.95 Y24.85 F200',
    'G1 X16.54 Y25.25 F200',
    'G1 X17.14 Y25.49 F200',
    'G1 X17.74 Y25.57 F200',
    'G1 X18.31 Y25.47 F200',
    'G1 X18.82 Y25.21 F200',
    'G1 X19.26 Y24.80 F200',
    'G1 X19.61 Y24.26 F200',
    'G1 X19.85 Y23.59 F200',
    'G1 X19.96 Y22.82 F200',
    'G1 X19.95 Y21.97 F200',
    'G1 X19.79 Y21.07 F200',
    'G1 X19.50 Y20.14 F200',
    'G1 X19.08 Y19.21 F200',
    'G1 X18.54 Y18.31 F200',
    'G1 X17.88 Y17.45 F200',
    'G1 X17.14 Y16.66 F200',
    'G1 X18.12 Y17.13 F200',
    'G1 X19.13 Y17.49 F200',
    'G1 X20.16 Y17.72 F200',
    'G1 X21.18 Y17.84 F200',
    'G1 X22.15 Y17.83 F200',
    'G1 X23.05 Y17.69 F200',
    'G1 X23.86 Y17.45 F200',
    'G1 X24.56 Y17.10 F200',
    'G1 X25.12 Y16.67 F200',
    'G1 X25.54 Y16.17 F200',
    'G1 X25.79 Y15.62 F200',
    'G1 X25.88 Y15.05 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X17.47 F200',
    'G1 Y15.14 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X17.42 Y14.64 F200',
    'G1 X17.27 Y14.17 F200',
    'G1 X17.04 Y13.73 F200',
    'G1 X16.72 Y13.34 F200',
    'G1 X16.34 Y13.02 F200',
    'G1 X15.90 Y12.79 F200',
    'G1 X15.42 Y12.64 F200',
    'G1 X14.92 Y12.60 F200',
    'G1 X14.43 Y12.64 F200',
    'G1 X13.95 Y12.79 F200',
    'G1 X13.51 Y13.02 F200',
    'G1 X13.12 Y13.34 F200',
    'G1 X12.81 Y13.73 F200',
    'G1 X12.57 Y14.17 F200',
    'G1 X12.43 Y14.64 F200',
    'G1 X12.38 Y15.14 F200',
    'G1 X12.43 Y15.64 F200',
    'G1 X12.57 Y16.12 F200',
    'G1 X12.81 Y16.56 F200',
    'G1 X13.12 Y16.94 F200',
    'G1 X13.51 Y17.26 F200',
    'G1 X13.95 Y17.49 F200',
    'G1 X14.43 Y17.64 F200',
    'G1 X14.92 Y17.69 F200',
    'G1 X15.42 Y17.64 F200',
    'G1 X15.90 Y17.49 F200',
    'G1 X16.34 Y17.26 F200',
    'G1 X16.72 Y16.94 F200',
    'G1 X17.04 Y16.56 F200',
    'G1 X17.27 Y16.12 F200',
    'G1 X17.42 Y15.64 F200',
    'G1 X17.47 Y15.14 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X5.03 F200',
    'G1 Y14.97 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X5.02 Y14.96 F200',
    'G1 X5.02 Y14.96 F200',
    'G1 X5.02 Y14.95 F200',
    'G1 X5.02 Y14.95 F200',
    'G1 X5.02 Y14.95 F200',
    'G1 X5.01 Y14.95 F200',
    'G1 X5.01 Y14.94 F200',
    'G1 X5.00 Y14.94 F200',
    'G1 X5.00 Y14.94 F200',
    'G1 X4.99 Y14.95 F200',
    'G1 X4.99 Y14.95 F200',
    'G1 X4.99 Y14.95 F200',
    'G1 X4.98 Y14.95 F200',
    'G1 X4.98 Y14.96 F200',
    'G1 X4.98 Y14.96 F200',
    'G1 X4.98 Y14.97 F200',
    'G1 X4.98 Y14.97 F200',
    'G1 X4.98 Y14.97 F200',
    'G1 X4.98 Y14.98 F200',
    'G1 X4.99 Y14.98 F200',
    'G1 X4.99 Y14.98 F200',
    'G1 X4.99 Y14.99 F200',
    'G1 X5.00 Y14.99 F200',
    'G1 X5.00 Y14.99 F200',
    'G1 X5.01 Y14.99 F200',
    'G1 X5.01 Y14.99 F200',
    'G1 X5.02 Y14.98 F200',
    'G1 X5.02 Y14.98 F200',
    'G1 X5.02 Y14.98 F200',
    'G1 X5.02 Y14.97 F200',
    'G1 X5.02 Y14.97 F200',
    'G1 X5.03 Y14.97 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X22.58 F200',
    'G1 Y10.14 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X22.58 Y10.13 F200',
    'G1 X22.58 Y10.13 F200',
    'G1 X22.58 Y10.13 F200',
    'G1 X22.57 Y10.12 F200',
    'G1 X22.57 Y10.12 F200',
    'G1 X22.57 Y10.12 F200',
    'G1 X22.56 Y10.12 F200',
    'G1 X22.56 Y10.12 F200',
    'G1 X22.55 Y10.12 F200',
    'G1 X22.55 Y10.12 F200',
    'G1 X22.55 Y10.12 F200',
    'G1 X22.54 Y10.12 F200',
    'G1 X22.54 Y10.13 F200',
    'G1 X22.54 Y10.13 F200',
    'G1 X22.54 Y10.13 F200',
    'G1 X22.54 Y10.14 F200',
    'G1 X22.54 Y10.14 F200',
    'G1 X22.54 Y10.15 F200',
    'G1 X22.54 Y10.15 F200',
    'G1 X22.54 Y10.15 F200',
    'G1 X22.55 Y10.16 F200',
    'G1 X22.55 Y10.16 F200',
    'G1 X22.55 Y10.16 F200',
    'G1 X22.56 Y10.16 F200',
    'G1 X22.56 Y10.16 F200',
    'G1 X22.57 Y10.16 F200',
    'G1 X22.57 Y10.16 F200',
    'G1 X22.57 Y10.15 F200',
    'G1 X22.58 Y10.15 F200',
    'G1 X22.58 Y10.15 F200',
    'G1 X22.58 Y10.14 F200',
    'G1 X22.58 Y10.14 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X22.80 F200',
    'G1 Y20.89 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X22.80 Y20.89 F200',
    'G1 X22.80 Y20.88 F200',
    'G1 X22.80 Y20.88 F200',
    'G1 X22.79 Y20.87 F200',
    'G1 X22.79 Y20.87 F200',
    'G1 X22.79 Y20.87 F200',
    'G1 X22.78 Y20.87 F200',
    'G1 X22.78 Y20.87 F200',
    'G1 X22.77 Y20.87 F200',
    'G1 X22.77 Y20.87 F200',
    'G1 X22.77 Y20.87 F200',
    'G1 X22.76 Y20.87 F200',
    'G1 X22.76 Y20.88 F200',
    'G1 X22.76 Y20.88 F200',
    'G1 X22.76 Y20.89 F200',
    'G1 X22.76 Y20.89 F200',
    'G1 X22.76 Y20.89 F200',
    'G1 X22.76 Y20.90 F200',
    'G1 X22.76 Y20.90 F200',
    'G1 X22.76 Y20.91 F200',
    'G1 X22.77 Y20.91 F200',
    'G1 X22.77 Y20.91 F200',
    'G1 X22.77 Y20.91 F200',
    'G1 X22.78 Y20.91 F200',
    'G1 X22.78 Y20.91 F200',
    'G1 X22.79 Y20.91 F200',
    'G1 X22.79 Y20.91 F200',
    'G1 X22.79 Y20.91 F200',
    'G1 X22.80 Y20.90 F200',
    'G1 X22.80 Y20.90 F200',
    'G1 X22.80 Y20.89 F200',
    'G1 X22.80 Y20.89 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X12.57 F200',
    'G1 Y6.28 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X12.57 Y6.27 F200',
    'G1 X12.57 Y6.27 F200',
    'G1 X12.57 Y6.26 F200',
    'G1 X12.57 Y6.26 F200',
    'G1 X12.56 Y6.26 F200',
    'G1 X12.56 Y6.26 F200',
    'G1 X12.56 Y6.25 F200',
    'G1 X12.55 Y6.25 F200',
    'G1 X12.55 Y6.25 F200',
    'G1 X12.54 Y6.26 F200',
    'G1 X12.54 Y6.26 F200',
    'G1 X12.54 Y6.26 F200',
    'G1 X12.53 Y6.26 F200',
    'G1 X12.53 Y6.27 F200',
    'G1 X12.53 Y6.27 F200',
    'G1 X12.53 Y6.28 F200',
    'G1 X12.53 Y6.28 F200',
    'G1 X12.53 Y6.28 F200',
    'G1 X12.53 Y6.29 F200',
    'G1 X12.54 Y6.29 F200',
    'G1 X12.54 Y6.29 F200',
    'G1 X12.54 Y6.30 F200',
    'G1 X12.55 Y6.30 F200',
    'G1 X12.55 Y6.30 F200',
    'G1 X12.56 Y6.30 F200',
    'G1 X12.56 Y6.30 F200',
    'G1 X12.56 Y6.29 F200',
    'G1 X12.57 Y6.29 F200',
    'G1 X12.57 Y6.29 F200',
    'G1 X12.57 Y6.28 F200',
    'G1 X12.57 Y6.28 F200',
    'G1 X12.57 Y6.28 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X12.49 F200',
    'G1 Y23.26 F200',
    'M3',
    'G1 Z0.05 F200',
    'G1 X12.49 Y23.26 F200',
    'G1 X12.48 Y23.25 F200',
    'G1 X12.48 Y23.25 F200',
    'G1 X12.48 Y23.24 F200',
    'G1 X12.48 Y23.24 F200',
    'G1 X12.47 Y23.24 F200',
    'G1 X12.47 Y23.24 F200',
    'G1 X12.46 Y23.24 F200',
    'G1 X12.46 Y23.24 F200',
    'G1 X12.46 Y23.24 F200',
    'G1 X12.45 Y23.24 F200',
    'G1 X12.45 Y23.24 F200',
    'G1 X12.45 Y23.25 F200',
    'G1 X12.44 Y23.25 F200',
    'G1 X12.44 Y23.26 F200',
    'G1 X12.44 Y23.26 F200',
    'G1 X12.44 Y23.26 F200',
    'G1 X12.44 Y23.27 F200',
    'G1 X12.45 Y23.27 F200',
    'G1 X12.45 Y23.28 F200',
    'G1 X12.45 Y23.28 F200',
    'G1 X12.46 Y23.28 F200',
    'G1 X12.46 Y23.28 F200',
    'G1 X12.46 Y23.28 F200',
    'G1 X12.47 Y23.28 F200',
    'G1 X12.47 Y23.28 F200',
    'G1 X12.48 Y23.28 F200',
    'G1 X12.48 Y23.28 F200',
    'G1 X12.48 Y23.27 F200',
    'G1 X12.48 Y23.27 F200',
    'G1 X12.49 Y23.26 F200',
    'G1 X12.49 Y23.26 F200',
    'M5',
    'G1 Z0.00 F200',
    'G1 X0',
    'G1 Y0',
]

# Black chrome + white drawing canvas; colourful accent buttons.
BG_DARK      = "#0a0a0f"
BG_PANEL     = "#12121a"
BG_CARD      = "#1a1a28"
BG_INPUT     = "#26263a"
GLASS_BORDER = "#3d3880"
GLASS_EDGE   = "#4b5563"
ACCENT_BLUE  = "#60a5fa"
ACCENT_CYAN  = "#22d3ee"
ACCENT_GREEN = "#10b981"
ACCENT_AMBER = "#f97316"
ACCENT_PINK  = "#f472b6"
ACCENT_PURP  = "#a78bfa"
TEXT_PRIMARY = "#e2e8f0"
TEXT_DIM     = "#94a3b8"
ORIGIN_RED   = "#ff4d6d"
CANVAS_BG    = "#ffffff"

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

# Colour number = position in COLOUR_PALETTE (Red=1 ... Purple=8).  After each
# colour switch the machine taps out this number in Y so the operator can tell
# which colour is loaded: N x (forward COLOUR_MARK_MM, back to origin).
COLOUR_NUMBERS  = {name: i for i, name in enumerate(COLOUR_PALETTE, start=1)}
COLOUR_MARK_MM  = 1.00

MODES = [
    ("Pre-designed",            "",  ACCENT_BLUE),
    ("AI Generated",            "",  ACCENT_PURP),
    ("Import Designs",          "",  ACCENT_AMBER),
    ("Robot Test",              "",  ACCENT_GREEN),
]

FLAME_COLORS  = ["#fbbf24", "#f97316", "#fde047", "#f97316", "#fb923c"]
FLOWER_COLORS = ["#f472b6", "#a855f7", "#22d3ee", "#f9a825", "#fb7185", "#34d399"]
AI_FX_TICK_MS = 180

def _translate(paths, cx, cy):
    return [[(cx + x, cy - y) for x, y in path] for path in paths]


# ── Polyline -> G2/G3 arc fitting ───────────────────────────────────────────
# Every shape here (petals, rings, DXF imports, freehand pen strokes...) is
# stored as a dense polyline. Turning that into G-code as one G1 per point is
# what causes GRBL to decelerate to a near-stop at every vertex (hundreds of
# them per shape). A true circle needs only one G2/G3 command; a general
# curve doesn't have a single command for it, but it can still be covered by
# a handful of circular arcs instead of dozens of tiny straight lines.
# This greedily grows, from each point, the longest run that one circle can
# explain within ARC_FIT_TOLERANCE, emits it as a single G2/G3, and only
# falls back to G1 where the curvature won't fit a circle at all (or is a
# genuinely straight run).
ARC_FIT_TOLERANCE = 0.03            # mm — max deviation of a fitted arc from the points
ARC_MIN_RUN       = 4               # points collapsed before an arc is worth it
ARC_MAX_RADIUS    = 2000.0          # circles bigger than this are effectively straight
ARC_MAX_SWEEP     = 2 * math.pi - 0.15  # stop short of a full circle (start==end is ambiguous)


def _fit_circle(p1, p2, p3):
    """Exact circumcircle through 3 points. Returns (cx, cy, r) or None if
    the points are (numerically) collinear.

    This is used with p1/p3 fixed as the arc's start/end point, which is
    what actually matters: GRBL computes an arc's radius twice — once from
    (current position -> center) via I/J, once from (center -> target X/Y)
    — and alarms (error:33) if they disagree by more than a few microns.
    A least-squares fit over every point in the run only bounds the
    *average* error; the two points GRBL actually checks (the run's first
    and last) could each sit up to `tol` off that average circle, on
    opposite sides, so their disagreement could be ~2*tol — comfortably
    enough to trip the alarm. Forcing the circle through the endpoints
    exactly (with p2, a middle point, only choosing *which* of the
    infinitely many circles through p1/p3 to use) guarantees the two radii
    GRBL computes are identical by construction, not just close."""
    ax, ay = p1
    bx, by = p2
    cx0, cy0 = p3
    d = 2 * (ax * (by - cy0) + bx * (cy0 - ay) + cx0 * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx0 * cx0 + cy0 * cy0
    ux = (a2 * (by - cy0) + b2 * (cy0 - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx0 - bx) + b2 * (ax - cx0) + c2 * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _arc_sweep_and_code(pts, cx, cy):
    """If pts wind steadily (no direction reversal) around (cx, cy), return
    (total_sweep_radians, grbl_code). None if the winding reverses, which
    means a single arc can't represent this run even though the points
    happen to lie on a circle (e.g. it doubles back on itself)."""
    angles = [math.atan2(y - cy, x - cx) for x, y in pts]
    unwrapped = [angles[0]]
    for a in angles[1:]:
        da = a - unwrapped[-1]
        while da > math.pi:
            da -= 2 * math.pi
        while da < -math.pi:
            da += 2 * math.pi
        unwrapped.append(unwrapped[-1] + da)
    diffs = [unwrapped[k + 1] - unwrapped[k] for k in range(len(unwrapped) - 1)]
    if all(d >= -1e-9 for d in diffs):
        total = unwrapped[-1] - unwrapped[0]
    elif all(d <= 1e-9 for d in diffs):
        total = unwrapped[-1] - unwrapped[0]
    else:
        return None
    if abs(total) < 1e-6:
        return None
    return (abs(total), 3 if total > 0 else 2)  # CCW -> G3, CW -> G2


def _fit_arcs(points, tol=ARC_FIT_TOLERANCE):
    """Collapse a dense polyline into a mix of straight and circular moves.

    Returns a list of segments describing the move *to* each point from
    wherever the previous segment ended (points[0] is the start and is not
    itself part of the output):
      ('line', (x, y))
      ('arc',  (x, y), cx, cy, grbl_code)   # grbl_code is 2 (G2) or 3 (G3)
    """
    segments = []
    n = len(points)
    i = 0
    while i < n - 1:
        best = None  # (end_index, cx, cy, code)
        if n - i > ARC_MIN_RUN:
            j = i + ARC_MIN_RUN
            while j < n:
                window = points[i:j + 1]
                mid = points[i + (j - i) // 2]
                circle = _fit_circle(points[i], mid, points[j])
                if circle is None:
                    break
                cx, cy, r = circle
                if r > ARC_MAX_RADIUS:
                    break
                if any(abs(math.hypot(px - cx, py - cy) - r) > tol
                       for px, py in window):
                    break
                sweep = _arc_sweep_and_code(window, cx, cy)
                if sweep is None or sweep[0] > ARC_MAX_SWEEP:
                    break
                best = (j, cx, cy, sweep[1])
                j += 1
        if best is not None:
            j, cx, cy, code = best
            segments.append(('arc', points[j], cx, cy, code))
            i = j
        else:
            segments.append(('line', points[i + 1]))
            i += 1
    return segments


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


FESTIVAL_OPTIONS   = ["All", "Diwali", "Onam", "Pongal/Sankranti", "Navratri", "Ugadi"]
STATE_OPTIONS      = ["All", "Tamil Nadu", "Andhra Pradesh", "Karnataka", "Kerala",
                       "Maharashtra", "Gujarat", "Rajasthan"]
DIFFICULTY_OPTIONS = ["All", "Easy", "Medium", "Hard"]

# Pre-designed gallery pulls these DXF files straight from Downloads —
# no procedural rangoli generation, no in-gallery editing.
DOWNLOADS_DIR   = os.path.join(os.path.expanduser("~/Downloads"), "Predesigned Library")
# User-saved canvas designs shown in a "My Designs" gallery section.
MY_DESIGNS_DIR  = os.path.join(DOWNLOADS_DIR, "My Designs")

# Kolam Notebook Digitizer — a photographed page is resized to this working
# resolution before tracing. Big enough to keep a pencil line, small enough
# that a whole page processes in well under a second on a laptop.
NB_MAX_DIM      = 1200
# Small images are enlarged to that working size too, but only so far — past
# this there is nothing left to recover and the tracing just costs more.
NB_MAX_UPSCALE  = 4.0
# Cap on blobs fed to the grid search, which is quadratic in them. A page whose
# printed rules dissolved into specks can offer thousands; the largest are kept.
NB_MAX_DOT_CANDIDATES = 500
# Hard ceiling on traced strokes per page, longest first. A shadow or a smudge
# can otherwise contribute hundreds of specks that the robot would try to draw.
NB_MAX_STROKES  = 140
# Lookup tables for skeleton tracing, built once on first use. Both are indexed
# by a pixel's 8-neighbour bit pattern, so the whole page can be tested with one
# convolution and one array lookup instead of a Python loop over every pixel.
_NB_THIN_LUT    = {}
# ── Daily Rotation ("Kolam of the Day") ────────────────────────────────────
# Every morning the app proposes one page out of her digitized notebook, so a
# household gets a different kolam each day at no extra cost and without an AI
# inventing anything: the pool is only the pages she actually drew.
DAILY_ROTATION_FILE = os.path.join(_APP_DIR, "daily_rotation.json")
# How many recent days are avoided before a page may come round again. With a
# 40-page puthagam that is a month and a bit before anything repeats; with only
# three pages the pool simply shrinks to "not yesterday's", which is the one
# rule that always holds.
DAILY_HISTORY_KEEP  = 30

# Lunar festival dates cannot be computed from the calendar, so the ones that
# matter here are tabulated per year: (start_month, start_day, end_month,
# end_day, label, note). Panchangam dates vary by region and almanac — these
# are the common North/South Indian dates and are worth checking against the
# family's own calendar. Years outside the table are not an error: the app
# falls back to the rules that *are* computable (Margazhi, Pongal, Friday).
FESTIVAL_CALENDAR = {
    2025: [(3, 30, 3, 30, "Ugadi",    "New year — a fresh, full design"),
           (9,  5, 9,  5, "Onam",     "Pookalam day"),
           (9, 22, 10, 1, "Navratri", "Nine nights, nine kolams"),
           (10, 18, 10, 22, "Diwali", "The biggest kolam of the year")],
    2026: [(3, 19, 3, 19, "Ugadi",    "New year — a fresh, full design"),
           (8, 26, 8, 26, "Onam",     "Pookalam day"),
           (10, 11, 10, 19, "Navratri", "Nine nights, nine kolams"),
           (11, 6, 11, 10, "Diwali",  "The biggest kolam of the year")],
    2027: [(4,  7, 4,  7, "Ugadi",    "New year — a fresh, full design"),
           (9, 14, 9, 14, "Onam",     "Pookalam day"),
           (9, 30, 10, 8, "Navratri", "Nine nights, nine kolams"),
           (10, 27, 10, 31, "Diwali", "The biggest kolam of the year")],
    2028: [(3, 27, 3, 27, "Ugadi",    "New year — a fresh, full design"),
           (9,  2, 9,  2, "Onam",     "Pookalam day"),
           (9, 18, 9, 26, "Navratri", "Nine nights, nine kolams"),
           (10, 15, 10, 19, "Diwali", "The biggest kolam of the year")],
}


def _daily_occasion(d):
    """What kind of day is ``d`` (a ``datetime.date``) for a kolam?

    Returns ``(label, note, elaborate)``. ``elaborate`` decides which half of
    her notebook the day's page is drawn from — the fuller pages on festival
    days, Margazhi mornings and Fridays; the quicker ones on ordinary days,
    because a working Tuesday morning is not when anyone wants a 15-dot sikku.
    """
    md = (d.month, d.day)

    # Pongal / Sankranti is solar: it lands on 14 January (15th in some years)
    # and the festival runs the days after, so a fixed window is honest here.
    if (1, 13) <= md <= (1, 17):
        return "Pongal", "Pongal special — sugarcane, pot and sun motifs", True

    for a_m, a_d, b_m, b_d, label, note in FESTIVAL_CALENDAR.get(d.year, ()):
        if (a_m, a_d) <= md <= (b_m, b_d):
            return label, note, True

    # Margazhi (mid-December to mid-January) is the kolam month: the doorstep
    # design grows through it, so the elaborate pages belong here.
    if md >= (12, 16) or md <= (1, 14):
        return "Margazhi", "Margazhi morning — an elaboration, not a quick one", True

    if d.weekday() == 4:
        return "Friday", "Velli kizhamai — a fuller kolam for Friday", True

    return "", "", False


PREDESIGNED_DXF = {
    "Compressed Flower 2": "Compressed_flower2.dxf",
    "Diyex":               "Diyex.dxf",
    "Flower 1":            "Flower_1.dxf",
    "Flower Nice":         "Flower_nice.dxf",
    "Funnel":              "funnel.dxf",
    "Image":               "image.dxf",
}

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

# ── Learn Mode content ─────────────────────────────────────────────────────
# The one-time "how to make a rangoli" lesson is the silent video played in
# the Learn Mode popup (see LEARN_VIDEO_FILE), not a written list of steps.

# Each part of the design is shown in its own colour so the student can see the
# whole plan at a glance and knows which powder to load for the current part.
LEARN_PART_COLORS = [
    ("Red",    "#ef4444"), ("Orange", "#f97316"), ("Yellow", "#eab308"),
    ("Green",  "#22c55e"), ("Blue",   "#3b82f6"), ("Purple", "#a855f7"),
    ("Pink",   "#ec4899"), ("Cyan",   "#06b6d4"),
]

# Five instructions shown before the student copies each part. The set rotates
# per step, so the guidance changes as the lesson progresses.
LEARN_STEP_SETS = [
    [
        "Study the highlighted part the robot just drew — that pink shape is "
        "exactly what you'll copy.",
        "Start at the point nearest you and rest the bottle nozzle just above "
        "the floor.",
        "Trace the outline in one slow, continuous squeeze — don't lift halfway.",
        "Keep the line thin and even; ease off the pressure on the curves.",
        "Stop squeezing before you lift the bottle so you don't leave a blob.",
    ],
    [
        "This part is a petal — see how it swells in the middle and narrows to "
        "points at each end.",
        "Dot the two pointed tips first so you know exactly where to aim.",
        "Draw one curved side from tip to tip, then the second side to close it.",
        "Match the robot's line width by holding the bottle at the same height.",
        "Brush away any stray grains outside the petal before you move on.",
    ],
    [
        "This part is a ring — the smoothest rings come from sweeping your whole "
        "arm around the centre.",
        "Plant your elbow, then move in one flowing motion instead of short "
        "strokes.",
        "Go slowly: a ring shows every wobble, so steady beats fast here.",
        "Overlap the powder slightly where the ring closes so there's no gap.",
        "Check it's even all the way round before you refill the bottle.",
    ],
    [
        "This part is made of straight lines — line up your start point with the "
        "robot's.",
        "Pull each line toward yourself; pulling is far steadier than pushing.",
        "Hold the squeeze pressure constant so the line doesn't fade at the end.",
        "Lift cleanly at the end of every line to keep the corners sharp.",
        "Space the lines evenly — glance at the robot's version to compare.",
    ],
    [
        "This is a fine-detail part — switch to a bottle with a narrower nozzle "
        "if you have one.",
        "Use short, light squeezes for small strokes instead of one long pour.",
        "Rest your little finger on a clean patch of floor to steady your hand.",
        "Build detail up gradually — you can always add powder, never remove it.",
        "Take your time; the small details are what make a rangoli shine.",
    ],
    [
        "This part ties the design together — connect it neatly to the parts you "
        "already drew.",
        "Follow the robot's line closely so the whole rangoli stays symmetrical.",
        "Fill any thin or broken spots with a second gentle pass of powder.",
        "Keep your hand off the finished areas so nothing gets smudged.",
        "When it's done, tap the loose powder off your fingers before the next "
        "step.",
    ],
]

# Kid Mode copy for the same six step sets — same instruction, small words, and
# the bottle is a "magic bottle". Index-for-index with LEARN_STEP_SETS so
# _learn_step_instructions can swap between them without any other change.
LEARN_STEP_SETS_KID = [
    [
        "Look at the bright shape the robot just drew — that's the one you copy!",
        "Start at the bit closest to you. Hold your magic bottle just above "
        "the floor.",
        "Squeeze slowly and go all the way round in one go — no stopping!",
        "Keep your line skinny, not fat. Go extra slow on the bendy bits.",
        "Stop squeezing BEFORE you lift up, or you'll get a big blob.",
    ],
    [
        "This bit is a petal — fat in the middle, pointy at both ends.",
        "Put a dot on each pointy end first, so you know where to aim.",
        "Draw one curvy side, then the other curvy side to close it up.",
        "Hold your bottle the same height as before so the lines match.",
        "Brush away any stray powder that escaped outside the petal.",
    ],
    [
        "This bit is a circle — the trick is to swing your whole arm round.",
        "Plant your elbow on the floor and swoosh round in one smooth move.",
        "Go slow! Circles show every wobble, so steady beats speedy.",
        "Overlap the powder a tiny bit where the circle joins up — no gaps.",
        "Check it's the same all the way round before you refill.",
    ],
    [
        "This bit is straight lines. Line your start point up with the robot's.",
        "Pull each line towards you — pulling is way steadier than pushing.",
        "Squeeze the same amount the whole way so the line doesn't go faint.",
        "Lift up cleanly at the end of each line to keep the corners sharp.",
        "Space them out evenly — peek at the robot's ones to compare.",
    ],
    [
        "This is a tiny detail bit — use a bottle with a smaller nozzle if "
        "you have one.",
        "Do little short squeezes instead of one big long pour.",
        "Rest your pinky finger on a clean bit of floor to steady your hand.",
        "Build it up bit by bit — you can always add powder, never take it away.",
        "Take your time. The tiny bits are what make people go wow!",
    ],
    [
        "This bit joins everything together — connect it neatly to your other bits.",
        "Follow the robot's line closely so the whole rangoli stays even.",
        "Go over any thin or gappy spots with a second gentle squeeze.",
        "Keep your hands off the finished bits so nothing gets smudged.",
        "Done? Tap the loose powder off your fingers before the next bit.",
    ],
]

# ── Learn Mode: progressive difficulty ─────────────────────────────────────
# The robot's share of the work shrinks as the child gets better. Level 1 is
# mostly demonstration — the robot draws about seven parts in ten and the child
# copies the rest. By Level 5 the robot has stopped drawing lines altogether
# and only lays the pulli (the dot scaffold), which is exactly how an
# experienced hand works: grandma doesn't need the lines drawn for her, she
# needs the dots put down. So graduating out of Learn Mode is not a certificate
# screen, it IS Pulli Mode — the child ends up in the same mode as grandma.
LEARN_LEVELS = {
    1: {"robot_share": 0.70, "title": "Follow along",
        "blurb": "The robot draws most of the parts — you copy them."},
    2: {"robot_share": 0.55, "title": "Take a turn",
        "blurb": "Just over half the parts are still the robot's."},
    3: {"robot_share": 0.40, "title": "Even hands",
        "blurb": "You now draw more of the rangoli than the robot does."},
    4: {"robot_share": 0.20, "title": "Almost on your own",
        "blurb": "The robot only demonstrates the few hardest parts."},
    5: {"robot_share": 0.00, "title": "Pulli Mode", "pulli": True,
        "blurb": "The robot lays only the dots. Every line is yours — "
                 "the way grandma does it."},
}
LEARN_MAX_LEVEL = 5

# A level only moves on evidence: LEARN_LEVEL_WINDOW consecutive attempts at
# the current level, all judged by the vision model. Scores are normalised to
# a 0-10 scale before they are compared with these thresholds.
LEARN_LEVEL_WINDOW  = 2
LEARN_PROMOTE_SCORE = 8.0   # every attempt in the window at/above this → up
LEARN_DEMOTE_SCORE  = 5.0   # every attempt in the window below this   → down

# Pulli Mode scaffold: dots sampled along each part of the design, so the child
# is connecting dots that belong to the rangoli they chose rather than a
# generic lattice. Capped so a 9-part design doesn't turn into a dot storm.
PULLI_DOTS_PER_PART = 6
PULLI_MAX_DOTS      = 48

# ── Learn Mode: symmetry challenges ────────────────────────────────────────
# The classroom finding this answers: the children could *see* the symmetry in a
# rangoli but couldn't draw it. Recognising a mirror line is not the same skill
# as producing one freehand, and drawing a whole design never isolates it —
# a wobbly petal and a wobbly reflection look the same on the mat.
#
# So this lesson type isolates it. The robot draws only what falls inside one
# fundamental domain of the design (the left half, or a quadrant, or an octant),
# and the child draws the reflections. The mirror lines are shown on screen and
# the target halves are ghosted in, so the child can see exactly what "mirrored"
# should look like while their hand tries to do it. The reflections come from the
# same _mirror_transforms engine the pen tool's mirror mode uses.
#
# How many axes the child completes scales with the level from item 5: one
# mirror line while they are learning, the full eightfold kolam symmetry by the
# time they reach Pulli Mode.
LEARN_SYMMETRY_BY_LEVEL = {
    1: "2-way", 2: "2-way", 3: "4-way", 4: "4-way", 5: "8-way",
}
LEARN_SYMMETRY_LABELS = {
    "2-way": "one mirror line",
    "4-way": "two mirror lines",
    "8-way": "four mirror lines",
}
# Parts whose clipped half is shorter than this fraction of the design's extent
# are slivers — not worth a turn of their own.
LEARN_SYM_MIN_LEN_FRAC = 0.04
LEARN_SYM_MAX_PAIRS    = 4      # keep one challenge to a few focused halves

# ── Learn Mode: one-continuous-line (sikku) visualisation ──────────────────
# Stroke endpoints closer together than this fraction of the design's extent are
# the same junction. Two strokes meeting at a petal tip are one vertex even when
# their float coordinates differ in the last decimals, and the Euler test is
# entirely a question of what counts as the same vertex.
SIKKU_WELD_FRAC = 0.02
# A dot counts as "passed" once the pen comes this close, as a fraction of the
# preview's dot spacing.
SIKKU_DOT_HIT_FRAC = 0.9

# ── Ratio-based UI scaling ──────────────────────────────────────────────────
# Every fixed pixel size below (popup dimensions, fonts, padding, corner
# radii, button widths...) was measured against a 2560x1600 reference
# screen. UI_SCALE is the ratio between the machine actually running the app
# and that reference, so the whole chrome grows/shrinks together instead of
# looking correct on one monitor and cramped/oversized on another. It is set
# once in ShapeApp.__init__ from the real screen size. Canvas/camera-panel
# sizing already fits itself to available space and is untouched by this.
REF_SCREEN_W, REF_SCREEN_H = 2560, 1600
UI_SCALE = 1.0

# Lower bound on UI_SCALE. Pure proportional scaling makes chrome tiny on
# low-res laptops, so hold it at this floor and let the canvas give up the
# difference — readable controls matter more than a few extra canvas pixels.
MIN_UI_SCALE = 0.92
MAX_UI_SCALE = 1.60

# Height of the tallest popup in reference pixels (the Learn-mode dialog at
# S(760)). The floor above must never scale chrome past what the screen can
# actually show, so UI_SCALE is capped so this popup still fits with a margin.
TALLEST_POPUP_H = 760

# The canvas takes this fraction of the space left over by the banner and the
# bottom strip, rather than all of it. The leftover sliver is split evenly
# above and below by the centred placement, which is what keeps the "Rangoli
# Bot" banner title visually clear of the drawing surface.
CANVAS_FILL = 0.90

# ── Family Sharing: the notebook that lives in two houses ───────────────────
# A digitized page travels to the grandchild's copy of the app as either a
# small .kolam file (full detail, plus an optional photo and voice note) or a
# QR code (the design alone, small enough to hold up to a phone). Both are
# carried by hand — WhatsApp, email, a USB stick. There is no server, no
# account and no cloud in this feature, which is the point: the two houses are
# connected by the family, not by a service that can be switched off.
SHARE_DIR        = os.path.join(_APP_DIR, "family_share")
SHARE_INBOX_DIR  = os.path.join(SHARE_DIR, "inbox")     # pages received here
SHARE_REPLY_DIR  = os.path.join(SHARE_DIR, "replies")   # photos sent back
SHARE_MEDIA_DIR  = os.path.join(SHARE_DIR, "media")     # extracted photo/voice
SHARE_EXT        = ".kolam"
SHARE_SENDER_FILE = os.path.join(_APP_DIR, "share_sender.json")

# QR text payload: a magic prefix plus base45, both drawn from the QR
# alphanumeric alphabet so the code stays in the denser alphanumeric mode.
SHARE_MAGIC      = "KOLAM1:"
# Coordinates are quantised onto this many steps across the design's bounding
# box. Over a ~630px page that is a rounding error of about a third of a
# pixel — invisible — while costing one byte per coordinate for typical
# point-to-point steps.
SHARE_GRID       = 1024
# Above this many characters no code in the search survives the readability
# check, so the design is simplified before the encoder is asked. A cheap
# pre-filter only — _share_qr_readable is the real gate.
SHARE_QR_MAX_CHARS = 1500
# Modules per side beyond which a code stops surviving the blurred/downscaled
# decode, i.e. stops being something a phone reads at arm's length. Measured
# against this OpenCV build, not taken from the QR spec.
SHARE_QR_MAX_MODULES = 113
# Pixels per module when a code is rendered. Dense codes decode far more
# reliably at 8 than at the 3-4 a "fit it in 460px" rule would pick.
SHARE_QR_PX_PER_MODULE = 8
# Douglas–Peucker tolerances (canvas px) tried in order when a page is too big
# for one QR. 0.0 means "send every point as drawn".
SHARE_SIMPLIFY_STEPS = (0.0, 0.8, 1.5, 2.5, 4.0)
# Photos are re-encoded before being embedded, so a 4MB phone snap does not
# turn into a 5MB base64 blob inside the share file.
SHARE_PHOTO_MAX_DIM = 1000
SHARE_PHOTO_QUALITY = 78
# A voice note bigger than this is refused rather than silently embedded — at
# that size the file stops being a thing you can send over WhatsApp.
SHARE_VOICE_MAX_BYTES = 4 * 1024 * 1024
SHARE_AUDIO_EXTS = (".opus", ".ogg", ".m4a", ".mp3", ".aac", ".wav", ".amr")

# base45 (RFC 9285): two bytes become three characters from the QR
# alphanumeric alphabet. That is ~1.5 chars/byte against base64's 1.33, but
# alphanumeric mode packs 2 chars into 11 bits where byte mode needs 8 bits
# per char, so the QR ends up meaningfully smaller.
_B45_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_B45_REV      = {c: i for i, c in enumerate(_B45_ALPHABET)}


def _b45_encode(data):
    out = []
    for i in range(0, len(data) - 1, 2):
        v = data[i] * 256 + data[i + 1]
        out.append(_B45_ALPHABET[v % 45]); v //= 45
        out.append(_B45_ALPHABET[v % 45]); v //= 45
        out.append(_B45_ALPHABET[v])
    if len(data) % 2:
        v = data[-1]
        out.append(_B45_ALPHABET[v % 45])
        out.append(_B45_ALPHABET[v // 45])
    return "".join(out)


def _b45_decode(text):
    try:
        vals = [_B45_REV[c] for c in text]
    except KeyError as e:
        raise ValueError(f"not base45: {e.args[0]!r}") from None
    out = bytearray()
    for i in range(0, len(vals) - 2, 3):
        v = vals[i] + vals[i + 1] * 45 + vals[i + 2] * 2025
        if v > 0xFFFF:
            raise ValueError("corrupt base45 triple")
        out.append(v >> 8); out.append(v & 0xFF)
    rem = len(vals) % 3
    if rem == 2:
        v = vals[-2] + vals[-1] * 45
        if v > 0xFF:
            raise ValueError("corrupt base45 pair")
        out.append(v)
    elif rem:
        raise ValueError("truncated base45")
    return bytes(out)


class _ShareWriter:
    """Little byte sink for the compact design format. Varints everywhere —
    a kolam's point-to-point steps are small numbers and pay one byte."""

    def __init__(self):
        self.b = bytearray()

    def u8(self, v):
        self.b.append(int(v) & 0xFF)

    def u16(self, v):
        self.b += (max(0, min(65535, int(v)))).to_bytes(2, "big")

    def uv(self, v):
        v = int(v)
        while v >= 0x80:
            self.b.append((v & 0x7F) | 0x80)
            v >>= 7
        self.b.append(v)

    def sv(self, v):
        v = int(v)
        self.uv((-v << 1) - 1 if v < 0 else v << 1)   # zigzag

    def s(self, txt, wide=False):
        raw = (txt or "").encode("utf-8")[:65535 if wide else 255]
        (self.u16 if wide else self.u8)(len(raw))
        self.b += raw


class _ShareReader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def _need(self, n):
        if self.i + n > len(self.b):
            raise ValueError("share payload ended early")

    def u8(self):
        self._need(1)
        v = self.b[self.i]; self.i += 1
        return v

    def u16(self):
        self._need(2)
        v = int.from_bytes(self.b[self.i:self.i + 2], "big"); self.i += 2
        return v

    def uv(self):
        v = shift = 0
        while True:
            c = self.u8()
            v |= (c & 0x7F) << shift
            if not c & 0x80:
                return v
            shift += 7
            if shift > 63:
                raise ValueError("corrupt varint")

    def sv(self):
        v = self.uv()
        return (v >> 1) ^ -(v & 1)

    def s(self, wide=False):
        n = self.u16() if wide else self.u8()
        self._need(n)
        raw = self.b[self.i:self.i + n]; self.i += n
        return raw.decode("utf-8", "replace")


def _share_simplify(pts, tol):
    """Ramer–Douglas–Peucker. Drops points that sit within ``tol`` of the line
    they lie on, which is how an over-sampled traced stroke gets small enough
    to fit in a QR without changing the shape anyone can see."""
    if len(pts) < 3 or tol <= 0:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        x0, y0 = pts[a]
        x1, y1 = pts[b]
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy)
        best, best_i = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if norm < 1e-9:
                d = math.hypot(px - x0, py - y0)
            else:
                d = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / norm
            if d > best:
                best, best_i = d, i
        if best > tol:
            keep[best_i] = True
            stack.append((a, best_i))
            stack.append((best_i, b))
    return [p for p, k in zip(pts, keep) if k]


def _share_design_points(data):
    """Every stroke of a saved design, with its colour name. Shared by the
    encoder and by the "how big is this page" checks in the UI."""
    out = []
    for entry in data.get('shapes', []):
        pc = entry.get('path_colours') or {}
        base = entry.get('colour')
        for i, p in enumerate(entry.get('paths', [])):
            if len(p) < 2:
                continue
            try:
                pts = [(float(x), float(y)) for x, y in p]
            except (TypeError, ValueError):
                continue
            out.append((pts, pc.get(str(i)) or base))
    return out


def _share_encode(data, sender="", note="", tol=0.0):
    """A saved design dict → the compact bytes a QR carries.

    Only what the receiving app needs to *draw* the page travels this way:
    strokes, pulli, book and page, who sent it and a short note. The photo and
    voice note are far too big for a QR and ride in the .kolam file instead.
    """
    strokes = _share_design_points(data)
    paths, colours = [], []
    for pts, colour in strokes:
        if tol > 0:
            pts = _share_simplify(pts, tol)
        if len(pts) < 2:
            continue
        paths.append(pts)
        colours.append(COLOUR_NUMBERS.get(colour or "", 0))
    if not paths:
        raise ValueError("this design has no strokes to share")

    nb = data.get('notebook') or {}
    dots = []
    for d in nb.get('guide_dots') or []:
        try:
            dots.append((float(d[0]), float(d[1]), float(d[2])))
        except (TypeError, ValueError, IndexError):
            continue

    xs = [x for p in paths for x, _ in p] + [d[0] for d in dots]
    ys = [y for p in paths for _, y in p] + [d[1] for d in dots]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    sx = (SHARE_GRID - 1) / max(x1 - x0, 1e-6)
    sy = (SHARE_GRID - 1) / max(y1 - y0, 1e-6)

    w = _ShareWriter()
    w.u8(1)                                   # format version
    flags = ((1 if dots else 0)
             | (2 if nb.get('draw_dots') else 0)
             | (4 if nb.get('snapped') else 0)
             | (8 if any(colours) else 0)
             | (16 if tol > 0 else 0))
    w.u8(flags)
    w.s(str(nb.get('book') or ''))
    w.uv(max(0, int(nb.get('page') or 0)))
    w.s(sender)
    w.s(note, wide=True)
    # The bounding box travels in eighths of a canvas pixel, so the page lands
    # on the grandchild's canvas at the size and place the grandmother drew it.
    for v in (x0, y0, x1, y1):
        w.u16(round(v * 8))
    w.uv(len(paths))
    for pts in paths:
        w.uv(len(pts))
        px = py = 0
        for x, y in pts:
            qx = int(round((x - x0) * sx))
            qy = int(round((y - y0) * sy))
            w.sv(qx - px); w.sv(qy - py)
            px, py = qx, qy
    if flags & 8:
        for c in colours:
            w.u8(c)
    w.uv(len(dots))
    if dots:
        # One radius for the lot: they come off a fitted grid and differ by
        # less than the quantisation step anyway.
        w.u16(round(sum(d[2] for d in dots) / len(dots) * 16))
        px = py = 0
        for dx, dy, _ in sorted(dots, key=lambda d: (round(d[1], 1), d[0])):
            qx = int(round((dx - x0) * sx))
            qy = int(round((dy - y0) * sy))
            w.sv(qx - px); w.sv(qy - py)
            px, py = qx, qy
    return zlib.compress(bytes(w.b), 9)


def _share_decode(blob):
    """Compact bytes → (design dict shaped like My Designs, metadata dict)."""
    try:
        raw = zlib.decompress(blob)
    except zlib.error as e:
        raise ValueError(f"this kolam code is damaged ({e})") from None
    r = _ShareReader(raw)
    version = r.u8()
    if version != 1:
        raise ValueError(
            f"this code was made by a newer version of the app (format "
            f"{version}) — update this copy to open it")
    flags = r.u8()
    book   = r.s()
    page   = r.uv()
    sender = r.s()
    note   = r.s(wide=True)
    x0, y0, x1, y1 = (r.u16() / 8.0 for _ in range(4))
    ux = (x1 - x0) / (SHARE_GRID - 1)
    uy = (y1 - y0) / (SHARE_GRID - 1)

    n_paths = r.uv()
    if n_paths > 20000:
        raise ValueError("implausible stroke count — not a kolam code")
    paths = []
    for _ in range(n_paths):
        n_pts = r.uv()
        if n_pts > 200000:
            raise ValueError("implausible point count — not a kolam code")
        px = py = 0
        pts = []
        for _ in range(n_pts):
            px += r.sv(); py += r.sv()
            pts.append([round(x0 + px * ux, 2), round(y0 + py * uy, 2)])
        if len(pts) >= 2:
            paths.append(pts)
    colours = [r.u8() for _ in range(n_paths)] if flags & 8 else []
    dots = []
    n_dots = r.uv()
    if n_dots:
        radius = r.u16() / 16.0
        px = py = 0
        for _ in range(n_dots):
            px += r.sv(); py += r.sv()
            dots.append([round(x0 + px * ux, 2), round(y0 + py * uy, 2),
                         round(radius, 2)])

    entry = {'paths': paths, 'colour': None}
    by_no = {i: name for name, i in COLOUR_NUMBERS.items()}
    named = {str(i): by_no[c] for i, c in enumerate(colours) if c in by_no}
    if named:
        entry['path_colours'] = named
        entry['colour'] = next(iter(COLOUR_PALETTE))
    name = (f"{book} — Page {page}" if book and page
            else book or (f"Page {page}" if page else "Shared kolam"))
    design = {
        'name': name,
        'shapes': [entry],
        'notebook': {
            'book': book or "Shared kolam",
            'page': page,
            'dots': [],
            'guide_dots': dots,
            'guide_pitch': None,
            'draw_dots': bool(flags & 2),
            'snapped': bool(flags & 4),
            'ruled_lines_removed': False,
            'captured': "",
        },
    }
    return design, {'sender': sender, 'note': note,
                    'simplified': bool(flags & 16)}


def _share_page_id(book, page, sender):
    """Stable id for one page from one sender, derived the same way on both
    sides so a reply can be matched to the page it answers without either copy
    of the app having to carry an id around."""
    key = f"{(book or '').strip().lower()}|{int(page or 0)}|{(sender or '').strip().lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def S(px):
    """Scale a reference-resolution pixel length to the real screen."""
    return max(1, round(px * UI_SCALE))

def FS(pt):
    """Scale a reference-resolution font point size; never below 8pt."""
    return max(8, round(pt * UI_SCALE))

class ShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Rangoli-Bot")
        self.root.attributes("-fullscreen", True)
        # Escape leaves fullscreen through the same path as the window buttons,
        # so the maximise glyph never disagrees with the actual window state.
        self.root.bind("<Escape>",
                       lambda e: self._is_fullscreen() and self._window_toggle_max())
        self.root.configure(bg=BG_DARK)

        self._translation_cache = self._load_translation_cache()
        self.current_lang = self._load_language_pref()
        self._translatable_widgets = []  # [(widget, kwarg, english_text), ...]

        global GRAPH_W, GRAPH_H, CANVAS_W, CANVAS_H, UI_SCALE
        global MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B
        screen_h = self.root.winfo_screenheight()
        screen_w = self.root.winfo_screenwidth()
        # Truly proportional: the chrome takes the same FRACTION of the screen
        # on every machine, so whatever is left for the canvas is also the same
        # fraction everywhere. (The old max(1.0, ...) clamp pinned chrome at
        # full reference size on smaller laptops, which ate the canvas's share.)
        # The floor keeps text and hit targets usable on sub-720p panels; the
        # ceiling stops 4K/5K monitors from rendering everything oversized.
        UI_SCALE = min(screen_w / REF_SCREEN_W, screen_h / REF_SCREEN_H)
        UI_SCALE = max(MIN_UI_SCALE, min(UI_SCALE, MAX_UI_SCALE))
        # MIN_UI_SCALE can outrun a small screen — cap it so the tallest popup
        # still fits with room to spare rather than running off the display.
        UI_SCALE = min(UI_SCALE, (screen_h * 0.92) / TALLEST_POPUP_H)
        # Axis margins belong to the drawing surface, so they scale with it —
        # a fixed 40px gutter is 3% of the reference canvas but 6% of a small
        # one, which visibly shifts the plot frame between screens.
        MARGIN_L = S(REF_MARGIN_L)
        MARGIN_B = S(REF_MARGIN_B)
        MARGIN_T = S(REF_MARGIN_T)
        MARGIN_R = S(REF_MARGIN_R)
        # Reserve only a slim banner + bottom action strip; canvas fills the rest.
        graph_size = max(200, int(min(screen_w, screen_h - S(100)) * CANVAS_FILL))
        GRAPH_W  = graph_size
        GRAPH_H  = graph_size
        CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
        CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B
        self._resize_after_id = None

        self.shapes               = []
        self.selected_shape_index = None
        self._pending_raw_gcode   = None

        self.shape_type           = tk.StringVar(value="Select")
        self.feed_rate            = tk.StringVar(value="Low (default)")
        self.port_var             = tk.StringVar()
        self.size_val             = tk.IntVar(value=50)
        self.is_moving            = False
        self.last_ports           = []
        self.is_sending           = False
        self.is_paused            = False
        self.cancel_requested     = False
        self.pause_event          = threading.Event()
        self.pause_event.set()
        # Serial handle for the in-flight send_gcode() stream, if any. The
        # Pause/Cancel buttons run on the UI thread and need it to push GRBL
        # real-time bytes ('!', '~', 0x18) while the sender thread is blocked
        # waiting on a reply.
        self._active_serial       = None
        self.hint_popup           = None
        self._dxf_preview_popup   = None
        # Kolam Notebook Digitizer: one "sitting" spans many pages, so the
        # book name and page counter outlive each individual capture dialog.
        self._notebook_session    = None
        self._notebook_capture_dlg = None
        self._notebook_review_popup = None
        self._progress_popup      = None
        self.hint_after_id        = None
        self.progress_var         = tk.DoubleVar(value=0.0)

        self.selected_preset      = tk.StringVar(value="")
        self._gallery_popup       = None
        # Kolam of the Day: the morning proposal from her notebook.
        self._daily_popup         = None
        # True while the card is standing in for the launch design chooser,
        # which must be restored if the page is waved off.
        self._daily_resume        = False

        self.multi_colour_var     = tk.BooleanVar(value=False)
        self.shape_colour_var     = tk.StringVar(value=next(iter(COLOUR_PALETTE)))
        self._colour_switch_event = None
        self._pending_colour_event = None
        self._colour_switch_popup = None
        self._sim_running = False
        self._sim_after_id = None
        self._sim_frames = []
        self._sim_index = 0
        self._sim_last = None

        self._ai_generating = False

        self._ai_fx_running  = False
        self._ai_fx_loading  = False
        self._ai_fx_after_id = None
        self._flower_items   = []
        self._diya_items     = []

        self._log_popup      = None
        self._settings_popup = None
        self._features_popup = None

        # ── Learn Mode ──────────────────────────────────────────────────────
        self.learn_mode_var   = tk.BooleanVar(value=False)
        self.learn_btn        = None      # Features-popup start/stop button
        self._learn_popup     = None      # whichever learn popup is open now
        self._learn_intro_seen = False    # intro video shown at least once
        # Intro-video playback (OpenCV frames blitted into the Learn popup)
        self._learn_video_cap       = None   # cv2.VideoCapture while playing
        self._learn_video_after     = None   # after-id of the frame loop
        self._learn_video_label     = None   # Tk label the frames land on
        self._learn_video_img       = None   # PhotoImage reused per frame
        self._learn_video_pause_btn = None
        self._learn_video_paused    = False
        self._learn_video_size      = (640, 360)
        self._learn_video_delay     = 40.0   # ms between frames (from the fps)
        self._learn_design    = None      # name of the design being learned
        self._learn_parts     = []        # design decomposed into parts (paths)
        # Who draws which part is not a fixed alternation any more — it comes
        # from the learner's level (see LEARN_LEVELS and _learn_build_plan).
        self._learn_owner     = {}        # part index → "robot" | "student"
        self._learn_center    = (MARGIN_L + GRAPH_W // 2,
                                 MARGIN_T + GRAPH_H // 2)
        self._learn_dots      = []        # Pulli Mode: the dot scaffold, if any
        self._learn_dots_laid = False     # the scaffold is down on the mat
        # Lesson type: "full" walks the whole design, "symmetry" drills mirror
        # lines only (see LEARN_SYMMETRY_BY_LEVEL).
        self._learn_lesson    = "full"
        self._learn_sym_mode  = "2-way"
        self._learn_sym_pairs = []        # [{part, robot, target}, …]
        self._learn_sym_idx   = 0         # which challenge is on screen
        self._learn_sym_runs  = []        # preview-space runs being traced

        # ── Learn Mode: one-continuous-line (sikku) visualisation ───────────
        self._sikku_g         = None      # the analysed multigraph
        self._sikku_trails    = []        # the route, as continuous trails
        self._sikku_frames    = []        # pen frames in preview space
        self._sikku_i         = 0
        self._sikku_last      = None
        self._sikku_lifts     = 0
        self._sikku_running   = False
        self._sikku_after     = None
        self._sikku_prev      = None      # the visualisation canvas
        self._sikku_tf        = None
        self._sikku_dots      = []        # pulli in preview space
        self._sikku_dots_hit  = set()     # indices the line has reached
        self._sikku_dot_r     = 6.0
        self._sikku_counter   = None      # the live counter label
        self._sikku_play_btn  = None

        # ── Kid Mode ────────────────────────────────────────────────────────
        self.kid_mode      = False        # cartoon skin over Learn Mode
        self.kid_sounds    = False        # muted by default, on purpose
        self._kid_font     = None         # resolved playful family (lazy)
        self._kid_mascot   = None         # mascot canvas of the open popup
        self._kid_mascot_after = None
        self._kid_mascot_mood  = "idle"
        self._kid_mascot_phase = 0
        self._kid_bubble   = None         # mascot speech-bubble label
        self._kid_glass    = None         # open Learn popup's backdrop canvas
        self._kid_confetti = []           # live confetti particles
        self._kid_confetti_after = None
        self._kid_sparkles = []           # live sparkle item ids (capped)
        self._kid_oneline_solved = []     # designs proven to be one line
        self._load_kid_mode_config()
        self._learn_student_idx = None    # part the student is drawing now
        self._learn_robot_idx   = None    # part the robot is drawing now
        self._learn_done_parts  = set()   # indices finished by either of them
        self._learn_next_free   = 0       # next part nobody has claimed yet
        self._learn_streaming   = False   # robot part is on the real machine
        self._learn_cur_pts     = []      # preview-space points being traced
        self._learn_cur_col     = ACCENT_PURP
        self._learn_prev      = None      # the step popup's preview canvas
        self._learn_tf        = None      # (cx, cy, scale, mid_x, mid_y, ysign)
        self._learn_anim_id   = None      # after-id of the running preview draw
        self._learn_status    = None      # "what the robot is doing" label
        # Preset generators come back pre-flipped into canvas space; notebook
        # and shared pages are already the right way up. See _learn_tf_for.
        self._learn_flip_y    = True
        # Set when the lesson is a page somebody sent, so the result card can
        # offer to send the finished rangoli back to them.
        self._learn_share_src = None      # {'sender', 'book', 'page', 'page_id'}

        # ── Family Sharing ──────────────────────────────────────────────────
        self._share_popup     = None      # the compose / hub / QR popups
        self._share_qr_popup  = None
        self._share_scan_cap  = None      # cv2 capture while scanning a QR
        self._share_scan_after = None
        self._share_name      = ""        # who this copy of the app signs as
        self._load_share_sender()

        # ── Learn Mode: persisted learner profile ───────────────────────────
        self._learn_level     = 1         # 1 … LEARN_MAX_LEVEL
        self._learn_sessions  = []        # every recorded attempt, oldest first
        self._learn_level_note = None     # "moved up to Level 3" for the result
        self._load_learner_profile()

        # ── Learn Mode: USB camera (install half only — no camera on the rig)
        self._camera_index    = None      # device index of the installed camera
        self._camera_name     = None      # friendly label shown in the UI
        self._camera_devices  = []        # last scan: [(index, label), …]
        self._camera_scanning = False     # a scan thread is running
        self._camera_status   = None      # status label on the camera popup
        self._camera_list     = None      # frame the scan results land in
        self._learn_photo_path = None     # last photo taken of the real mat
        self._load_camera_config()

        # ── Live camera panel: shows the installed camera's feed while the
        # robot is drawing, floating over the canvas. ─────────────────────
        self._live_cam_cap    = None      # open cv2.VideoCapture while live
        self._live_cam_active = False
        self._live_cam_panel  = None      # floating frame over the canvas
        self._live_cam_label  = None      # image label inside the panel
        self._live_cam_status_lbl = None  # "Idle" / "LIVE" indicator label
        self._live_cam_photo  = None      # PhotoImage kept alive by reference
        self._live_cam_idle_photo = None  # placeholder PhotoImage
        self._live_cam_after  = None      # scheduled root.after id

        # One-shot callback fired on the main thread when a G-code stream ends.
        self._on_send_complete = None

        self._design_options_popup = None
        self._edit_popup = None

        # Stroke-editor state: hover, multi-select, pen tool, mirror, clipboard
        self._hover_hit     = None
        self._hit_cache     = None
        self._multi_sel     = []      # [(shape_idx, path_idx), ...]
        self._move_indices  = None    # shape indices moved together
        self._band_start    = None
        self._band_active   = False
        self._pending_hit   = None    # stroke pressed; popup opens on release
        self._clipboard     = []
        self._pen_points    = None
        self._line_start    = None    # (x, y, colour) while placing a Line
        self.pen_mode_var   = tk.BooleanVar(value=False)
        self.pen_btn        = None    # lives in the Design Options popup
        self.mirror_mode_var = tk.StringVar(value="Off")

        # Pulli guide layer: her notebook page's dot grid, shown on the canvas
        # purely as scaffolding for her own pen strokes. These are drawn with
        # their own tag and never carry "sim_path", so the robot never lays
        # them — the powder on the floor is only the lines she drew.
        self._pulli_guides  = []      # [(canvas_x, canvas_y, radius_px), ...]
        self._pulli_pitch   = None    # grid spacing in canvas px, if known
        self._pulli_label   = ""      # e.g. "Kolam Notebook — Page 3"
        self.pulli_show_var = tk.BooleanVar(value=True)
        self.pulli_snap_var = tk.BooleanVar(value=True)

        self.setup_ui()
        self.setup_context_menu()
        self.poll_ports()
        # Open design chooser on launch; the banner's "✎ Designs" button
        # re-opens it later.
        self.root.after(180, self._open_design_options_popup)
        # Then the morning's page from her notebook, once per day, on top of
        # the design chooser. Silent if the notebook is empty or today's page
        # has already been proposed.
        self.root.after(900, self._maybe_show_daily_kolam)

    # ── Main UI ───────────────────────────────────────────────────────────────
    def setup_ui(self):
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill="both", expand=True, padx=S(0), pady=S(0))

        # Slim top banner only — controls float on the canvas itself.
        self._build_banner(main)

        # Compact bottom strip: small action buttons + thin print progress.
        bottom = tk.Frame(main, bg=BG_DARK, height=S(36))
        bottom.pack(side="bottom", fill="x", padx=S(10), pady=(S(0), S(2)))
        bottom.pack_propagate(False)

        btn_wrap = tk.Frame(bottom, bg=BG_DARK)
        btn_wrap.pack(side="left", padx=(S(0), S(12)))
        clear_btn = self._color_button(
            btn_wrap, "Clear", self.clear_canvas, "#7c3aed",
            width=S(84), height=S(34), font_size=FS(11))
        clear_btn.pack(side="left", padx=(S(0), S(8)))
        self.send_btn = self._color_button(
            btn_wrap, "Send to Bot", self.start_gcode_streaming, "#0d9488",
            width=S(120), height=S(34), font_size=FS(11))
        self.send_btn.pack(side="left")

        # Pause / Cancel only exist while the robot is actually drawing, so
        # they are built here but left unpacked — _show_print_controls()
        # slides them in when a stream starts and takes them back out when it
        # ends. A disabled-but-visible button reads as "broken"; an absent one
        # reads as "not applicable yet".
        self.pause_btn = self._color_button(
            btn_wrap, "⏸ Pause", self.toggle_pause, "#b45309",
            width=S(96), height=S(34), font_size=FS(11), corner_radius=S(8))
        self.cancel_btn = self._color_button(
            btn_wrap, "✕ Cancel", self.cancel_gcode_streaming, "#b91c1c",
            width=S(96), height=S(34), font_size=FS(11), corner_radius=S(8))


        prog_wrap = tk.Frame(bottom, bg=BG_DARK)
        prog_wrap.pack(side="left", fill="x", expand=True, padx=(S(4), S(0)))
        self.progress_bar = ctk.CTkProgressBar(
            prog_wrap, variable=self.progress_var,
            fg_color=BG_INPUT, progress_color=ACCENT_GREEN,
            height=S(8), corner_radius=S(4))
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(S(0), S(8)))
        self.progress_bar.set(0)
        self.sidebar_progress_var = self.progress_var
        self.sidebar_progress_bar = self.progress_bar
        self.sidebar_pct_label = tk.Label(
            prog_wrap, text="0%", bg=BG_DARK, fg=ACCENT_GREEN,
            font=("Segoe UI", FS(9), "bold"), width=S(4))
        self.sidebar_pct_label.pack(side="left")

        # Canvas fills all remaining space.
        canvas_outer = tk.Frame(main, bg=BG_DARK)
        canvas_outer.pack(side="top", fill="both", expand=True)
        self.canvas_outer = canvas_outer

        self.root.update_idletasks()

        # canvas_wrap is the drawing surface host. Overlays are children of
        # this frame (not canvas_outer) so they sit ON the canvas, above the
        # drawing widget, not in the empty margins around it.
        canvas_wrap = tk.Frame(canvas_outer, bg=GLASS_BORDER, bd=1,
                               width=CANVAS_W, height=CANVAS_H)
        canvas_wrap.place(relx=0.5, rely=0.5, anchor="center")
        canvas_wrap.pack_propagate(False)
        canvas_wrap.grid_propagate(False)
        self.canvas_wrap = canvas_wrap

        self.canvas = tk.Canvas(canvas_wrap, width=CANVAS_W, height=CANVAS_H,
                                bg=CANVAS_BG, highlightthickness=0)
        # Fixed pixel size (no relwidth/relheight stretch) so event.x/y and
        # item coordinates stay 1:1 — critical for simulation matching the art.
        self.canvas.place(x=0, y=0, width=CANVAS_W, height=CANVAS_H)
        self._fit_canvas_to_space()
        self._build_live_camera_panel(canvas_outer, CANVAS_H)
        # Re-fit whenever the space actually changes size — leaving fullscreen
        # with Escape, moving to a second monitor, a DPI change. Without this
        # the canvas stays frozen at whatever it measured during startup.
        canvas_outer.bind("<Configure>", self._on_canvas_space_resize)

        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Button-2>", self.on_right_click)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<Motion>",   self.on_mouse_move)
        self.canvas.bind("<Shift-Button-1>",   self.on_shift_click)
        self.canvas.bind("<B1-Motion>",        self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>",  self.on_canvas_release)
        for seq in ("<Command-c>", "<Control-c>"):
            self.root.bind(seq, self._copy_selection)
        for seq in ("<Command-v>", "<Control-v>"):
            self.root.bind(seq, self._paste_clipboard)
        self.draw_grid()

        self._build_canvas_overlays(canvas_wrap)

        # Log console history (Text widget created only while log popup is open).
        self._log_lines = []
        self.console = None

        # Settings-popup control placeholders (rebuilt each time popup opens).
        self.port_combo = None
        self.port_menu  = None
        self.feed_combo = None
        self.shape_menu = None

    # ── Responsive canvas sizing ──────────────────────────────────────────────
    def _fit_canvas_to_space(self):
        """Size the drawing surface to the largest square that fits the space
        the banner and bottom strip left over. Both of those scale with
        UI_SCALE, so the square ends up the same fraction of the screen on
        every machine. Safe to call repeatedly."""
        global GRAPH_W, GRAPH_H, CANVAS_W, CANVAS_H

        outer = self.canvas_outer
        outer.update_idletasks()
        avail_w = outer.winfo_width()
        avail_h = outer.winfo_height()
        # An unmapped widget reports 1x1. Fall back to the screen minus the
        # chrome we reserved: the measured banner + bottom strip + its pad.
        if avail_w <= 1 or avail_h <= 1:
            chrome = getattr(self, "_banner_h", S(44)) + S(36) + S(2)
            avail_w = self.root.winfo_screenwidth()
            avail_h = self.root.winfo_screenheight() - chrome

        # CANVAS_FILL leaves a sliver of slack; the centred placement splits it
        # above and below, clearing the banner title.
        max_side = int(min(avail_w, avail_h) * CANVAS_FILL) - 2
        graph_size = max(
            200,
            max_side - max(MARGIN_L + MARGIN_R, MARGIN_T + MARGIN_B),
        )
        GRAPH_W  = graph_size
        GRAPH_H  = graph_size
        CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
        CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

        self.canvas_wrap.configure(width=CANVAS_W, height=CANVAS_H)
        self.canvas.configure(width=CANVAS_W, height=CANVAS_H)
        self.canvas.place_configure(x=0, y=0, width=CANVAS_W, height=CANVAS_H)

        # The live-camera panel is sized off the canvas height, so it has to
        # follow along or it stops matching once the window changes.
        if getattr(self, "_live_cam_panel", None) is not None:
            box_h = CANVAS_H
            box_w = max(S(320), int(CANVAS_H * 0.9))
            self._live_cam_frame_size = (box_w - S(24), box_h - S(90))
            self._live_cam_panel.configure(width=box_w, height=box_h)

    def _on_canvas_space_resize(self, event=None):
        """<Configure> fires in a burst for every pixel of a drag, and each
        re-fit redraws every shape — so coalesce them into one late call."""
        if self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except (ValueError, tk.TclError):
                pass
        self._resize_after_id = self.root.after(140, self._apply_canvas_resize)

    def _apply_canvas_resize(self):
        self._resize_after_id = None
        if not self.canvas.winfo_exists():
            return
        old_w, old_h = GRAPH_W, GRAPH_H
        self._fit_canvas_to_space()
        if (GRAPH_W, GRAPH_H) == (old_w, old_h):
            return
        # Shape geometry is stored in canvas pixels, so it has to be rescaled
        # about the plot origin — otherwise the artwork keeps its old pixel
        # position and lands on different millimetre coordinates.
        self._rescale_shapes(old_w, old_h)
        self.canvas.delete("all")
        self.draw_grid()
        self.redraw()

    def _rescale_shapes(self, old_w, old_h):
        """Remap every stored pixel coordinate from the old graph size to the
        new one, keeping each shape at the same millimetre position."""
        if not old_w or not old_h:
            return
        fx, fy = GRAPH_W / old_w, GRAPH_H / old_h
        if fx == 1.0 and fy == 1.0:
            return

        def _pt(x, y):
            return (MARGIN_L + (x - MARGIN_L) * fx,
                    MARGIN_T + (y - MARGIN_T) * fy)

        for s in self.shapes:
            s['x'], s['y'] = _pt(s['x'], s['y'])
            # 'size' is a pixel diameter/edge length; the graph stays square so
            # either factor works, but average them in case that ever changes.
            s['size'] = s.get('size', 0) * (fx + fy) / 2.0
            if s.get('paths'):
                s['paths'] = [[_pt(px, py) for px, py in path]
                              for path in s['paths']]
        # Cached hit geometry is now stale; redraw() rebuilds it.
        self._hit_cache = None

    def _color_button(self, parent, text, command, color, *,
                      width=None, height=None, font_size=None, corner_radius=None,
                      text_color="#ffffff"):
        """Solid colourful CTk button — full area clickable, no custom hover hacks.
        Defaults are resolved here (not in the signature) so they still pick up
        UI_SCALE, which is only known once ShapeApp.__init__ runs — a default
        baked into the signature would be frozen at import time instead."""
        width         = S(110)  if width         is None else width
        height        = S(36)   if height        is None else height
        font_size     = FS(12)  if font_size     is None else font_size
        corner_radius = S(10)   if corner_radius is None else corner_radius
        # hover=False keeps colour stable and avoids finicky enter/leave redraws.
        # Same colour for hover_color satisfies CTk API when hover is disabled.
        btn = ctk.CTkButton(
            parent, text=text, command=command,
            fg_color=color, hover_color=color, hover=False,
            text_color=text_color, font=("Segoe UI", font_size, "bold"),
            width=width, height=height, corner_radius=corner_radius,
            border_width=0)
        return btn

    def _build_canvas_overlays(self, host):
        """Float Settings / Log / Size / Multi-colour on the white canvas."""
        panel_bg = CANVAS_BG
        self._canvas_overlay_frames = []
        self._sim_after_id = None

        def _panel(**place_kw):
            fr = tk.Frame(host, bg=panel_bg, padx=S(6), pady=S(4), highlightthickness=0)
            fr.place(**place_kw)
            fr.lift()
            self._canvas_overlay_frames.append(fr)
            return fr

        # Settings gear + Debug Log now live in the top banner (see
        # _build_banner) instead of floating over the canvas.

        # Top-right: Simulate / AI Enhance
        top_right = _panel(relx=1.0, rely=0.0, anchor="ne", x=-6, y=6)

        # The pen tool now lives in the Design Options popup; this slot holds
        # Features (Learn Mode and anything added alongside it).
        self.progress_btn = self._color_button(
            top_right, "\U0001f4ca Progress", self._open_progress_dashboard,
            ACCENT_BLUE, width=S(110), height=S(40), font_size=FS(12),
            text_color="#0d0b2b")
        self.progress_btn.pack(side="left", padx=(S(0), S(8)))
        self.register_translatable(self.progress_btn, "Progress",
                                    fmt=lambda t: f"\U0001f4ca {t}")

        self.features_btn = self._color_button(
            top_right, "Features", self._open_features_popup,
            "#0d9488", width=S(70), height=S(40), font_size=FS(12))
        self.features_btn.pack(side="left", padx=(S(0), S(8)))
        self.register_translatable(self.features_btn, "Features")

        # Re-open the design chooser (Gallery / AI / Import / Freehand Pen)
        # at any time — it used to only appear once, on launch.
        self.design_options_btn = self._color_button(
            top_right, "✎ Designs", self._open_design_options_popup,
            "#0f766e", width=S(110), height=S(40), font_size=FS(12))
        self.design_options_btn.pack(side="left", padx=(S(0), S(8)))
        self.register_translatable(self.design_options_btn, "Designs",
                                    fmt=lambda t: f"✎ {t}")

        self.simulate_btn = self._color_button(
            top_right, "\u25b6 Simulate", self.simulate_pattern, ACCENT_AMBER,
            width=S(130), height=S(40), font_size=FS(12))
        self.simulate_btn.pack(side="left", padx=(S(0), S(8)))
        self.register_translatable(self.simulate_btn, "Simulate",
                                    fmt=lambda t: f"\u25b6 {t}")

        self.ai_fx_btn = self._color_button(
            top_right, "\u2728 AI Enhance", self.toggle_ai_effects, ACCENT_PURP,
            width=S(140), height=S(40), font_size=FS(12))
        self.ai_fx_btn.pack(side="left")
        self.register_translatable(self.ai_fx_btn, "AI Enhance",
                                    fmt=lambda t: f"\u2728 {t}")
        self._sim_running = False

        # Bottom: Size + Multi-colour (+ colour/part pickers)
        bottom_ov = _panel(relx=0.5, rely=1.0, anchor="s", x=0, y=-6)

        size_frame = tk.Frame(bottom_ov, bg=panel_bg)
        size_frame.pack(side="left", padx=(S(0), S(12)))
        size_lbl = tk.Label(size_frame, text="Size:", bg=panel_bg, fg=ACCENT_CYAN,
                 font=("Segoe UI", FS(10), "bold"))
        size_lbl.pack(side="left", padx=(S(0), S(6)))
        self.register_translatable(size_lbl, "Size", fmt=lambda t: f"{t}:")
        self.size_slider = ctk.CTkSlider(
            size_frame, from_=1, to=800, variable=self.size_val,
            command=self._on_slider, width=S(200), height=S(18),
            fg_color="#e2e8f0", progress_color=ACCENT_PURP,
            button_color=ACCENT_PINK, button_hover_color="#f9a8d4",
            bg_color=panel_bg)
        self.size_slider.pack(side="left", padx=(S(0), S(6)))
        self.size_display = tk.Label(
            size_frame, text="50", bg=panel_bg, fg=ACCENT_CYAN,
            font=("Segoe UI", FS(10), "bold"), width=S(4))
        self.size_display.pack(side="left")

        self.multi_colour_switch = ctk.CTkSwitch(
            bottom_ov, text="Multi-colour", variable=self.multi_colour_var,
            command=self._on_multi_colour_toggle,
            fg_color="#cbd5e1", progress_color=ACCENT_PINK,
            text_color="#9d174d", font=("Segoe UI", FS(11), "bold"),
            bg_color=panel_bg, width=S(48), height=S(22))
        self.multi_colour_switch.pack(side="left", padx=(S(0), S(10)))
        self.register_translatable(self.multi_colour_switch, "Multi-colour")

        colour_row = tk.Frame(bottom_ov, bg=panel_bg)
        colour_row.pack(side="left")
        colour_lbl = tk.Label(colour_row, text="Colour:", bg=panel_bg, fg=ACCENT_PINK,
                 font=("Segoe UI", FS(10), "bold"))
        colour_lbl.pack(side="left", padx=(S(0), S(4)))
        self.register_translatable(colour_lbl, "Colour", fmt=lambda t: f"{t}:")
        # CTkComboBox splits itself at (width - height): the left section is
        # outlined in border_color, but the right section holding the dropdown
        # arrow is outlined in button_color. With a pink border that made the
        # outline stop short and left the arrow sitting outside the box. Wrap a
        # borderless combo in a pink-bordered frame instead, so a single
        # rounded outline encloses the colour name and the arrow together.
        colour_box = ctk.CTkFrame(
            colour_row, fg_color="#f8fafc", border_color=ACCENT_PINK,
            border_width=S(2), corner_radius=S(8))
        colour_box.pack(side="left")
        self.colour_combo = ctk.CTkComboBox(
            colour_box, variable=self.shape_colour_var,
            values=list(COLOUR_PALETTE.keys()), state="readonly",
            width=S(112), height=S(24), fg_color="#f8fafc",
            border_width=0, corner_radius=S(6),
            button_color="#f8fafc", button_hover_color="#fce7f3",
            text_color="#9d174d", dropdown_fg_color="#ffffff",
            dropdown_text_color="#0f172a", font=("Segoe UI", FS(10)),
            command=self._on_colour_select)
        # Inset by more than the border width, or the combo covers the border.
        self.colour_combo.pack(padx=S(3), pady=S(3))
        self.colour_combo.configure(state="disabled")

        self.part_label = tk.Label(
            colour_row, text="  Part:", bg=panel_bg, fg=ACCENT_PURP,
            font=("Segoe UI", FS(10), "bold"))
        self.register_translatable(self.part_label, "Part", fmt=lambda t: f"  {t}:")
        self.part_select_var = tk.StringVar(value="Whole shape")
        self.part_combo = ctk.CTkComboBox(
            colour_row, variable=self.part_select_var, values=self._PART_OPTIONS,
            state="readonly", width=S(120), height=S(32), fg_color="#f8fafc",
            border_color=ACCENT_PURP, button_color="#f8fafc",
            button_hover_color="#ede9fe", text_color="#5b21b6",
            dropdown_fg_color="#ffffff", dropdown_text_color="#0f172a",
            font=("Segoe UI", FS(10)), command=self._on_part_select)
        self.part_label.pack_forget()
        self.part_combo.pack_forget()

        # Colour-emptied — top-left corner of the canvas. Settings and Debug
        # Log moved to the banner, so nothing sits above this any more.
        self.colour_emptied_btn = self._color_button(
            host, "\U0001f3a8 Emptied", self._on_colour_emptied_click, ACCENT_AMBER,
            width=S(120), height=S(34), font_size=FS(11))
        self.colour_emptied_btn.place(relx=0.0, rely=0.0, anchor="nw",
                                      x=S(8), y=S(8))
        self.colour_emptied_btn.lift()
        self.register_translatable(self.colour_emptied_btn, "Emptied",
                                    fmt=lambda t: f"\U0001f3a8 {t}")
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#4b5563", hover_color="#4b5563",
            text_color=TEXT_DIM)

        # Keep overlays above the canvas if anything re-stacks widgets later.
        self.root.after(50, self._raise_canvas_overlays)
        self.root.after(300, self._raise_canvas_overlays)

    def _raise_canvas_overlays(self):
        for w in getattr(self, "_canvas_overlay_frames", []):
            try:
                w.lift()
            except tk.TclError:
                pass
        try:
            self.colour_emptied_btn.lift()
        except (tk.TclError, AttributeError):
            pass

    # ── Banner ────────────────────────────────────────────────────────────────
    def _build_banner(self, parent):
        # Measure the title first: the banner has to be tall enough to show it
        # in full. It used to be a flat S(40) with the title drawn at unscaled
        # coordinates inside a scaled canvas, so the lettering was clipped.
        self._title_font = tkfont.Font(family="Georgia", size=FS(16),
                                       weight="bold")
        title_h = self._title_font.metrics("linespace")
        banner_h = max(S(44), title_h + S(12), S(36) + S(8))

        banner = tk.Frame(parent, bg="#000000", height=banner_h)
        banner.pack(fill="x", side="top")
        banner.pack_propagate(False)
        # Keep the banner above the canvas in the stacking order. pack() gives
        # it its own strip so nothing should overlap, but the canvas and its
        # overlays are created later and call lift() on themselves — this makes
        # the title's precedence explicit instead of order-dependent.
        banner.lift()
        self.banner = banner
        self._banner_h = banner_h

        center = tk.Frame(banner, bg="#000000")
        center.place(relx=0.5, rely=0.5, anchor="center")

        # Settings + Debug Log, top-left corner.
        controls = tk.Frame(banner, bg="#000000")
        controls.place(relx=0.0, rely=0.5, anchor="w", x=S(12))

        self.log_switch_var = tk.BooleanVar(value=False)
        self.debug_log_btn = self._color_button(
            controls, "</> Debug Log", self._on_debug_log_click,
            "#1e293b", width=S(118), height=S(36), font_size=FS(11), corner_radius=S(10))
        self.debug_log_btn.pack(side="left", padx=(S(0), S(8)))
        self.register_translatable(self.debug_log_btn, "Debug Log",
                                    fmt=lambda t: f"</> {t}")

        self.settings_btn = self._color_button(
            controls, "⚙", self._open_settings_popup, "#1e293b",
            width=S(40), height=S(36), font_size=FS(15), corner_radius=S(18))
        self.settings_btn.pack(side="left")

        icon_side = S(34)
        icon_c = tk.Canvas(center, width=icon_side, height=icon_side,
                           bg="#000000", highlightthickness=0)
        icon_c.pack(side="left", padx=(S(0), S(8)))
        # Centre and radius derived from the (scaled) canvas — the old fixed
        # 16/16/13 pushed the flower off-centre and clipped it once S() shrank
        # the canvas around it.
        self._draw_flower_icon(icon_c, icon_side / 2, icon_side / 2,
                               icon_side * 0.40)

        title = "Rangoli Bot"
        colors = ["#f9a825", "#f97316", "#ec4899", "#a855f7",
                  "#6366f1", "#3b82f6", "#06b6d4", "#10b981",
                  "#f9a825", "#f97316", "#ec4899"]
        # Measure the real font instead of a hand-tuned width table, so the
        # letters stay correctly spaced and fully visible at any UI_SCALE.
        tf = self._title_font
        title_w = tf.measure(title) + S(8)
        title_h = tf.metrics("linespace") + S(4)
        title_c = tk.Canvas(center, bg="#000000", highlightthickness=0,
                            width=title_w, height=title_h)
        title_c.pack(side="left")
        x_off = S(2)
        for ch, col in zip(title, colors):
            title_c.create_text(x_off, title_h / 2, text=ch, fill=col,
                                font=tf, anchor="w")
            x_off += tf.measure(ch)

        # Hidden rangoli trigger: an invisible black hit-area filling the
        # banner's right corner, with a tiny dot as the only visual marker.
        # Clicking anywhere in the corner region works, not just the dot.
        # Minimise / maximise / close, far right, in the order Windows uses.
        # The app opens fullscreen, which hides the native title bar, so these
        # stand in for the buttons every other window on the machine has.
        wc = self._build_window_controls(banner)
        wc.place(relx=1.0, rely=0.5, anchor="e", x=-S(4))
        wc.update_idletasks()

        hot_w = S(90)
        hot = tk.Canvas(banner, width=hot_w, height=self._banner_h,
                        bg="#000000", highlightthickness=0, cursor="hand2")
        dot_x, dot_y, dot_r = hot_w - S(10), self._banner_h / 2, S(2)
        hot.create_oval(dot_x - dot_r, dot_y - dot_r, dot_x + dot_r,
                        dot_y + dot_r, fill="#1a1a1a", outline="")
        # Clear of the window buttons, or the corner would eat their clicks.
        hot.place(relx=1.0, rely=0.5, anchor="e",
                  x=-(wc.winfo_reqwidth() + S(8)))
        hot.bind("<Button-1>", self.load_and_send_rangoli)

    # ── Window controls ───────────────────────────────────────────────────────
    def _build_window_controls(self, banner):
        wc = tk.Frame(banner, bg="#000000")
        self._win_restore_geom = None   # geometry to come back to from maximised
        self._win_was_full     = True   # re-enter fullscreen after un-minimising
        specs = (("–", self._window_minimise, "#334155"),
                 ("□", self._window_toggle_max, "#334155"),
                 ("✕", self._window_close,     "#e11d48"))
        for glyph, cmd, hover in specs:
            b = tk.Label(wc, text=glyph, bg="#000000", fg="#cbd5e1",
                         font=("Segoe UI", FS(12)), cursor="hand2",
                         width=4, height=1)
            b.pack(side="left")
            b.bind("<Enter>", lambda e, w=b, h=hover: w.configure(bg=h, fg="#ffffff"))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg="#000000", fg="#cbd5e1"))
            b.bind("<Button-1>", lambda e, c=cmd: c())
            if glyph == "□":
                self._max_btn = b
                if self._is_fullscreen():
                    b.configure(text="❐")   # we launch maximised
        # Coming back from the taskbar restores the fullscreen we gave up in
        # order to iconify (Windows will not minimise a fullscreen Tk window).
        self.root.bind("<Map>", self._on_root_mapped, add="+")
        return wc

    def _is_fullscreen(self):
        try:
            return bool(self.root.attributes("-fullscreen"))
        except Exception:
            return False

    def _window_minimise(self):
        self._win_was_full = self._is_fullscreen()
        if self._win_was_full:
            self.root.attributes("-fullscreen", False)
        try:
            self.root.iconify()
        except Exception:
            pass

    def _on_root_mapped(self, event):
        if event.widget is not self.root:
            return
        if self._win_was_full and not self._is_fullscreen():
            self.root.after(10, lambda: self.root.attributes("-fullscreen", True))

    def _window_toggle_max(self):
        if self._is_fullscreen():
            self.root.attributes("-fullscreen", False)
            self._win_was_full = False
            if self._win_restore_geom:
                self.root.geometry(self._win_restore_geom)
            else:
                w = int(self.root.winfo_screenwidth()  * 0.75)
                h = int(self.root.winfo_screenheight() * 0.75)
                self.root.geometry(f"{w}x{h}+{w//8}+{h//8}")
            if hasattr(self, "_max_btn"):
                self._max_btn.configure(text="□")
        else:
            self._win_restore_geom = self.root.geometry()
            self.root.attributes("-fullscreen", True)
            self._win_was_full = True
            if hasattr(self, "_max_btn"):
                self._max_btn.configure(text="❐")

    def _window_close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

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

    @staticmethod
    def _lighten(hex_col, amount=40):
        r = min(255, int(hex_col[1:3], 16) + amount)
        g = min(255, int(hex_col[3:5], 16) + amount)
        b = min(255, int(hex_col[5:7], 16) + amount)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _small_btn(self, parent, text, cmd, fg_color, hover_color):
        """Compact action button. ``cmd`` is wrapped so rapid double-fires
        (press+release races) cannot run the action twice."""
        fired = {"done": False}

        def _once():
            if fired["done"]:
                return
            fired["done"] = True
            cmd()

        wrap = tk.Frame(parent, bg=BG_CARD, cursor="hand2")
        wrap.pack(side="right", padx=(S(4), S(0)))
        btn = self._color_button(
            wrap, text, _once, fg_color,
            width=S(112), height=S(40), font_size=FS(12))
        btn.pack(padx=S(4), pady=S(4))
        return btn

    def _label(self, parent, text, fg=TEXT_DIM):
        tk.Label(parent, text=text, bg=BG_CARD, fg=fg,
                 font=("Segoe UI", FS(11), "bold")).pack(anchor="w", pady=(S(6), S(1)))

    _PART_OPTIONS = ["Whole shape"] + [f"Petal {i+1}" for i in range(8)] + ["Center"]

    # ── NEW: Design Options popup (Pre-designed / AI Generated / Import) ───
    def _open_design_options_popup(self):
        self._close_design_options_popup()
        self.root.update_idletasks()
        # Tall enough for 4 full-height action rows + title without clipping Pen.
        W, H = S(400), S(380)
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        # Opaque shell — alpha fades on overrideredirect windows make
        # hit-testing flaky on macOS (clicks miss half the time).
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._design_options_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0, takefocus=0)
        glass.place(x=0, y=0, width=W, height=H)
        self._draw_rounded_rect(
            glass, 4, 4, W - 4, H - 4, radius=S(22),
            fill=BG_CARD, outline=GLASS_BORDER, width=1)
        glass.create_text(
            28, 30, text=self.tr("Choose a design"), anchor="w",
            fill=TEXT_PRIMARY, font=("Segoe UI", FS(15), "bold"))
        glass.create_text(
            28, 54, text=self.tr("Pick how you want to start your rangoli."),
            anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))
        # Decorative only — never steal clicks from the buttons below.
        glass.bind("<Button-1>", lambda e: "break")
        glass.bind("<ButtonRelease-1>", lambda e: "break")

        close_id = glass.create_text(
            W - 26, 26, text="✕", anchor="center",
            fill=TEXT_DIM, font=("Segoe UI", FS(13), "bold"), tags="close_btn")

        def _on_close_enter(e):
            glass.itemconfig(close_id, fill=TEXT_PRIMARY)

        def _on_close_leave(e):
            glass.itemconfig(close_id, fill=TEXT_DIM)

        def _on_close_click(e):
            self._close_design_options_popup()
            return "break"

        glass.tag_bind("close_btn", "<Enter>", _on_close_enter)
        glass.tag_bind("close_btn", "<Leave>", _on_close_leave)
        glass.tag_bind("close_btn", "<Button-1>", _on_close_click)
        glass.tag_bind("close_btn", "<ButtonRelease-1>", lambda e: "break")

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=24, y=78, width=W - 48, height=H - 100)
        body.lift()  # ensure above the glass canvas for hit-testing

        def _pick(action):
            def _go():
                self._close_design_options_popup()
                # Defer so the popup is fully destroyed before the next
                # dialog/file picker grabs focus (avoids swallowed clicks).
                self.root.after(10, action)
            return _go

        for label, _, col in MODES:
            if label == "Robot Test":
                continue
            row = tk.Frame(body, bg=BG_CARD)
            row.pack(fill="x", pady=(S(0), S(12)))
            tk.Label(row, text=self.tr(label), bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(12), "bold")).pack(side="left")

            if label == "Import Designs":
                self._small_btn(row, self.tr("Browse"),
                                 _pick(self.import_design),
                                 ACCENT_AMBER, "#b45309")
            elif label == "AI Generated":
                self._small_btn(row, self.tr("Generate"),
                                 _pick(self.generate_ai_design),
                                 ACCENT_PURP, "#8b5cf6")
            elif label == "Pre-designed":
                self._small_btn(row, self.tr("Gallery"),
                                 _pick(self._open_gallery),
                                 ACCENT_BLUE, "#3b82f6")

        # Draw it yourself — the pen tool used to sit on the canvas toolbar.
        pen_row = tk.Frame(body, bg=BG_CARD)
        pen_row.pack(fill="x", pady=(S(0), S(12)))
        tk.Label(pen_row, text=self.tr("Freehand Pen"), bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self.pen_btn = self._small_btn(
            pen_row, self._pen_btn_label(), _pick(self.toggle_pen_mode),
            self._pen_btn_colour(), "#14b8a6")

        # Pulli Mode controls — only meaningful once a dot grid is loaded, so
        # the row stays out of the way until there is one.
        if self._pulli_guides:
            pulli_row = tk.Frame(body, bg=BG_CARD)
            pulli_row.pack(fill="x", pady=(S(0), S(12)))
            tk.Label(pulli_row,
                     text=self.tr("Pulli grid") + f" — {self._pulli_label}",
                     bg=BG_CARD, fg=ACCENT_CYAN,
                     font=("Segoe UI", FS(11), "bold"),
                     wraplength=S(240), justify="left").pack(side="left")

            def _pulli_toggle(text_of, action):
                lbl = tk.Label(pulli_row, text=text_of(), bg=BG_INPUT,
                               fg=TEXT_PRIMARY, cursor="hand2", padx=S(8),
                               pady=S(4), font=("Segoe UI", FS(10), "bold"))
                lbl.pack(side="right", padx=(S(4), 0))

                def click(_e):
                    action()
                    lbl.configure(text=text_of())
                lbl.bind("<Button-1>", click)

            _pulli_toggle(
                lambda: f"Snap: {'ON' if self.pulli_snap_var.get() else 'off'}",
                self.toggle_pulli_snap)
            _pulli_toggle(
                lambda: f"Dots: {'shown' if self.pulli_show_var.get() else 'hidden'}",
                self.toggle_pulli_guides)

        popup.lift()
        popup.focus_force()
        try:
            popup.grab_set()
        except tk.TclError:
            pass
        # Force geometry + stacking so the bottom Browse row is laid out
        # before the user can click.
        popup.update_idletasks()
        body.lift()

    def _close_design_options_popup(self):
        popup = self._design_options_popup
        self.pen_btn = None      # rebuilt with the popup
        if popup is None:
            return
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self._design_options_popup = None

    # ── Language / i18n ──────────────────────────────────────────────────
    def _load_language_pref(self):
        try:
            with open(LANGUAGE_CONFIG_FILE, "r", encoding="utf-8") as f:
                code = json.load(f).get("lang", "en")
        except Exception:
            return "en"
        return code if code in LANGUAGES else "en"

    def _save_language_pref(self, code):
        try:
            with open(LANGUAGE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"lang": code}, f)
        except Exception:
            pass

    def _load_translation_cache(self):
        try:
            with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_translation_cache(self):
        try:
            with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._translation_cache, f, ensure_ascii=False)
        except Exception:
            pass

    def _gtranslate(self, text, lang):
        """One string, English -> `lang`, via Google's public translate
        endpoint (no key needed — the same unauthenticated call the
        `googletrans` library wraps). Returns None on any failure (offline,
        endpoint shape changed, etc.) so callers can fall back to English."""
        try:
            import urllib.parse
            url = ("https://translate.googleapis.com/translate_a/single"
                   "?client=gtx&sl=en&tl=" + urllib.parse.quote(lang) +
                   "&dt=t&q=" + urllib.parse.quote(text))
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return "".join(chunk[0] for chunk in data[0] if chunk[0])
        except Exception as e:
            if not getattr(self, "_translate_fail_logged", False):
                self._translate_fail_logged = True
                self.log_to_console(
                    f"Language: couldn't reach the translation service "
                    f"({e}). Showing English for anything not already "
                    f"cached in translation_cache.json.", "info")
            return None

    def tr(self, text):
        """Translate a UI string into the currently selected language.
        English (the default / fallback) returns the text unchanged.
        Results are cached to disk per-language so repeat strings (and
        repeat runs) never re-hit the network. Never use this for Log
        panel messages — those stay in English by design."""
        if not text or self.current_lang == "en":
            return text
        lang_cache = self._translation_cache.setdefault(self.current_lang, {})
        if text in lang_cache:
            return lang_cache[text]
        translated = self._gtranslate(text, self.current_lang)
        if translated is None:
            return text
        lang_cache[text] = translated
        self._save_translation_cache()
        return translated

    def register_translatable(self, widget, english_text, kwarg="text", fmt=None):
        """Track a widget/kwarg so `_refresh_translatable_widgets` can
        re-apply the right translation after the user switches language,
        without having to rebuild the whole window. `fmt`, if given, wraps
        the translated word (e.g. to keep a leading icon glyph out of the
        machine-translation call: fmt=lambda t: f"▶ {t}")."""
        value = self.tr(english_text)
        widget.configure(**{kwarg: fmt(value) if fmt else value})
        self._translatable_widgets.append((widget, kwarg, english_text, fmt))
        return widget

    def _refresh_translatable_widgets(self):
        alive = []
        for widget, kwarg, english_text, fmt in self._translatable_widgets:
            try:
                value = self.tr(english_text)
                widget.configure(**{kwarg: fmt(value) if fmt else value})
                alive.append((widget, kwarg, english_text, fmt))
            except tk.TclError:
                pass  # widget was destroyed since it registered
        self._translatable_widgets = alive

    def _on_language_select(self, native_name):
        for code, name in LANGUAGES.items():
            if name == native_name:
                self._set_language(code)
                return

    def _set_language(self, code):
        if code not in LANGUAGES or code == self.current_lang:
            return
        self.current_lang = code
        self._save_language_pref(code)
        self._refresh_translatable_widgets()
        # Popups build their text at open-time from self.tr(), so the
        # simplest correct "immediate" update for them is: close, reopen.
        if self._settings_popup is not None:
            self._open_settings_popup()
        if self._features_popup is not None:
            self._open_features_popup()
        if self._design_options_popup is not None:
            self._open_design_options_popup()

    # ── NEW: Settings popup (Connection / Speed / Robot Test) ──────────────
    def _open_settings_popup(self):
        self._close_settings_popup()
        self.root.update_idletasks()
        # Two Kid Mode rows were added below, so the card is taller — clamped to
        # the screen so it can't run off the bottom on a small display.
        W = S(400)
        H = min(S(724), self.root.winfo_screenheight() - S(40))
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = max(S(10), self.root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._settings_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(20),
                                fill=BG_CARD, outline=ACCENT_PURP, width=2)
        glass.create_text(24, 26, text=self.tr("Settings"), anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", FS(14), "bold"))

        close_lbl = tk.Label(popup, text="\u2715", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(13), "bold"), cursor="hand2")
        close_lbl.place(x=W-38, y=14)
        close_lbl.bind("<Button-1>", lambda e: self._close_settings_popup())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=20, y=54, width=W-40, height=H-74)

        def _row(label_text, sub_text=None):
            row_outer = tk.Frame(body, bg=BG_CARD)
            row_outer.pack(fill="x", pady=(S(0), S(14)))
            top = tk.Frame(row_outer, bg=BG_CARD)
            top.pack(fill="x")
            tk.Label(top, text=self.tr(label_text), bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(12), "bold")).pack(side="left")
            select_slot = tk.Frame(top, bg=BG_CARD)
            select_slot.pack(side="right")
            if sub_text:
                tk.Label(row_outer, text=self.tr(sub_text), bg=BG_CARD, fg=TEXT_DIM,
                         font=("Segoe UI", FS(9)), wraplength=W-60,
                         justify="left").pack(anchor="w", pady=(S(2), S(0)))
            return select_slot

        # 0) Language — pick the UI language; everything except the Log
        # panel switches immediately.
        slot = _row("Language", "Translate the app's text. The Log panel always stays in English.")
        self._lang_name_var = tk.StringVar(value=LANGUAGES[self.current_lang])
        lang_combo = ctk.CTkComboBox(
            slot, variable=self._lang_name_var, values=list(LANGUAGES.values()),
            state="readonly", width=S(170), fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11)),
            command=self._on_language_select)
        lang_combo.pack(side="right")

        # 1) Connection — select a serial port
        slot = _row("Connection", "Choose the serial port your robot is connected on.")
        current_ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo = ctk.CTkComboBox(
            slot, variable=self.port_var, values=current_ports, state="readonly",
            width=S(170), fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11)))
        self.port_combo.pack(side="right")
        self.port_menu = self.port_combo

        # 2) Speed — select a feed rate
        slot = _row("Speed", "Feed rate used when streaming G-code.")
        # Speed / shape / colour option values below are left in English —
        # they're also used as internal keys elsewhere in the code (e.g.
        # compared against literal strings when generating G-code), so
        # translating the stored value would silently break that logic.
        self.feed_combo = ctk.CTkComboBox(
            slot, variable=self.feed_rate,
            values=["Aqua Low", "Super Low", "Low (default)", "Medium", "High"],
            state="readonly",
            width=S(170), fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11)))
        self.feed_combo.pack(side="right")

        # 3) Robot Test — select a test shape
        slot = _row("Robot Test",
                    "Pick a test shape, then click the canvas to place it. "
                    "Line needs two clicks: start point, then end point.")
        self.shape_menu = ctk.CTkComboBox(
            slot, variable=self.shape_type,
            values=["Select", "Line", "Square", "Rectangle", "Circle",
                    "Triangle", "Flower", "Complex Flower"],
            state="readonly", width=S(170),
            fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_GREEN,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(10)),
            command=lambda v: self._on_shape_menu_select(v))
        self.shape_menu.pack(side="right")

        # 4) Kid Mode — a cartoon skin over Learn Mode only. Nothing about the
        # machine, the canvas or any other setting changes, so the same install
        # is a game for a child and stays exactly as it was for an elder.
        slot = _row("Kid Mode",
                    "Cartoon skin for Learn Mode (ages 7-12): mascot, stickers, "
                    "confetti and simple wording. Everything else stays the same.")
        self._color_button(
            slot, "🎨 ON" if self.kid_mode else "OFF", self._toggle_kid_mode,
            ACCENT_PINK if self.kid_mode else GLASS_EDGE,
            width=S(170), height=S(32), font_size=FS(11)).pack(side="right")

        slot = _row("Kid Mode sounds",
                    "Little beeps when a part is finished. Off by default.")
        self._color_button(
            slot, "🔊 ON" if self.kid_sounds else "🔇 MUTED",
            self._toggle_kid_sounds,
            ACCENT_AMBER if self.kid_sounds else GLASS_EDGE,
            width=S(170), height=S(32), font_size=FS(11)).pack(side="right")

        # Learn Mode moved out to the Features popup on the canvas toolbar.

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()

    def _close_settings_popup(self):
        popup = self._settings_popup
        self.port_combo = None
        self.port_menu  = None
        self.feed_combo = None
        self.shape_menu = None
        if popup is None:
            return
        try: popup.destroy()
        except Exception: pass
        self._settings_popup = None

    # ── NEW: Features popup (Learn Mode, …) ────────────────────────────────
    def _open_features_popup(self):
        """Toolbar "Features" button — extra teaching modes live here."""
        if self._features_popup is not None:
            self._close_features_popup()
            return
        self.root.update_idletasks()
        # Six rows: Learn, Picture to Rangoli, Kolam Notebook, Import from
        # Photo, Kolam of the Day, Family Sharing. Progress lives only on the
        # toolbar. Capped to the screen so the last row is never cut off on a
        # short laptop display.
        W, H = S(400), min(S(750), self.root.winfo_screenheight() - S(40))
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._features_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0, takefocus=0)
        glass.place(x=0, y=0, width=W, height=H)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(20),
                                fill=BG_CARD, outline="#0d9488", width=2)
        glass.create_text(24, 26, text=self.tr("Features"), anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", FS(14), "bold"))
        glass.create_text(24, 50, text=self.tr("Extra ways to work with the robot."),
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))
        glass.bind("<Button-1>", lambda e: "break")

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(13), "bold"), cursor="hand2")
        close_lbl.place(x=W-38, y=14)
        close_lbl.bind("<Button-1>", lambda e: self._close_features_popup())

        # Scrollable, because the list has outgrown a short laptop screen: at
        # six features the last row and its description ran off the bottom
        # edge, which made Family Sharing look as though it did not exist.
        scroll_cv = tk.Canvas(popup, bg=BG_CARD, highlightthickness=0)
        scroll_cv.place(x=20, y=74, width=W-40, height=H-94)
        # tk.Misc.tkraise explicitly: Canvas aliases both lift and tkraise to
        # tag_raise, which wants a canvas item and errors on a bare call.
        tk.Misc.tkraise(scroll_cv)
        body = tk.Frame(scroll_cv, bg=BG_CARD)
        scroll_win = scroll_cv.create_window((0, 0), window=body, anchor="nw",
                                             width=W-40)

        def _sync_scroll(_e=None):
            scroll_cv.configure(scrollregion=scroll_cv.bbox("all"))
            scroll_cv.itemconfigure(scroll_win, width=W-40)
        body.bind("<Configure>", _sync_scroll)
        popup.bind("<MouseWheel>", lambda e: scroll_cv.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        # Learn Mode — guided, step-by-step rangoli lessons
        row = tk.Frame(body, bg=BG_CARD)
        row.pack(fill="x", pady=(S(0), S(6)))
        tk.Label(row, text=self.tr("Learn Mode"), bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        on = self.learn_mode_var.get()
        self.learn_btn = self._small_btn(
            row, self.tr("Stop") if on else self.tr("Start"), self._toggle_learn_mode,
            ACCENT_PINK if on else ACCENT_GREEN, "#f472b6" if on else "#4ade80")
        tk.Label(body,
                 text=self.tr("Learn to draw rangoli by hand. The robot draws one "
                      "part, then you copy it with your powder bottle."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Picture to Rangoli — snap a photo, AI sketches a rangoli inspired by it
        pic_row = tk.Frame(body, bg=BG_CARD)
        pic_row.pack(fill="x", pady=(S(14), S(6)))
        tk.Label(pic_row, text=self.tr("Picture to Rangoli"), bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self._small_btn(
            pic_row, self.tr("Start"), self._launch_picture_to_rangoli,
            ACCENT_PINK, self._lighten(ACCENT_PINK, -30))
        tk.Label(body,
                 text=self.tr("Photograph your surroundings and the AI will sketch "
                      "a rangoli inspired by it."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Kolam Notebook — digitize a hand-written kolam puthagam, page by page
        nb_row = tk.Frame(body, bg=BG_CARD)
        nb_row.pack(fill="x", pady=(S(14), S(6)))
        tk.Label(nb_row, text=self.tr("Kolam Notebook"), bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self._small_btn(
            nb_row, self.tr("Start"), self._launch_notebook_digitizer,
            ACCENT_AMBER, self._lighten(ACCENT_AMBER, -30))
        tk.Label(body,
                 text=self.tr("Photograph the pages of a hand-drawn kolam book. "
                      "The pulli dots and lines are read off the page and "
                      "cleaned up into a design the robot can draw. Works "
                      "offline — no internet needed."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Import from Photo — the same tracer, pointed at any photograph:
        # a paper sketch, a chalk drawing, an old photo of a finished kolam.
        imp_row = tk.Frame(body, bg=BG_CARD)
        imp_row.pack(fill="x", pady=(S(14), S(6)))
        tk.Label(imp_row, text=self.tr("Import from Photo"), bg=BG_CARD,
                 fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self._small_btn(
            imp_row, self.tr("Choose"), self._launch_photo_import,
            ACCENT_PURP, self._lighten(ACCENT_PURP, -30))
        tk.Label(body,
                 text=self.tr("Any photo of a pattern — a paper sketch, a chalk "
                      "drawing on the floor, an old photograph of a finished "
                      "kolam. The lines are traced straight onto the canvas. "
                      "Works offline — no internet needed."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Kolam of the Day — the morning page from her digitized notebook
        day_row = tk.Frame(body, bg=BG_CARD)
        day_row.pack(fill="x", pady=(S(14), S(6)))
        tk.Label(day_row, text=self.tr("Kolam of the Day"), bg=BG_CARD,
                 fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self._small_btn(
            day_row, self.tr("Open"), self._launch_daily_kolam,
            ACCENT_CYAN, self._lighten(ACCENT_CYAN, -30))
        tk.Label(body,
                 text=self.tr("A different page from the notebook every "
                      "morning — fuller designs on Fridays, Margazhi and "
                      "festival days, and never yesterday's again."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Family Sharing — the notebook page that travels between two houses
        fam_row = tk.Frame(body, bg=BG_CARD)
        fam_row.pack(fill="x", pady=(S(14), S(6)))
        tk.Label(fam_row, text=self.tr("Family Sharing"), bg=BG_CARD,
                 fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(side="left")
        self._small_btn(
            fam_row, self.tr("Open"), self._launch_family_share,
            ACCENT_PINK, self._lighten(ACCENT_PINK, -30))
        tk.Label(body,
                 text=self.tr("Send a page of the notebook to a grandchild as "
                      "a file or a QR code, with a photo and a voice note, "
                      "and receive the rangoli they drew from it. Carried by "
                      "WhatsApp or a USB stick — no account, no cloud."),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-60, justify="left").pack(anchor="w")

        # Progress & Impact used to be duplicated here — the 📊 Progress button
        # on the canvas toolbar is the single entry point now.

        popup.lift()
        popup.focus_force()
        popup.update_idletasks()
        tk.Misc.tkraise(scroll_cv)
        _sync_scroll()

    def _close_features_popup(self):
        popup = self._features_popup
        self.learn_btn = None
        self._features_popup = None
        if popup is None:
            return
        try: popup.destroy()
        except Exception: pass

    def _launch_picture_to_rangoli(self):
        """Features popup > Picture to Rangoli — close the popup first so the
        capture dialog's grab isn't fighting an already-open Toplevel."""
        self._close_features_popup()
        self.root.after(10, self._open_picture_capture_dialog)

    # ── NEW: Log popup toggle ───────────────────────────────────────────────
    def _on_debug_log_click(self):
        """Debug Log is a plain button now (was a switch) — flip the same
        state var _toggle_log_popup already reads, so its open/close logic
        is untouched."""
        self.log_switch_var.set(not self.log_switch_var.get())
        self._toggle_log_popup()

    def _toggle_log_popup(self):
        if self.log_switch_var.get():
            self._open_log_popup()
        else:
            self._close_log_popup()

    def _open_log_popup(self):
        self._close_log_popup(reset_switch=False)
        self.root.update_idletasks()
        W, H = S(640), S(340)
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        sx = rx + 60
        sy = ry + 90

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_PANEL)
        popup.transient(self.root)

        hdr = tk.Frame(popup, bg=BG_PANEL)
        hdr.pack(fill="x")
        tk.Label(hdr, text="REAL-TIME GRBL CONSOLE", bg=BG_PANEL, fg=ACCENT_PURP,
                 font=("Segoe UI", FS(10), "bold")).pack(side="left", padx=S(10), pady=S(6))
        tk.Button(hdr, text="Clear", bg=BG_PANEL, fg=TEXT_DIM, bd=0,
                  font=("Segoe UI", FS(9)), activebackground=BG_PANEL,
                  command=lambda: self._clear_console()).pack(side="right", padx=S(6))
        close_lbl = tk.Label(hdr, text="\u2715", bg=BG_PANEL, fg=TEXT_DIM,
                             font=("Segoe UI", FS(11), "bold"), cursor="hand2")
        close_lbl.pack(side="right", padx=S(6))
        close_lbl.bind("<Button-1>", lambda e: self._on_log_close_clicked())

        # Create a brand-new Text widget as a real child of this popup (a
        # widget can't be safely moved between Toplevels in Tkinter), and
        # replay the buffered history into it.
        console = tk.Text(popup, bg="#110e2e", fg="#a8d8a8",
                          font=("Consolas", FS(10)), bd=0, highlightthickness=0,
                          insertbackground=ACCENT_GREEN)
        console.tag_config("send", foreground=ACCENT_CYAN)
        console.tag_config("recv", foreground=ACCENT_GREEN)
        console.tag_config("err",  foreground=ACCENT_PINK)
        console.tag_config("info", foreground=ACCENT_AMBER)
        console.pack(fill="both", expand=True, padx=S(2), pady=(S(0), S(2)))
        for msg, tag in self._log_lines:
            console.insert(tk.END, msg + "\n", tag)
        console.see(tk.END)
        self.console = console

        self._log_popup = popup
        self._fade(popup, 0.0, 0.96, 0.1)
        popup.protocol("WM_DELETE_WINDOW", self._on_log_close_clicked)
        popup.lift()

    def _clear_console(self):
        self._log_lines = []
        if self.console is not None:
            try:
                if self.console.winfo_exists():
                    self.console.delete("1.0", tk.END)
            except tk.TclError:
                pass

    def _on_log_close_clicked(self):
        self.log_switch_var.set(False)
        self._close_log_popup()

    def _close_log_popup(self, reset_switch=False):
        popup = self._log_popup
        self.console = None
        if popup is None:
            return
        try: popup.destroy()
        except Exception: pass
        self._log_popup = None
        if reset_switch:
            self.log_switch_var.set(False)

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
            self.part_label.pack(side="left", padx=(S(10), S(4)))
            self.part_combo.pack(side="left")
        else:
            self.part_label.pack_forget()
            self.part_combo.pack_forget()
            self.part_select_var.set("Whole shape")

    def _part_key(self, part_label):
        if part_label == "Center":
            return 8
        return int(part_label.split()[1]) - 1

    def _on_part_select(self, value):
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
        self._close_settings_popup()

    def _open_gallery(self):
        self.show_gallery_popup()

    def _on_slider(self, val):
        v = int(float(val))
        self.size_val.set(v)
        self.size_display.config(text=str(v))
        self.update_shape_size(v)

    # ── Console ───────────────────────────────────────────────────────────────
    def log_to_console(self, msg, tag="info"):
        self._log_lines.append((msg, tag))
        if self.console is not None:
            try:
                if self.console.winfo_exists():
                    self.console.insert(tk.END, msg + "\n", tag)
                    self.console.see(tk.END)
            except tk.TclError:
                pass

    # ── Context menu ──────────────────────────────────────────────────────────
    def setup_context_menu(self):
        self.context_menu = tk.Menu(self.root, tearoff=0, bg=BG_CARD, fg=TEXT_PRIMARY,
                                    activebackground=ACCENT_BLUE, activeforeground="#ffffff",
                                    font=("Segoe UI", FS(10)))
        self.context_menu.add_command(label="Move",   command=self.start_move)
        self.context_menu.add_command(label="Delete", command=self.delete_shape)

    # ── Port polling ──────────────────────────────────────────────────────────
    def poll_ports(self):
        current_ports = [p.device for p in serial.tools.list_ports.comports()]
        if current_ports != self.last_ports:
            if self.port_combo is not None:
                try:
                    self.port_combo.configure(values=current_ports)
                except tk.TclError:
                    pass
            if current_ports:
                new = list(set(current_ports) - set(self.last_ports))
                chosen = (new[0] if new else
                          (current_ports[0] if self.port_var.get() not in current_ports
                           else self.port_var.get()))
                self.port_var.set(chosen)
                if self.port_combo is not None:
                    try:
                        self.port_combo.set(chosen)
                    except tk.TclError:
                        pass
            else:
                self.port_var.set("")
                if self.port_combo is not None:
                    try:
                        self.port_combo.set("")
                    except tk.TclError:
                        pass
            self.last_ports = current_ports
        self.root.after(1000, self.poll_ports)

    # ── Coordinate helpers ────────────────────────────────────────────────────
    def to_machine(self, x, y):
        mx = ((x - MARGIN_L) / GRAPH_W) * MAX_X
        my = ((CANVAS_H - MARGIN_B - y) / GRAPH_H) * MAX_Y
        return mx, my

    def from_machine(self, mx, my):
        x = MARGIN_L + (mx / MAX_X) * GRAPH_W
        y = CANVAS_H - MARGIN_B - (my / MAX_Y) * GRAPH_H
        return x, y

    # ── Predesigned rangoli (Cmd+J) ─────────────────────────────────────────────
    def _rangoli_canvas_paths(self):
        """Reconstruct stroke paths (canvas coords) from RANGOLI_GCODE."""
        paths = []
        current = None
        last_x = last_y = 0.0
        for line in RANGOLI_GCODE:
            if line in ("$X", "G21", "G90") or line.startswith("F"):
                continue
            if line == "M3":
                current = [self.from_machine(last_x, last_y)]
            elif line == "M5":
                if current:
                    paths.append(current)
                current = None
            elif line.startswith("G1 Z"):
                continue
            elif line.startswith("G1 X") and "Y" in line:
                parts = line.split()
                last_x = float(parts[1][1:])
                last_y = float(parts[2][1:])
                if current is not None:
                    current.append(self.from_machine(last_x, last_y))
            elif line.startswith("G1 X"):
                last_x = float(line.split()[1][1:])
            elif line.startswith("G1 Y"):
                last_y = float(line.split()[1][1:])
        return paths

    def load_and_send_rangoli(self, event=None):
        """Cmd+J — load the predesigned rangoli onto the canvas. Sending is
        manual: it just arms the exact same G-code (verbatim) so the normal
        Send button streams it byte-for-byte instead of regenerating it."""
        # Debounce: the <Command-j> binding and the KeyPress fallback can
        # both fire for one keystroke.
        now = time.time()
        if now - getattr(self, "_last_rangoli_load", 0) < 0.5:
            return "break"
        self._last_rangoli_load = now

        if self.is_sending:
            self.log_to_console("Already sending — wait for the current job "
                                 "to finish.", "err")
            return "break"

        if getattr(self, "_sim_running", False):
            self._stop_simulation()
        self._close_edit_popup()
        self.shapes = []
        self.selected_shape_index = None
        self.canvas.delete("shape")
        if self._ai_fx_running:
            self.toggle_ai_effects()

        self.shapes.append({
            'type':   'Imported',
            'paths':  self._rangoli_canvas_paths(),
            'x':      MARGIN_L + GRAPH_W // 2,
            'y':      MARGIN_T + GRAPH_H // 2,
            'size':   0,
            'colour': None,
        })
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()
        self._pending_raw_gcode = list(RANGOLI_GCODE)
        self.log_to_console(
            "Loaded predesigned rangoli — click Send to stream it.", "info")
        return "break"

    # ── Grid ──────────────────────────────────────────────────────────────────
    def draw_grid(self):
        c  = self.canvas
        x0, x1 = MARGIN_L, CANVAS_W - MARGIN_R
        y0, y1 = MARGIN_T, CANVAS_H - MARGIN_B

        c.create_rectangle(x0, y0, x1, y1, fill=CANVAS_BG, outline="", tags="grid")

        DOT_COLOR  = "#c8c8e0"
        MAJOR_DOT  = "#9090c0"
        r_minor, r_major = S(1), S(2)
        # Tick spacing follows the bed size instead of being hardcoded. The
        # old fixed 5mm dots / 10mm labels only made sense on a large bed —
        # on a 4mm one they collapse to a single dot and a lone "0" label.
        span     = max(MAX_X, MAX_Y)
        dot_step = 1 if span <= 10 else 5
        lbl_step = 1 if span <= 10 else 10
        for ix in range(0, MAX_X + 1, dot_step):
            for iy in range(0, MAX_Y + 1, dot_step):
                px  = x0 + (ix / MAX_X) * GRAPH_W
                py  = y1 - (iy / MAX_Y) * GRAPH_H
                major = (ix % lbl_step == 0) and (iy % lbl_step == 0)
                r   = r_major if major else r_minor
                col = MAJOR_DOT if major else DOT_COLOR
                c.create_oval(px-r, py-r, px+r, py+r,
                              fill=col, outline="", tags="grid")

        for ix in range(0, MAX_X + 1, lbl_step):
            px = x0 + (ix / MAX_X) * GRAPH_W
            c.create_text(px, y1 + S(14), text=str(ix), fill="#8080a0",
                          font=("Consolas", FS(8)), tags="grid")
        for iy in range(0, MAX_Y + 1, lbl_step):
            py = y1 - (iy / MAX_Y) * GRAPH_H
            c.create_text(x0 - S(8), py, text=str(iy), fill="#8080a0",
                          font=("Consolas", FS(8)), anchor="e", tags="grid")

        c.create_line(x0, y1, x1, y1, fill="#c084fc", width=S(2), tags="grid")
        c.create_line(x0, y0, x0, y1, fill="#c084fc", width=S(2), tags="grid")
        c.create_text((x0 + x1) // 2, CANVAS_H - S(4), text="X (mm)",
                      fill="#7c3aed", font=("Segoe UI", FS(9), "bold"), tags="grid")
        c.create_text(S(6), (y0 + y1) // 2,        text="Y",
                      fill="#7c3aed", font=("Segoe UI", FS(9), "bold"), tags="grid")
        c.create_text(S(6), (y0 + y1) // 2 - S(14), text="(mm)",
                      fill="#7c3aed", font=("Segoe UI", FS(7)), tags="grid")

        ox, oy = x0, y1
        for hr, hcol in [(S(22), "#e9d5ff"), (S(15), "#c084fc"), (S(10), "#7c3aed")]:
            c.create_oval(ox-hr, oy-hr, ox+hr, oy+hr, fill="", outline=hcol,
                          width=1, tags="grid")
        arm, ah = S(40), S(6)
        c.create_line(ox, oy-S(12), ox, oy-arm, fill="#7c3aed", width=S(2), tags="grid")
        c.create_polygon(ox-ah, oy-arm+ah*2, ox+ah, oy-arm+ah*2, ox, oy-arm,
                         fill="#7c3aed", outline="", tags="grid")
        c.create_line(ox+S(12), oy, ox+arm, oy, fill="#7c3aed", width=S(2), tags="grid")
        c.create_polygon(ox+arm-ah*2, oy-ah, ox+arm-ah*2, oy+ah, ox+arm, oy,
                         fill="#7c3aed", outline="", tags="grid")
        r = S(11)
        c.create_oval(ox-r, oy-r, ox+r, oy+r, fill="#7c3aed",
                      outline="#ffffff", width=S(2), tags="grid")
        rd = S(5)
        c.create_oval(ox-rd, oy-rd, ox+rd, oy+rd, fill="#ffffff", outline="",
                      tags="grid")
        c.create_text(ox-S(4), oy+S(16), text="(0,0)", fill="#7c3aed",
                      font=("Segoe UI", FS(9), "bold"), tags="grid")
        c.create_text(ox+arm+S(14), oy-S(6),   text="X+", fill="#7c3aed",
                      font=("Segoe UI", FS(8), "bold"), tags="grid")
        c.create_text(ox+S(14), oy-arm-S(8),   text="Y+", fill="#7c3aed",
                      font=("Segoe UI", FS(8), "bold"), tags="grid")

    # ── DXF IMPORT ────────────────────────────────────────────────────────────
    def _raw_scan_dxf_lwpolylines(self, path):
        """Read LWPOLYLINE vertices straight off the group-code stream.

        Bypasses ezdxf's entity loader entirely, so it tolerates files that
        skip the AcDbEntity/AcDbPolyline subclass markers ezdxf requires.
        Only handles plain (10, 20) x/y vertex pairs — enough for the
        simple polyline-only rangoli exports this app deals with.
        """
        try:
            with open(path, "r", errors="replace") as fh:
                raw = fh.read().splitlines()
        except OSError:
            return []

        tags = []
        i = 0
        while i + 1 < len(raw):
            try:
                tags.append((int(raw[i].strip()), raw[i + 1].strip()))
            except ValueError:
                pass
            i += 2

        paths, xs, ys, in_poly = [], [], [], False

        def _flush():
            if xs and len(xs) == len(ys):
                pts = list(zip(xs, ys))
                if len(pts) >= 2:
                    paths.append(pts)

        for code, val in tags:
            if code == 0:
                if in_poly:
                    _flush()
                xs, ys = [], []
                in_poly = (val == "LWPOLYLINE")
                continue
            if not in_poly:
                continue
            if code == 10:
                try: xs.append(float(val))
                except ValueError: pass
            elif code == 20:
                try: ys.append(float(val))
                except ValueError: pass
        if in_poly:
            _flush()
        return paths

    def _parse_dxf_file(self, path):
        """Load a DXF file into deduped, chained stroke paths.

        Returns (raw_paths, error_message). raw_paths is None on failure.
        """
        try:
            import ezdxf
            from ezdxf import path as ezpath
        except ImportError:
            return None, "ezdxf is required.\nRun: pip install ezdxf"

        raw_paths = None
        try:
            doc = ezdxf.readfile(path)
            msp = doc.modelspace()
            raw_paths = []
            for entity in msp:
                try:
                    p = ezpath.make_path(entity)
                except Exception:
                    continue
                pts = [(v.x, v.y) for v in p.flattening(0.05)]
                if len(pts) >= 2:
                    raw_paths.append(pts)
        except Exception:
            raw_paths = None

        if not raw_paths:
            # Strict loader bailed or found nothing usable (e.g. a LWPOLYLINE
            # missing its AcDbEntity/AcDbPolyline subclass markers, which
            # ezdxf refuses outright, even in recovery mode). Fall back to a
            # bare group-code scan that reads LWPOLYLINE vertices directly,
            # ignoring the missing subclass structure entirely.
            raw_paths = self._raw_scan_dxf_lwpolylines(path)

        if not raw_paths:
            return None, "No drawable entities found in DXF."

        all_x = [x for pts in raw_paths for x, _ in pts]
        all_y = [y for pts in raw_paths for _, y in pts]
        diag = math.hypot(max(all_x) - min(all_x), max(all_y) - min(all_y)) or 1.0
        TOL = max(diag * 0.01, 1e-6)

        def _resample(pts, samples=12):
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
        raw_paths = deduped

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

        return _chain(raw_paths), None

    def import_design(self):
        self.selected_preset.set("")

        path = filedialog.askopenfilename(
            title="Select Design DXF",
            filetypes=[("DXF Files", "*.dxf")])
        if not path:
            return

        self.log_to_console(f"Loading DXF design: {os.path.basename(path)}", "info")

        raw_paths, err = self._parse_dxf_file(path)
        if err:
            if "ezdxf" in err:
                messagebox.showerror("Missing Library", err)
            else:
                self.log_to_console(err, "err")
            return

        self._show_dxf_preview_popup(os.path.basename(path), raw_paths)

    def _finalize_dxf_import(self, filename, raw_paths, path_colours=None):
        if not raw_paths:
            self.log_to_console("Import cancelled — no components left.", "err")
            return

        path_colours = path_colours or {}

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
            cy = off_y + (GRAPH_H - (y - min_y) * scale)
            return cx, cy

        canvas_paths = [[dxf_to_canvas(x, y) for x, y in pts] for pts in raw_paths]

        removed = sum(1 for s in self.shapes if s['type'] == 'Imported')
        self.shapes = [s for s in self.shapes if s['type'] != 'Imported']
        if removed:
            self.log_to_console(
                f"Replaced previous imported design ({removed} removed).", "info")

        # Index-keyed colours from the preview (only assigned strokes)
        indexed_colours = {int(k): v for k, v in path_colours.items()
                           if v and int(k) < len(canvas_paths)}

        if indexed_colours and not self.multi_colour_var.get():
            self.multi_colour_var.set(True)
            self._on_multi_colour_toggle()

        default_colour = self.shape_colour_var.get() if self.multi_colour_var.get() else None
        shape = {
            'type':   'Imported',
            'paths':  canvas_paths,
            'x':      MARGIN_L + GRAPH_W // 2,
            'y':      MARGIN_T  + GRAPH_H // 2,
            'size':   0,
            'colour': default_colour,
        }
        if indexed_colours:
            shape['path_colours'] = indexed_colours
            # Fall back default so uncoloured strokes still have a base colour
            if not shape['colour']:
                shape['colour'] = next(iter(COLOUR_PALETTE))
        self.shapes.append(shape)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

        total_pts = sum(len(p) for p in canvas_paths)
        n_col = len(indexed_colours)
        col_note = f", {n_col} multi-colour stroke(s)" if n_col else ""
        self.log_to_console(
            f"Imported {filename}: {len(canvas_paths)} stroke paths "
            f"({total_pts} points{col_note}). Ready to generate G-code.", "recv")

    def _show_dxf_preview_popup(self, filename, raw_paths):
        self.root.update_idletasks()

        W, H = S(640), S(760)
        CW    = 560
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

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(24),
                                fill=BG_CARD, outline=ACCENT_PURP, width=2)
        glass.create_text(28, 30, text=f"Preview: {filename}", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", FS(14), "bold"))
        glass.create_text(
            28, 54,
            text="Edit → click a stroke → Delete or Make multi-colour.",
            anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))

        prev_x = (W - CW) // 2
        prev_y = 76
        preview = tk.Canvas(popup, width=CW, height=CW, bg=CANVAS_BG, highlightthickness=0)
        preview.place(x=prev_x, y=prev_y)

        status_lbl = tk.Label(popup, text="", bg=BG_CARD, fg=TEXT_DIM,
                              font=("Segoe UI", FS(9), "bold"))
        status_lbl.place(x=28, y=prev_y + CW + 10)

        # path_colours keyed by id(pts list) while editing, remapped to indices on confirm
        state = {
            'remaining': list(raw_paths),
            'path_colours': {},
            'edit': False,
            'items': [],
            'action_frame': None,
            'selected_pts': None,
        }

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

        def dismiss_action_menu():
            fr = state.get('action_frame')
            if fr is not None:
                try:
                    fr.destroy()
                except Exception:
                    pass
                state['action_frame'] = None
            state['selected_pts'] = None

        def stroke_colour(pts):
            name = state['path_colours'].get(id(pts))
            if name:
                return COLOUR_PALETTE.get(name, ACCENT_PINK)
            return ACCENT_PINK

        def redraw_preview():
            preview.delete("stroke")
            state['items'] = []
            for pts in state['remaining']:
                flat = [c for x, y in pts for c in to_preview(x, y)]
                if len(flat) < 4:
                    continue
                fill = stroke_colour(pts)
                # Slightly thicker when a colour is assigned so it stands out
                lw = 3 if id(pts) in state['path_colours'] else 2
                item = preview.create_line(flat, fill=fill, width=lw,
                                           smooth=True, tags="stroke")
                state['items'].append((item, pts))
            n_col = len(state['path_colours'])
            extra = ""
            if state['edit']:
                extra = "  —  click a stroke: Delete or Make multi-colour"
            if n_col:
                extra += f"  ·  {n_col} coloured"
            status_lbl.config(
                text=f"{len(state['remaining'])} component(s){extra}")

        def delete_selected_stroke():
            pts = state.get('selected_pts')
            dismiss_action_menu()
            if pts is None:
                return
            try:
                state['remaining'].remove(pts)
            except ValueError:
                return
            state['path_colours'].pop(id(pts), None)
            redraw_preview()

        def apply_colour(colour_name):
            pts = state.get('selected_pts')
            dismiss_action_menu()
            if pts is None:
                return
            state['path_colours'][id(pts)] = colour_name
            redraw_preview()

        def show_colour_picker(anchor_x, anchor_y):
            """Replace action buttons with a compact colour palette."""
            # Keep the selected stroke while swapping the floating menu
            kept_pts = state.get('selected_pts')
            fr_old = state.get('action_frame')
            if fr_old is not None:
                try:
                    fr_old.destroy()
                except Exception:
                    pass
                state['action_frame'] = None
            state['selected_pts'] = kept_pts

            fr = tk.Frame(popup, bg=BG_PANEL, highlightbackground=ACCENT_PURP,
                          highlightthickness=2, bd=0, padx=S(6), pady=S(6))
            state['action_frame'] = fr
            tk.Label(fr, text="Pick a colour", bg=BG_PANEL, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(9), "bold")).pack(anchor="w", pady=(S(0), S(4)))
            row = tk.Frame(fr, bg=BG_PANEL)
            row.pack()
            for name, hex_col in COLOUR_PALETTE.items():
                sw = tk.Canvas(row, width=S(22), height=S(22), bg=BG_PANEL,
                               highlightthickness=1, highlightbackground="#ffffff")
                sw.create_rectangle(2, 2, 20, 20, fill=hex_col, outline=hex_col)
                sw.pack(side="left", padx=S(2))
                sw.bind("<Button-1>", lambda _e, n=name: apply_colour(n))
                sw.configure(cursor="hand2")
                sw.bind("<Enter>", lambda _e, n=name: status_lbl.config(
                    text=f"Colour: {n}"))
            tk.Button(
                fr, text="Cancel", command=dismiss_action_menu,
                bg=BG_INPUT, fg=TEXT_DIM, relief="flat",
                font=("Segoe UI", FS(8)), cursor="hand2",
                activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
            ).pack(anchor="e", pady=(S(6), S(0)))
            fr.update_idletasks()
            fw, fh = fr.winfo_reqwidth(), fr.winfo_reqheight()
            px = max(8, min(anchor_x, W - fw - 8))
            py = max(8, min(anchor_y, H - fh - 8))
            fr.place(x=px, y=py)
            fr.lift()

        def show_action_menu(pts, canvas_x, canvas_y):
            """Small popup: Delete | Make multi-colour."""
            dismiss_action_menu()
            state['selected_pts'] = pts
            fr = tk.Frame(popup, bg=BG_PANEL, highlightbackground=ACCENT_PINK,
                          highlightthickness=2, bd=0, padx=S(6), pady=S(6))
            state['action_frame'] = fr

            def _act_btn(parent, text, cmd, accent):
                b = tk.Button(
                    parent, text=text, command=cmd,
                    bg=accent, fg="#ffffff", relief="flat",
                    font=("Segoe UI", FS(9), "bold"), cursor="hand2",
                    activebackground=accent, activeforeground="#ffffff",
                    padx=S(10), pady=S(4))
                b.pack(side="left", padx=S(3))
                return b

            # Map preview-canvas coords → popup coords
            ax = prev_x + canvas_x + 8
            ay = prev_y + canvas_y + 8

            _act_btn(fr, "Delete", delete_selected_stroke, ORIGIN_RED)
            _act_btn(fr, "Make multi-colour",
                     lambda: show_colour_picker(ax, ay), ACCENT_PURP)
            tk.Button(
                fr, text="✕", command=dismiss_action_menu,
                bg=BG_INPUT, fg=TEXT_DIM, relief="flat",
                font=("Segoe UI", FS(9), "bold"), cursor="hand2",
                activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
                padx=S(6), pady=S(4),
            ).pack(side="left", padx=(S(6), S(0)))

            fr.update_idletasks()
            fw, fh = fr.winfo_reqwidth(), fr.winfo_reqheight()
            px = max(8, min(ax, W - fw - 8))
            py = max(8, min(ay, H - fh - 8))
            fr.place(x=px, y=py)
            fr.lift()

            # Highlight the selected stroke thicker
            for item_id, p in state['items']:
                if p is pts:
                    preview.itemconfigure(item_id, width=4)
                    break

        def on_preview_click(e):
            if not state['edit'] or not state['items']:
                return
            closest = preview.find_closest(e.x, e.y)
            if not closest:
                return
            item_id = closest[0]
            for entry in state['items']:
                if entry[0] == item_id:
                    show_action_menu(entry[1], e.x, e.y)
                    return
            dismiss_action_menu()

        preview.bind("<Button-1>", on_preview_click)
        redraw_preview()

        edit_btn = ctk.CTkButton(
            popup, text="Edit: OFF", width=S(110), height=S(32),
            fg_color="transparent", hover_color="#f1f5f9",
            border_width=1, border_color=GLASS_EDGE,
            text_color=TEXT_PRIMARY, font=("Segoe UI", FS(10), "bold"))
        def toggle_edit():
            state['edit'] = not state['edit']
            dismiss_action_menu()
            edit_btn.configure(
                text=f"Edit: {'ON' if state['edit'] else 'OFF'}",
                border_color=ACCENT_PINK if state['edit'] else GLASS_EDGE,
                text_color=ACCENT_PINK if state['edit'] else TEXT_PRIMARY)
            preview.configure(cursor="hand2" if state['edit'] else "arrow")
            redraw_preview()
        edit_btn.configure(command=toggle_edit)
        edit_btn.place(x=28, y=H - 56)

        cancel_btn = ctk.CTkButton(
            popup, text="Cancel", width=S(110), height=S(32),
            fg_color="transparent", hover_color="#f1f5f9",
            border_width=1, border_color=GLASS_EDGE,
            text_color=TEXT_DIM, font=("Segoe UI", FS(10), "bold"),
            command=lambda: self._dxf_preview_cancel())
        cancel_btn.place(x=W - 260, y=H - 56)

        confirm_btn = ctk.CTkButton(
            popup, text="Confirm Import", width=S(120), height=S(32),
            fg_color="transparent", hover_color="#d1fae5",
            border_width=1, border_color=ACCENT_GREEN,
            text_color=ACCENT_GREEN, font=("Segoe UI", FS(10), "bold"),
            command=lambda: self._dxf_preview_confirm(filename, state))
        confirm_btn.place(x=W - 140, y=H - 56)

        self._dxf_preview_popup = popup
        self._fade(popup, 0.0, 0.96, 0.08)
        popup.lift()
        popup.focus_force()
        popup.grab_set()

    def _dxf_preview_confirm(self, filename, state):
        remaining = state['remaining']
        # Remap id(pts) colours → path index for the final shape
        indexed = {}
        for idx, pts in enumerate(remaining):
            col = state.get('path_colours', {}).get(id(pts))
            if col:
                indexed[idx] = col
        self._close_dxf_preview_popup()
        self._finalize_dxf_import(filename, remaining, path_colours=indexed)

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

    # ── KOLAM NOTEBOOK DIGITIZER ("Notebook → Code") ─────────────────────────
    # Photograph the pages of a hand-written kolam puthagam and turn each page
    # into strokes the robot can draw. Everything below runs locally with
    # OpenCV + numpy: no network call, no API key, nothing leaves the laptop.
    # That matters twice over — grandma's book stays private, and the demo
    # still works at a judging table with no internet.

    def _launch_notebook_digitizer(self):
        """Features popup > Kolam Notebook. Close the popup first so the
        capture dialog's grab isn't fighting an already-open Toplevel."""
        self._close_features_popup()
        self.root.after(10, self._start_notebook_session)

    def _start_notebook_session(self):
        dlg = ctk.CTkInputDialog(
            text="Whose notebook is this?\n(e.g. Paati's Kolam Puthagam)",
            title="Kolam Notebook — New Sitting")
        book = (dlg.get_input() or "").strip()
        if not book:
            return
        start_page = self._notebook_next_page(book)
        self._notebook_session = {"book": book, "page": start_page, "saved": 0}
        if start_page > 1:
            self.log_to_console(
                f"Kolam Notebook: '{book}' already has {start_page - 1} page(s) "
                f"— continuing at page {start_page}.", "info")
        self._open_notebook_capture_dialog()

    def _notebook_next_page(self, book):
        """Resume numbering where an earlier sitting left off."""
        highest = 0
        for _name, _full, data in self._load_saved_designs():
            nb = data.get('notebook')
            if isinstance(nb, dict) and nb.get('book') == book:
                try:
                    highest = max(highest, int(nb.get('page', 0)))
                except (TypeError, ValueError):
                    pass
        return highest + 1

    def _open_notebook_capture_dialog(self):
        """Live webcam view over the notebook page, plus an upload option for
        pages that were photographed on a phone. Same webcam plumbing as
        Picture to Rangoli, but it loops: every saved page comes straight back
        here for the next one, so a whole book goes in one sitting."""
        session = self._notebook_session
        if session is None:
            return
        existing = self._notebook_capture_dlg
        if existing is not None:
            try:
                existing.lift()
                existing.focus_force()
                return
            except Exception:
                self._notebook_capture_dlg = None

        try:
            import cv2
        except ImportError:
            self.log_to_console(
                "Kolam Notebook: OpenCV not available — "
                "run: pip install opencv-python", "err")
            return

        from PIL import Image, ImageTk

        cap = cv2.VideoCapture(0, self._camera_backend())
        if not cap.isOpened():
            try: cap.release()
            except Exception: pass
            cap = None
            self.log_to_console(
                "Kolam Notebook: no webcam found — you can still upload a "
                "photo of the page.", "err")

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Kolam Notebook — {session['book']}")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._notebook_capture_dlg = dlg
        state = {"cap": cap, "frozen": None, "after_id": None,
                 "live": cap is not None}

        def on_close():
            state["live"] = False
            if state["after_id"] is not None:
                try: dlg.after_cancel(state["after_id"])
                except Exception: pass
            if state["cap"] is not None:
                try: state["cap"].release()
                except Exception: pass
            self._notebook_capture_dlg = None
            try: dlg.destroy()
            except Exception: pass

        def on_finish():
            saved = session.get("saved", 0)
            book  = session.get("book", "notebook")
            on_close()
            self._notebook_session = None
            if saved:
                self.log_to_console(
                    f"Kolam Notebook: finished '{book}' — {saved} page(s) "
                    f"digitized into the gallery.", "recv")
                self.show_hint_popup(
                    f"{saved} page(s) saved under \U0001f4d6 Notebook in the gallery")

        dlg.protocol("WM_DELETE_WINDOW", on_finish)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))

        head = tk.Label(pad, text=f"Page {session['page']} — {session['book']}",
                        bg=BG_CARD, fg=TEXT_PRIMARY,
                        font=("Segoe UI", FS(13), "bold"))
        head.pack(anchor="w")
        tk.Label(pad, text="Lay the page flat, fill the frame with it, and keep "
                           "the light even. Nothing is uploaded anywhere — the "
                           "page is read on this computer.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                 justify="left", wraplength=S(420)).pack(anchor="w",
                                                         pady=(S(2), S(10)))

        view_w, view_h = 420, 315
        video_frame = tk.Frame(pad, bg="#000000", width=view_w, height=view_h)
        video_frame.pack_propagate(False)
        video_frame.pack()
        video_lbl = tk.Label(video_frame, bg="#000000", fg=TEXT_DIM,
                             font=("Segoe UI", FS(10)))
        video_lbl.pack(fill="both", expand=True)
        if cap is None:
            video_lbl.configure(
                text="No webcam available.\nUse \U0001f4c1 Upload Page Photo below.")

        def update_frame():
            if not state["live"]:
                return
            ok, frame = state["cap"].read()
            if ok and frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb).resize((view_w, view_h))
                state["photo_img"] = ImageTk.PhotoImage(pil_img)
                video_lbl.configure(image=state["photo_img"])
            state["after_id"] = dlg.after(66, update_frame)

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(S(12), S(0)))
        alt_row = tk.Frame(pad, bg=BG_CARD)
        alt_row.pack(fill="x", pady=(S(6), S(0)))

        def do_capture():
            if state["cap"] is None:
                return
            ok, frame = state["cap"].read()
            if not ok or frame is None:
                self.log_to_console(
                    "Kolam Notebook: couldn't grab a frame — try again.", "err")
                return
            state["frozen"] = frame
            state["live"] = False
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb).resize((view_w, view_h))
            state["photo_img"] = ImageTk.PhotoImage(pil_img)
            video_lbl.configure(image=state["photo_img"])
            capture_btn.pack_forget()
            retake_btn.pack(side="left", expand=True, fill="x", padx=(S(0), S(6)))
            use_btn.pack(side="left", expand=True, fill="x", padx=(S(6), S(0)))

        def do_retake():
            state["frozen"] = None
            state["live"] = True
            retake_btn.pack_forget()
            use_btn.pack_forget()
            capture_btn.pack(fill="x")
            update_frame()

        def do_use():
            frame = state["frozen"]
            if frame is None:
                return
            on_close()
            self._open_notebook_crop_dialog(frame)

        def do_upload():
            path = filedialog.askopenfilename(
                title="Choose a photo of a notebook page",
                filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"),
                           ("All files", "*.*")])
            if not path:
                return
            frame = None
            try:
                import numpy as np
                # np.fromfile + imdecode, not imread: imread silently fails on
                # non-ASCII paths on Windows.
                buf = np.fromfile(path, dtype=np.uint8)
                frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception as e:
                self.log_to_console(f"Kolam Notebook: could not open the "
                                    f"photo — {e}", "err")
                return
            if frame is None:
                self.log_to_console(
                    f"Kolam Notebook: '{os.path.basename(path)}' isn't a "
                    f"readable image.", "err")
                return
            on_close()
            self._open_notebook_crop_dialog(frame)

        capture_btn = ctk.CTkButton(
            btn_row, text="📷 Capture Page", command=do_capture,
            fg_color=ACCENT_AMBER if cap is not None else BG_INPUT,
            hover_color=self._lighten(ACCENT_AMBER, -30),
            text_color="#0d0b2b" if cap is not None else TEXT_DIM,
            font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8),
            state="normal" if cap is not None else "disabled")
        capture_btn.pack(fill="x")

        retake_btn = ctk.CTkButton(
            btn_row, text="Retake", command=do_retake,
            fg_color=BG_INPUT, hover_color=GLASS_EDGE,
            text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8))
        use_btn = ctk.CTkButton(
            btn_row, text="Use This Page →", command=do_use,
            fg_color=ACCENT_CYAN, hover_color=self._lighten(ACCENT_CYAN, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8))

        ctk.CTkButton(
            alt_row, text="📁 Upload Page Photo", command=do_upload,
            fg_color="transparent", hover_color=BG_INPUT,
            border_width=1, border_color=GLASS_EDGE, text_color=TEXT_PRIMARY,
            font=("Segoe UI", FS(10), "bold"), height=S(32),
            corner_radius=S(8)).pack(side="left", expand=True, fill="x",
                                     padx=(S(0), S(6)))
        ctk.CTkButton(
            alt_row, text="Finish Notebook", command=on_finish,
            fg_color="transparent", hover_color=BG_INPUT,
            border_width=1, border_color=GLASS_EDGE, text_color=TEXT_DIM,
            font=("Segoe UI", FS(10), "bold"), height=S(32),
            corner_radius=S(8)).pack(side="left", expand=True, fill="x",
                                     padx=(S(6), S(0)))

        if cap is not None:
            update_frame()

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _open_notebook_crop_dialog(self, frame):
        """Let the user say which part of the photo is actually the kolam.

        No amount of cleverness reliably tells a kolam from the decorative
        border printed around it, the caption under it, or the facing page —
        they are all just ink. One drag settles it, and it costs a second.
        """
        try:
            import cv2
        except ImportError:
            return self._digitize_notebook_frame(frame)
        from PIL import Image, ImageTk

        ih, iw = frame.shape[:2]
        view_w = 560
        view_h = max(1, min(420, int(round(view_w * ih / float(max(1, iw))))))
        if view_h == 420:
            view_w = max(1, int(round(view_h * iw / float(max(1, ih)))))

        dlg = tk.Toplevel(self.root)
        dlg.title("Kolam Notebook — Choose the area to trace")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))
        tk.Label(pad, text="Drag a box around just the kolam",
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold")).pack(anchor="w")
        tk.Label(pad, text="Leave out printed borders, captions and the edge "
                           "of the page — those get traced as part of the "
                           "design otherwise. Skip this to use the whole "
                           "picture.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                 justify="left", wraplength=S(view_w)).pack(
            anchor="w", pady=(S(2), S(10)))

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        shown = ImageTk.PhotoImage(
            Image.fromarray(rgb).resize((view_w, view_h)))
        cv = tk.Canvas(pad, width=view_w, height=view_h, bg="#000000",
                       highlightthickness=0, cursor="cross")
        cv.pack()
        cv.create_image(0, 0, image=shown, anchor="nw")
        cv.image = shown                      # keep a reference alive

        sel = {"x0": None, "y0": None, "x1": None, "y1": None, "rect": None}

        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        def on_press(e):
            sel["x0"], sel["y0"] = clamp(e.x, 0, view_w), clamp(e.y, 0, view_h)
            sel["x1"], sel["y1"] = sel["x0"], sel["y0"]
            if sel["rect"] is not None:
                cv.delete(sel["rect"])
            sel["rect"] = cv.create_rectangle(
                sel["x0"], sel["y0"], sel["x0"], sel["y0"],
                outline=ACCENT_CYAN, width=2)

        def on_drag(e):
            if sel["rect"] is None:
                return
            sel["x1"], sel["y1"] = clamp(e.x, 0, view_w), clamp(e.y, 0, view_h)
            cv.coords(sel["rect"], sel["x0"], sel["y0"], sel["x1"], sel["y1"])
            use_btn.configure(state="normal")

        cv.bind("<Button-1>", on_press)
        cv.bind("<B1-Motion>", on_drag)
        cv.bind("<ButtonRelease-1>", on_drag)

        def finish(cropped):
            try: dlg.grab_release()
            except Exception: pass
            try: dlg.destroy()
            except Exception: pass
            self._digitize_notebook_frame(cropped)

        def use_selection():
            if sel["x0"] is None:
                return finish(frame)
            x0, x1 = sorted((sel["x0"], sel["x1"]))
            y0, y1 = sorted((sel["y0"], sel["y1"]))
            # Back to the photo's own pixels, with a little breathing room so
            # a box drawn tight against the outermost stroke doesn't clip it.
            fx = iw / float(view_w)
            fy = ih / float(view_h)
            m = 6
            px0 = int(clamp(x0 * fx - m, 0, iw - 1))
            px1 = int(clamp(x1 * fx + m, 1, iw))
            py0 = int(clamp(y0 * fy - m, 0, ih - 1))
            py1 = int(clamp(y1 * fy + m, 1, ih))
            if px1 - px0 < 20 or py1 - py0 < 20:
                self.show_hint_popup("That box is too small — drag a bigger one")
                return
            self.log_to_console(
                f"Kolam Notebook: tracing a {px1-px0}x{py1-py0} area of the "
                f"{iw}x{ih} photo.", "info")
            finish(frame[py0:py1, px0:px1].copy())

        btns = tk.Frame(pad, bg=BG_CARD)
        btns.pack(fill="x", pady=(S(12), 0))
        use_btn = ctk.CTkButton(
            btns, text="Trace This Area →", command=use_selection,
            fg_color=ACCENT_CYAN, hover_color=self._lighten(ACCENT_CYAN, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8), state="disabled")
        use_btn.pack(side="left", expand=True, fill="x", padx=(0, S(6)))
        ctk.CTkButton(
            btns, text="Whole Picture →", command=lambda: finish(frame),
            fg_color="transparent", hover_color=BG_INPUT, border_width=1,
            border_color=GLASS_EDGE, text_color=TEXT_PRIMARY,
            font=("Segoe UI", FS(11), "bold"), height=S(38),
            corner_radius=S(8)).pack(side="left", expand=True, fill="x",
                                     padx=(S(6), 0))

        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(frame))
        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _digitize_notebook_frame(self, frame, opts=None):
        """Run one captured page through the tracer and open the edit step."""
        opts = dict(opts or {})
        opts.setdefault('snap', True)
        opts.setdefault('remove_rules', True)
        opts.setdefault('draw_dots', False)
        opts.setdefault('invert', "auto")
        try:
            result = self._nb_process_page(
                frame, snap=opts['snap'], remove_rules=opts['remove_rules'],
                invert=opts['invert'])
        except ImportError as e:
            self.log_to_console(f"Kolam Notebook: {e}", "err")
            return
        except Exception as e:
            # Never leave the sitting with no window open — one unreadable
            # page shouldn't end a session halfway through a book.
            self.log_to_console(
                f"Kolam Notebook: could not read that page — {e}", "err")
            self.show_hint_popup("Couldn't read that page — try another photo")
            if self._notebook_session is not None:
                self._open_notebook_capture_dialog()
            return

        if not result['paths']:
            self.log_to_console(
                "Kolam Notebook: no kolam lines found on that page. More light "
                "and less shadow across the page usually fixes it.", "err")
            self.show_hint_popup("Nothing traceable on that page — try more light")
            self._open_notebook_capture_dialog()
            return

        self.log_to_console(
            f"Kolam Notebook: traced {len(result['paths'])} stroke(s) and "
            f"{len(result['dots'])} pulli dot(s)"
            + (", light-on-dark photo inverted" if result['inverted'] else "")
            + (f", snapped to a {result['pitch']:.0f}px grid"
               if result['grid'] else ", grid snap not applied")
            + ".", "recv")
        self._open_notebook_review_popup(frame, result, opts)

    # ── page processing (pure OpenCV, no network) ────────────────────────────
    def _nb_process_page(self, frame, snap=True, remove_rules=True,
                         invert="auto"):
        """Photo of a notebook page → (stroke paths, pulli dots) in image
        pixel coordinates.

        A page photo is nothing like the flat AI renders the other importer
        handles: the paper is warm-white, one side sits in shadow, and the
        page is usually ruled. So the threshold has to be adaptive (per-region,
        not one global cut-off) and the ruled lines have to come out before
        anything is traced.

        ``invert`` is "auto", True or False — see _nb_light_on_dark."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            raise ImportError("opencv-python and numpy are required. "
                              "Run: pip install opencv-python numpy")

        if frame is None or getattr(frame, 'size', 0) == 0:
            raise ValueError("empty image")

        gray = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if getattr(frame, 'ndim', 2) == 3 else frame.copy())

        # Work at a known size in *both* directions. Every kernel below is a
        # fixed pixel count tuned for a page about NB_MAX_DIM across, so a
        # small image — a web JPEG of a kolam, say — arrives with those
        # kernels several times too coarse for its strokes, and the repair
        # steps weld neighbouring loops into one blob. Enlarging adds no
        # detail, but it does put the detail there in the kernels' reach.
        h0, w0 = gray.shape[:2]
        sc = NB_MAX_DIM / float(max(1, max(h0, w0)))
        sc = min(sc, NB_MAX_UPSCALE)
        if abs(sc - 1.0) > 0.02:
            gray = cv2.resize(
                gray, (max(1, int(w0 * sc)), max(1, int(h0 * sc))),
                interpolation=cv2.INTER_AREA if sc < 1.0 else cv2.INTER_CUBIC)
        h, w = gray.shape[:2]

        # Rangoli is as often drawn light-on-dark as dark-on-light: chalk or
        # kolam powder on a red-oxide floor, white ink on a dark page. The
        # threshold below looks for ink *darker* than its surroundings, so on
        # one of those the shoulders around each stroke get traced instead of
        # the stroke — every line comes back doubled, and every pulli comes
        # back as a hollow ring the dot finder throws away. Flipping the image
        # first is the whole fix.
        inverted = (self._nb_light_on_dark(gray) if invert == "auto"
                    else bool(invert))
        if inverted:
            gray = cv2.bitwise_not(gray)

        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        # Block size scales with the page so the neighbourhood is always a
        # few strokes wide, whatever resolution the camera gave us.
        block = max(15, (min(h, w) // 20) | 1)
        ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, block, 10)
        # Kill single-pixel paper grain before anything measures blob sizes.
        ink = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

        # The pulli are read off the ink *before* the ruled lines come out, and
        # that ordering is the whole ballgame on ruled paper. Nobody spaces
        # pulli by eye when the page already has lines on it — the dots go
        # straight onto the rules. Take the rules out first and the dots go
        # with them: on a real notebook page that turned 974 candidate blobs
        # into 8.
        cands, _labels = self._nb_dot_candidates(ink, gray)
        design_box = self._nb_design_bbox(ink)

        rules_removed = False
        if remove_rules:
            # A ruled line is the one thing on the page that stays straight for
            # a tenth of the sheet, so a long thin opening finds exactly those
            # and nothing a hand drew. Close along the same axis first: a faint
            # printed rule photographs as a dashed one, and an opening would
            # miss it entirely.
            rules = cv2.bitwise_or(
                self._nb_rule_mask(ink, horizontal=True, beyond=design_box),
                self._nb_rule_mask(ink, horizontal=False, beyond=design_box))
            if cv2.countNonZero(rules):
                rules_removed = True
                ink = cv2.subtract(ink, rules)

        # Confine the dots to where the drawing is. Paper grain, the gutter
        # shadow and the torn edge of the sheet all leave marks, and out in the
        # margins those happily fit a lattice of their own — the design is the
        # one thing that says where the pulli are allowed to be.
        cands = self._nb_keep_within(cands, design_box)
        (dots, _keep_labels, snapped,
         pitch, angle, grid_ok) = self._nb_choose_pulli(cands)

        # Settle the pulli *before* tracing, and lift them out of the ink. They
        # are a layer of their own, so leaving them in would have the robot
        # draw each one twice — once as a dot, once as a little ring around it.
        # Doing it in this order also means the page is traced once, not twice.
        lines = ink
        if dots:
            dot_mask = np.zeros_like(ink)
            for dx, dy, dr in dots:
                cv2.circle(dot_mask, (int(round(dx)), int(round(dy))),
                           int(round(dr)) + 2, 255, -1)
            lines = cv2.subtract(ink, dot_mask)

        # No repair pass before tracing. There used to be one here to mend the
        # design where taking out a rule had cut across it, but now that rule
        # removal only ever touches hairline ink there is nothing to mend — and
        # the repair was quietly welding neighbouring loops of a sikku kolam
        # into one blob, which is most of what made the traces look melted.
        paths = self._nb_trace_strokes(lines)
        paths = self._nb_keep_within(
            paths, design_box, min_keep=1,
            key=lambda pts: (sum(x for x, _ in pts) / len(pts),
                             sum(y for _, y in pts) / len(pts)))

        if grid_ok:
            src = np.array([[d[0], d[1]] for d in dots], dtype=float)
            # The kolam lives on its pulli grid, so anything traced well
            # outside it is page furniture — a caption, a torn edge, the
            # shadow of the facing page.
            paths, in_box = self._nb_crop_to_grid(paths, dots, pitch)
            if not all(in_box):
                sel = [i for i, ok in enumerate(in_box) if ok]
                dots = [dots[i] for i in sel]
                src, snapped = src[sel], snapped[sel]
            if snap and len(src):
                paths = self._nb_warp_paths(paths, src, snapped, pitch)
                dots = [(float(p[0]), float(p[1]), d[2])
                        for p, d in zip(snapped, dots)]
            else:
                grid_ok = False

        paths = [self._nb_smooth_path(p) for p in paths]
        paths = [p for p in paths if len(p) >= 3]

        return {'paths': paths, 'dots': dots, 'size': (w, h),
                'pitch': pitch, 'angle': angle, 'grid': grid_ok,
                'rules_removed': rules_removed, 'inverted': inverted}

    @staticmethod
    def _nb_light_on_dark(gray):
        """True when the drawing is light ink on a dark ground.

        Otsu splits the picture into its two natural tones and the *minority*
        tone is the ink — a drawing never covers more of the surface than the
        surface it sits on. So if the bright tone is the smaller share, the
        ink is the bright one. Judging it by which side is the minority rather
        than by absolute brightness is what keeps a badly underexposed photo
        of a white page from being mistaken for a dark one."""
        import cv2

        _thr, split = cv2.threshold(gray, 0, 255,
                                    cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        bright_share = cv2.countNonZero(split) / float(max(1, gray.size))
        return bright_share < 0.5

    @staticmethod
    def _nb_rule_mask(ink, horizontal=True, iters=48, beyond=None):
        """Find the printed rules on one axis of the page.

        A morphological opening with a long flat kernel is the textbook way to
        do this, but it only matches a line that stays inside a single pixel
        row — and a page photographed 2° off square doesn't. Nobody lays a
        notebook down perfectly straight, so the rules are found as *lines*
        instead: long, nearly-axis-aligned, tilt and all.

        The lines only seed the answer. A rule fades into dashes at the bright
        edge of a page and a Hough segment stops at the first real gap, so each
        seed is then grown along its own axis through connected ink until it
        runs out. Growing through ink rather than jumping gaps blind is what
        keeps the growth from threading one ring's tangent into the next."""
        import cv2
        import numpy as np

        h, w = ink.shape[:2]
        span = w if horizontal else h
        mask = np.zeros_like(ink)

        # Everything here happens on the *thin* ink only. A printed rule is a
        # hairline; a pencil kolam stroke is several times thicker, and so is a
        # pulli. Taking the thick ink out of consideration first is what stops
        # the search threading along the design and deleting it — searching the
        # whole page instead had the rule mask laying claim to half the kolam.
        # It also protects the dots for free.
        thickness = max(5, (min(h, w) // 120) | 1)
        thick = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (thickness, thickness)))
        thin = cv2.subtract(ink, thick)
        if not cv2.countNonZero(thin):
            return mask

        # With the design excluded, a generous gap tolerance is safe — and it
        # is needed, because a faint rule photographs as a dotted trail.
        min_len = max(60, span // 4)
        lines = cv2.HoughLinesP(thin, 1, np.pi / 360.0,
                                threshold=max(30, min_len // 4),
                                minLineLength=min_len,
                                maxLineGap=max(10, span // 20))
        if lines is None:
            return mask
        # Shape is (N, 1, 4) on most builds and (N, 4) on some — flatten either.
        segs = []
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            ang = math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180.0
            aligned = (ang < 12.0 or ang > 168.0) if horizontal \
                else (78.0 < ang < 102.0)
            if not aligned:
                continue
            # A printed rule crosses the whole sheet; it does not begin and end
            # inside the drawing. Requiring a seed to reach past the design is
            # what tells a rule from a long straight part *of* a design — on a
            # symmetric drawn kolam the search had found five parallel lines
            # inside the artwork and taken a stripe out of the middle of it.
            if beyond:
                bx0, by0, bx1, by1 = beyond
                edge = 0.05 * span
                lo, hi = ((min(x1, x2), max(x1, x2)) if horizontal
                          else (min(y1, y2), max(y1, y2)))
                near, far = (bx0, bx1) if horizontal else (by0, by1)
                if lo > near - edge and hi < far + edge:
                    continue
            offset = (y1 + y2) / 2.0 if horizontal else (x1 + x2) / 2.0
            segs.append((offset, (x1, y1, x2, y2)))
        if not segs:
            return mask

        # Ruled paper means *many* parallel lines — a page has a dozen or more.
        # One or two long straight lines are far more likely to be the border
        # of the kolam, the margin rule, or the edge of the facing page, and
        # eating those would quietly delete part of the design. So nothing is
        # removed until this axis looks like ruling rather than drawing.
        rows = []
        for offset, _seg in sorted(segs):
            if rows and abs(offset - rows[-1]) <= 10.0:
                continue
            rows.append(offset)
        if len(rows) < 5:
            return mask

        for _offset, (x1, y1, x2, y2) in segs:
            cv2.line(mask, (x1, y1), (x2, y2), 255, 3)

        if horizontal:
            join = cv2.getStructuringElement(cv2.MORPH_RECT, (81, 1))
            grow = cv2.getStructuringElement(cv2.MORPH_RECT, (41, 1))
        else:
            join = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 81))
            grow = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 41))

        # Close along the axis first so a dashed rule counts as connected. It
        # has to be a wide close: where the page is brightest a printed rule
        # thresholds into a dotted trail with long holes, and growth that
        # stops at the first hole leaves the whole faint end of every rule
        # behind — a tidy row of specks that looks just like pulli. Growing
        # through `thin` rather than through all the ink is what makes a close
        # this wide safe.
        reach = cv2.morphologyEx(thin, cv2.MORPH_CLOSE, join)
        seed = cv2.bitwise_and(mask, reach)
        for _ in range(iters):
            grown = cv2.bitwise_and(cv2.dilate(seed, grow), reach)
            if cv2.countNonZero(cv2.absdiff(grown, seed)) == 0:
                break
            seed = grown
        # Never hand back a thick pixel: the design and the pulli are safe by
        # construction, whatever the line search decided.
        return cv2.bitwise_and(seed, thin)

    def _nb_dot_candidates(self, ink, gray):
        """Every blob on the page that *could* be a pulli, plus the label map.

        Deliberately generous — no size filter beyond the absurd. On ruled
        paper the printed rules are often dotted, which puts hundreds of
        genuine-looking little blobs on the page, and a median-based size
        filter here would take its idea of "normal" from those and throw the
        real pulli out as too big. Choosing between them is _nb_choose_pulli's
        job, where the grid can be used as the evidence.

        Returns (candidates, labels) with candidates as [(x, y, radius, label)].
        """
        import cv2
        import numpy as np

        h, w = ink.shape[:2]
        max_r = max(3.0, 0.030 * min(h, w))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(ink, 8)

        cand = []
        for i in range(1, n):
            bw, bh, area = int(stats[i][2]), int(stats[i][3]), int(stats[i][4])
            if area < 4:
                continue
            if max(bw, bh) > 2 * max_r:
                continue
            if not (0.55 <= bw / float(max(bh, 1)) <= 1.8):
                continue
            if area / float(max(bw * bh, 1)) < 0.45:      # solid, not a curl
                continue
            cand.append((float(cents[i][0]), float(cents[i][1]),
                         (bw + bh) / 4.0, i))

        # The grid search below is O(n^2) in the candidates, so a page whose
        # rules dissolved into thousands of specks gets trimmed to the biggest
        # of them — a pulli is never the smallest mark on the page.
        if len(cand) > NB_MAX_DOT_CANDIDATES:
            cand.sort(key=lambda c: c[2], reverse=True)
            cand = cand[:NB_MAX_DOT_CANDIDATES]

        if len(cand) >= 4:
            return cand, labels

        # Faint or line-touched dots: fall back to gradient circle finding.
        # These carry label -1 — they have no component of their own, so they
        # can't be masked out of the line layer by label.
        min_dist = max(8, min(h, w) // 60)
        try:
            circles = cv2.HoughCircles(
                cv2.medianBlur(gray, 5), cv2.HOUGH_GRADIENT, dp=1,
                minDist=min_dist, param1=110, param2=13,
                minRadius=2, maxRadius=int(max_r))
        except cv2.error:
            circles = None
        if circles is not None:
            for c in np.asarray(circles).reshape(-1, 3):
                cx, cy, r = float(c[0]), float(c[1]), float(c[2])
                if any((cx - fx) ** 2 + (cy - fy) ** 2 < min_dist ** 2
                       for fx, fy, _fr, _l in cand):
                    continue
                cand.append((cx, cy, r, -1))
        return cand, labels

    @staticmethod
    def _nb_design_bbox(ink):
        """Where on the page the drawing actually is, as (x0, y0, x1, y1).

        Taken from the *thick* ink: a drawn kolam is the substantial mark on
        the sheet, while the torn edge of the paper, the shadow at the gutter
        and the printed rules are all hairlines. Anything with a decent share
        of the biggest blob's area joins in, so a kolam drawn as several
        separate loops still gets a footprint covering all of them.

        Returns None when there is nothing substantial to go on."""
        import cv2
        import numpy as np

        h, w = ink.shape[:2]
        t = max(5, (min(h, w) // 120) | 1)
        thick = cv2.morphologyEx(
            ink, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (t, t)))
        n, _labels, stats, _cents = cv2.connectedComponentsWithStats(thick, 8)
        if n <= 1:
            return None
        areas = stats[1:, 4]
        biggest = float(areas.max())
        if biggest <= 0:
            return None
        keep = [i + 1 for i, a in enumerate(areas) if a >= 0.15 * biggest]
        x0 = min(int(stats[i][0]) for i in keep)
        y0 = min(int(stats[i][1]) for i in keep)
        x1 = max(int(stats[i][0]) + int(stats[i][2]) for i in keep)
        y1 = max(int(stats[i][1]) + int(stats[i][3]) for i in keep)
        mx = 0.08 * max(1, x1 - x0)
        my = 0.08 * max(1, y1 - y0)
        return (x0 - mx, y0 - my, x1 + mx, y1 + my)

    @staticmethod
    def _nb_keep_within(items, box, min_keep=4, key=None):
        """Keep the items that fall inside ``box``, unless too few would."""
        if box is None or not items:
            return items
        x0, y0, x1, y1 = box
        if key is None:
            key = lambda it: (it[0], it[1])
        inside = []
        for it in items:
            x, y = key(it)
            if x0 <= x <= x1 and y0 <= y <= y1:
                inside.append(it)
        return inside if len(inside) >= min_keep else items

    def _nb_choose_pulli(self, cands):
        """Decide which blobs are the kolam's pulli, and fit their grid.

        Tries the whole candidate list and then successively larger-only
        subsets of it. A dotted printed rule and a hand-drawn pulli are both
        small round marks, and the only thing that reliably separates them is
        that the pulli are bigger *and* that they alone form a filled, square
        lattice — so both are used, and the subset producing the best grid
        wins.

        Returns (dots, keep_labels, snapped, pitch, angle_deg, grid_ok)."""
        import numpy as np

        if len(cands) < 4:
            return [(x, y, r) for x, y, r, _l in cands], [], None, None, 0.0, False

        radii = np.array([c[2] for c in cands], dtype=float)
        r_med = float(np.median(radii))
        bands, seen = [], set()
        for floor in (0.0, 0.9 * r_med, 1.2 * r_med, 1.5 * r_med, 2.0 * r_med):
            idx = tuple(i for i, r in enumerate(radii) if r >= floor)
            if len(idx) >= 4 and idx not in seen:
                seen.add(idx)
                bands.append(idx)

        best = None
        for idx in bands:
            subset = [cands[i] for i in idx]
            fit = self._nb_fit_grid([(c[0], c[1], c[2]) for c in subset])
            if fit is None:
                continue
            snapped, src, pitch, angle, keep = fit
            n_in = int(sum(1 for k in keep if k))
            if n_in < 4:
                continue
            # Count the dots, but weight them by how big they are relative to
            # everything on the page. Otherwise the small marks always win on
            # numbers alone: a dotted printed rule leaves hundreds of specks
            # against a couple of dozen pulli, and the specks are what get
            # snapped to. A pulli is put there on purpose and is the heavier
            # mark; the cap keeps a stray blot from running away with it.
            chosen_r = float(np.median([c[2] for c, ok in zip(subset, keep) if ok]))
            weight = min(3.0, chosen_r / max(0.5, r_med))
            score = n_in * weight
            if best is None or score > best[0]:
                best = (score, subset, snapped, src, pitch, angle, keep)

        if best is None:
            # No grid anywhere. Keep the dots that look alike so the layer is
            # still useful, but say plainly that nothing was snapped.
            dots = [(x, y, r) for x, y, r, _l in cands
                    if 0.45 * r_med <= r <= 2.2 * r_med]
            labs = [l for x, y, r, l in cands
                    if 0.45 * r_med <= r <= 2.2 * r_med]
            return dots, labs, None, None, 0.0, False

        _score, subset, snapped, _src, pitch, angle, keep = best
        chosen = [c for c, ok in zip(subset, keep) if ok]
        dots = [(c[0], c[1], c[2]) for c in chosen]
        labs = [c[3] for c in chosen]
        return dots, labs, snapped, pitch, angle, True

    # Offsets of the eight neighbours of a pixel, (dy, dx).
    _NB_N8 = ((-1, -1), (-1, 0), (-1, 1), (0, -1),
              (0, 1), (1, -1), (1, 0), (1, 1))

    def _nb_trace_strokes(self, lines_mask):
        """Trace the centreline of every line of ink left on the page.

        Following contours instead — the obvious thing, and what this used to
        do — traces the *edge* of a stroke rather than its middle, so a pencil
        line comes back as a long thin ribbon: the robot draws every line twice,
        once down each side, and at a crossing the two ribbons merge into a blob.
        On a sikku kolam, which is nothing but crossings, that turns a lattice
        of interlaced curves into a heap of melted rings.

        So the ink is thinned to a one-pixel skeleton and walked as a graph.
        The work is all in the crossings: a crossing of two strokes thins to a
        little cluster of branch points, not one clean X, and the two curves
        running through it have to come out still separate and still
        continuous."""
        import cv2
        import numpy as np

        h, w = lines_mask.shape[:2]
        min_len = 0.02 * (w + h)
        if not cv2.countNonZero(lines_mask):
            return []

        skel = self._nb_skeleton(lines_mask)
        if not skel.any():
            return []

        # Stroke width, straight off the distance transform: on the centreline
        # the distance to the nearest background pixel *is* the half-width.
        # Everything below is expressed in it, so the same code handles a fine
        # pen and a thick chalk line.
        on = cv2.distanceTransform(lines_mask, cv2.DIST_L2, 5)[skel > 0]
        thick = float(np.median(on)) * 2.0 if on.size else 3.0
        thick = max(2.0, min(thick, 0.02 * (w + h)))

        paths = self._nb_graph_strokes(skel, thick, min_len)
        paths = self._nb_bridge_gaps(paths, thick)
        out = []
        for pts in paths:
            closed = math.hypot(pts[0][0] - pts[-1][0],
                                pts[0][1] - pts[-1][1]) < 2.0
            approx = cv2.approxPolyDP(
                np.array(pts, np.float32).reshape(-1, 1, 2), 1.2, closed)
            simple = [(float(p[0][0]), float(p[0][1])) for p in approx]
            if len(simple) < 2:
                continue
            if closed and simple[0] != simple[-1]:
                simple.append(simple[0])
            out.append((self._nb_path_len(pts), simple))
        out.sort(key=lambda t: t[0], reverse=True)
        return [pts for _length, pts in out[:NB_MAX_STROKES]]

    @staticmethod
    def _nb_path_len(pts):
        return sum(math.hypot(pts[i + 1][0] - pts[i][0],
                              pts[i + 1][1] - pts[i][1])
                   for i in range(len(pts) - 1))

    @staticmethod
    def _nb_thin_tables():
        """Build the three neighbour-pattern tables, once per run.

        A pixel's eight neighbours make an 8-bit code, read clockwise from
        north. Every test below is a pure function of that code, so each one
        becomes a 256-entry table and a whole page is tested with one
        convolution and one lookup."""
        import numpy as np

        if _NB_THIN_LUT:
            return (_NB_THIN_LUT['del1'], _NB_THIN_LUT['del2'],
                    _NB_THIN_LUT['xing'])

        del1 = np.zeros(256, np.uint8)
        del2 = np.zeros(256, np.uint8)
        xing = np.zeros(256, np.uint8)
        for code in range(256):
            p = [(code >> i) & 1 for i in range(8)]   # p[0]=N .. clockwise
            ring = p + [p[0]]
            # Crossing number: how many separate arms leave this pixel. 1 is a
            # loose end, 2 is a point on a curve, 3+ is a real branch.
            trans = sum(1 for i in range(8)
                        if ring[i] == 0 and ring[i + 1] == 1)
            xing[code] = trans
            # Zhang-Suen: a pixel may go only if it has 2..6 neighbours, sits on
            # a single arm, and is not needed to keep its curve connected.
            if not (2 <= sum(p) <= 6 and trans == 1):
                continue
            n, ne, e, se, s, sw, wst, nw = p
            if n * e * s == 0 and e * s * wst == 0:
                del1[code] = 1
            if n * e * wst == 0 and n * s * wst == 0:
                del2[code] = 1
        _NB_THIN_LUT.update(del1=del1, del2=del2, xing=xing)
        return del1, del2, xing

    @staticmethod
    def _nb_neighbour_code(skel):
        """Pack each pixel's eight neighbours into one byte, clockwise from
        north. Eight shifted views of the image, no per-pixel Python."""
        import numpy as np

        h, w = skel.shape[:2]
        pad = np.zeros((h + 2, w + 2), np.uint8)
        pad[1:-1, 1:-1] = skel
        code = pad[0:h, 1:w + 1] << 0            # N
        code |= pad[0:h, 2:w + 2] << 1           # NE
        code |= pad[1:h + 1, 2:w + 2] << 2       # E
        code |= pad[2:h + 2, 2:w + 2] << 3       # SE
        code |= pad[2:h + 2, 1:w + 1] << 4       # S
        code |= pad[2:h + 2, 0:w] << 5           # SW
        code |= pad[1:h + 1, 0:w] << 6           # W
        code |= pad[0:h, 0:w] << 7               # NW
        return code

    @classmethod
    def _nb_skeleton(cls, mask):
        """Thin a binary mask to a one-pixel-wide skeleton (Zhang-Suen).

        OpenCV only ships thinning in its contrib package, which is not a
        dependency here, so it is done directly. Table-driven it costs a few
        array passes per sweep rather than a Python loop over a million pixels,
        and only the part of the page that has ink on it is swept at all."""
        import numpy as np

        del1, del2, _xing = cls._nb_thin_tables()
        full = (mask > 0).astype(np.uint8)
        ys, xs = np.nonzero(full)
        if not len(ys):
            return full
        y0, y1 = max(0, int(ys.min()) - 1), min(full.shape[0], int(ys.max()) + 2)
        x0, x1 = max(0, int(xs.min()) - 1), min(full.shape[1], int(xs.max()) + 2)
        img = full[y0:y1, x0:x1].copy()

        for _sweep in range(60):          # each sweep peels one layer of pixels
            changed = False
            for table in (del1, del2):
                gone = table[cls._nb_neighbour_code(img)] & img
                if gone.any():
                    img -= gone
                    changed = True
            if not changed:
                break

        out = np.zeros_like(full)
        out[y0:y1, x0:x1] = img
        return out

    @classmethod
    def _nb_crossing_number(cls, skel):
        """Arms leaving each skeleton pixel: 1 = loose end, 2 = curve, 3+ = branch.

        Counting neighbours instead gets this badly wrong: a skeleton running
        diagonally has three neighbours at every single step, so a neighbour
        count declares the whole curve one long junction — which, tried first,
        welded the design into a handful of straight lines across the page."""
        import numpy as np

        _d1, _d2, xing = cls._nb_thin_tables()
        return xing[cls._nb_neighbour_code(skel)].astype(np.int32) * skel

    @classmethod
    def _nb_walk_pixels(cls, seed, remaining):
        """Follow the skeleton out of `seed`, consuming pixels as it goes.

        Where several pixels are available it keeps going straight, which is
        what stops a walk from turning up a side branch at a crossing."""
        chain = [seed]
        remaining.discard(seed)
        prev, cur = None, seed
        while True:
            nbrs = [(cur[0] + dy, cur[1] + dx) for dy, dx in cls._NB_N8
                    if (cur[0] + dy, cur[1] + dx) in remaining]
            if not nbrs:
                return chain
            if prev is None:
                step = min(nbrs, key=lambda q: abs(q[0] - cur[0]) +
                           abs(q[1] - cur[1]))
            else:
                dy0, dx0 = cur[0] - prev[0], cur[1] - prev[1]
                step = max(nbrs, key=lambda q: (q[0] - cur[0]) * dy0 +
                           (q[1] - cur[1]) * dx0)
            chain.append(step)
            remaining.discard(step)
            prev, cur = cur, step

    @classmethod
    def _nb_pixel_chains(cls, pixels):
        """Split one connected run of skeleton into ordered pixel chains.

        Every pixel has to end up in some chain. A single walk from one end
        does not manage that — it abandons whatever it did not choose at a
        fork, and on a real page that silently dropped entire loops of the
        design — so walks are re-seeded until the run is used up."""
        remaining = set(pixels)
        degree = {}
        for p in remaining:
            degree[p] = sum(1 for dy, dx in cls._NB_N8
                            if (p[0] + dy, p[1] + dx) in remaining)
        ends = [p for p in remaining if degree[p] <= 1]

        chains, at = [], 0
        while remaining:
            seed = None
            while at < len(ends):
                cand = ends[at]
                at += 1
                if cand in remaining:
                    seed = cand
                    break
            if seed is None:            # no loose end left: start mid-curve
                seed = next(iter(remaining))
            forward = cls._nb_walk_pixels(seed, remaining)
            # Seeding mid-curve only walks one way; go back for the other half.
            remaining.add(seed)
            backward = cls._nb_walk_pixels(seed, remaining)
            chains.append(backward[::-1] + forward[1:]
                          if len(backward) > 1 else forward)
        return chains

    def _nb_graph_strokes(self, skel, thick, min_len):
        """Walk a skeleton into strokes, keeping crossings continuous."""
        import cv2
        import numpy as np

        h, w = skel.shape[:2]
        branch = ((self._nb_crossing_number(skel) >= 3) & (skel > 0))

        # Two strokes crossing do not thin to one tidy X — they thin to a pair
        # of Y's a stroke-width apart, joined by a short bridge. Taken as two
        # separate junctions the curves get spliced to the bridge instead of to
        # each other, so everything within a stroke width is one crossing.
        reach = max(1, int(round(thick * 0.9)))
        blob = cv2.dilate(branch.astype(np.uint8), cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1)))
        blob = (blob > 0) & (skel > 0)
        n_nodes, node_at, _stats, centres = cv2.connectedComponentsWithStats(
            blob.astype(np.uint8), 8)

        # What is left once the crossings are lifted out are the arcs between
        # them — each one a simple curve that can just be walked end to end.
        spans = ((skel > 0) & ~blob).astype(np.uint8)
        _n, span_at = cv2.connectedComponents(spans, 8)
        ys, xs = np.nonzero(spans)
        # Group the pixels by which arc they belong to. Sorting the labels does
        # this in one pass; walking the page pixel by pixel in Python instead
        # was most of the cost on a badly-lit photo, where the threshold leaves
        # tens of thousands of specks and each one became a dictionary entry.
        order = np.argsort(span_at[ys, xs], kind='stable')
        ys, xs = ys[order], xs[order]
        cuts = np.flatnonzero(np.diff(span_at[ys, xs])) + 1
        runs = [np.stack((ys[a:b], xs[a:b]), axis=1) for a, b in
                zip(np.r_[0, cuts], np.r_[cuts, len(ys)])]

        # Which crossing each arc end belongs to. Spreading the crossing labels
        # outward once, and then simply reading the answer off, is the whole
        # difference between this taking 60 ms and taking a second: searching
        # outward from each end in turn was two thirds of the tracing time.
        spread = max(1, reach + 2)
        near = cv2.dilate(node_at.astype(np.uint16),
                          cv2.getStructuringElement(
                              cv2.MORPH_ELLIPSE, (2 * spread + 1,
                                                  2 * spread + 1)))

        arcs = []
        for group in runs:
            # Two pixels cannot make an arc worth drawing: on its own it is a
            # speck, and at a crossing the crossing's own centre already covers
            # it. Dropping them here is what keeps a grainy page quick.
            if len(group) < 3:
                continue
            for chain in self._nb_pixel_chains(map(tuple, group.tolist())):
                arcs.append([[(float(x), float(y)) for (y, x) in chain],
                             int(near[chain[0]]), int(near[chain[-1]])])

        # A stub hanging off a crossing with its far end in mid-air is the
        # flared end of a pencil stroke, not a line anybody drew.
        stub = max(3.0, thick * 1.6)
        arcs = [a for a in arcs
                if not ((a[1] == 0) ^ (a[2] == 0))
                or self._nb_path_len(a[0]) > stub]

        centroids = {i: (float(centres[i][0]), float(centres[i][1]))
                     for i in range(1, n_nodes)}
        return self._nb_stitch(arcs, centroids, min_len, stub)

    @classmethod
    def _nb_bridge_gaps(cls, strokes, thick):
        """Rejoin curves that a break in the pencil line split in two.

        Nobody's hand leaves an unbroken line: the lead skips over the grain of
        the paper, a stroke fades where the hand lifted, and the threshold drops
        whatever was too faint. Left alone each break costs the robot a pen lift
        and leaves a visible nick in the powder.

        Only ends that are both close and *pointing at each other* are joined,
        which is what keeps two curves that merely pass near each other — the
        normal state of affairs in a kolam — from being spliced together."""
        reach = max(6.0, thick * 3.5)
        open_ends, closed = [], []
        for pts in strokes:
            if (len(pts) > 2 and math.hypot(pts[0][0] - pts[-1][0],
                                            pts[0][1] - pts[-1][1]) < 2.0):
                closed.append(list(pts))
            else:
                open_ends.append(list(pts))
        if len(open_ends) < 2:
            return closed + open_ends

        def tip(i, end):
            return open_ends[i][0] if end == 0 else open_ends[i][-1]

        def outward(i, end):
            dx, dy = cls._nb_direction(open_ends[i], end == 0)
            return (-dx, -dy)

        options = []
        for i in range(len(open_ends)):
            for j in range(i + 1, len(open_ends)):
                for ei in (0, 1):
                    for ej in (0, 1):
                        (x1, y1), (x2, y2) = tip(i, ei), tip(j, ej)
                        gap = math.hypot(x2 - x1, y2 - y1)
                        if gap > reach:
                            continue
                        # Ends almost touching are the same line whatever the
                        # tangents say; a longer jump has to be argued for.
                        need = -0.2 if gap < thick * 0.8 else 0.35
                        u = ((x2 - x1) / max(gap, 1e-6),
                             (y2 - y1) / max(gap, 1e-6))
                        d1, d2 = outward(i, ei), outward(j, ej)
                        if (d1[0] * u[0] + d1[1] * u[1] < need or
                                d2[0] * -u[0] + d2[1] * -u[1] < need):
                            continue
                        options.append((gap, i, ei, j, ej))
        options.sort(key=lambda t: t[0])

        root = list(range(len(open_ends)))

        def find(a):
            while root[a] != a:
                root[a] = root[root[a]]
                a = root[a]
            return a

        joins = {}
        for _gap, i, ei, j, ej in options:
            if (i, ei) in joins or (j, ej) in joins:
                continue
            if find(i) == find(j):      # already the same curve
                continue
            joins[(i, ei)] = (j, ej)
            joins[(j, ej)] = (i, ei)
            root[find(i)] = find(j)

        merged, used = [], set()
        for start in range(len(open_ends)):
            if start in used:
                continue
            if (start, 0) in joins and (start, 1) in joins:
                continue            # middle of a run; picked up from an end
            chain, i = [], start
            end = 1 if (start, 0) in joins else 0
            while i is not None and i not in used:
                used.add(i)
                pts = open_ends[i] if end == 0 else open_ends[i][::-1]
                chain.extend(pts)
                nxt = joins.get((i, 1 - end))
                i, end = (nxt[0], nxt[1]) if nxt else (None, 0)
            merged.append(chain)
        for i in range(len(open_ends)):
            if i not in used:
                merged.append(open_ends[i])
        return closed + merged

    @classmethod
    def _nb_direction(cls, pts, at_start, span=12.0):
        """Unit vector pointing away from one end of a chain, over `span` px.

        Measured over a short run rather than between adjacent pixels, which on
        a skeleton only ever point along eight directions."""
        seq = pts if at_start else pts[::-1]
        if len(seq) < 2:
            return (0.0, 0.0)
        x0, y0 = seq[0]
        walked, i = 0.0, 1
        for i in range(1, len(seq)):
            walked += math.hypot(seq[i][0] - seq[i - 1][0],
                                 seq[i][1] - seq[i - 1][1])
            if walked >= span:
                break
        dx, dy = seq[i][0] - x0, seq[i][1] - y0
        norm = math.hypot(dx, dy)
        return (dx / norm, dy / norm) if norm > 1e-6 else (0.0, 0.0)

    @classmethod
    def _nb_stitch(cls, arcs, centroids, min_len, floor):
        """Join arcs back into strokes across each crossing.

        This is the step that decides what a kolam *is*. Four arcs meet at a
        crossing and there are three ways to pair them up; only one of them is
        what the hand drew, and it is the one where each pair carries straight
        on through. Pairing by direction is what keeps two curves that cross
        from being read as four petals that touch."""
        ends_at = {}
        node_of = {}
        for i, (pts, a, b) in enumerate(arcs):
            for end, node in ((0, a), (1, b)):
                if node:
                    ends_at.setdefault(node, []).append((i, end))
                    node_of[(i, end)] = node

        partner = {}
        for _node, halves in ends_at.items():
            if len(halves) < 2:
                continue
            facing = {half: cls._nb_direction(arcs[half[0]][0], half[1] == 0)
                      for half in halves}
            options = []
            for a_i in range(len(halves)):
                for b_i in range(a_i + 1, len(halves)):
                    one, two = halves[a_i], halves[b_i]
                    if one[0] == two[0]:
                        continue
                    d1, d2 = facing[one], facing[two]
                    options.append((d1[0] * d2[0] + d1[1] * d2[1], one, two))
            # Most nearly opposite first: those two arms are one curve.
            options.sort(key=lambda t: t[0])
            taken = set()
            for score, one, two in options:
                if score > -0.20:       # too sharp a turn to be one stroke
                    break
                if one in taken or two in taken:
                    continue
                partner[one] = two
                partner[two] = one
                taken.update((one, two))

        strokes, used = [], set()

        def follow(start):
            """Walk from one arc through its pairings, gathering the points.

            Every arc stops a stroke-width short of each crossing it runs into,
            because the crossing was lifted out to be worked on separately. So
            the crossing's own centre is put back at both ends of the walk —
            not only where it carries straight on. Leaving it out where a walk
            *finishes* at a crossing left a hole the width of the crossing in
            the middle of the design, which is every break that survived once
            the tracing itself was right."""
            chain, at, anchored, first = [], start, False, True
            traced = 0.0
            while at is not None and at[0] not in used:
                i, end = at
                used.add(i)
                anchored = anchored or bool(arcs[i][1] or arcs[i][2])
                if first:
                    entry = centroids.get(node_of.get((i, end)))
                    if entry is not None:
                        chain.append(entry)
                    first = False
                chain.extend(arcs[i][0] if end == 0 else arcs[i][0][::-1])
                traced += cls._nb_path_len(arcs[i][0])
                leaving = (i, 1 - end)
                middle = centroids.get(node_of.get(leaving))
                if middle is not None:
                    chain.append(middle)
                at = partner.get(leaving)
            return chain, anchored, traced

        def worth_keeping(chain, anchored, traced):
            """The length a stroke has to reach to be drawn.

            A stroke running between two crossings is part of the design
            however short it is — it is what holds the two halves of a curve
            together, and dropping it leaves a visible nick in the powder. Only
            a mark floating on its own has to earn its place on length, which
            is what keeps grain and smudges out.

            Judged on the ink it actually traced, not on the finished polyline:
            the crossing centres tacked on at either end are long enough to
            carry a stub over the bar on their own, and every crossing on the
            page then came back as its own little dash."""
            if len(chain) < 2:
                return False
            return traced >= (floor if anchored else min_len)

        # Open curves first, from their loose ends, so each comes out whole.
        for i, (pts, a, b) in enumerate(arcs):
            if i in used:
                continue
            for end in (0, 1):
                node = a if end == 0 else b
                if node == 0 or (i, end) not in partner:
                    chain, anchored, traced = follow((i, end))
                    if worth_keeping(chain, anchored, traced):
                        strokes.append(chain)
                    break
        # Whatever has no loose end at all is a closed loop.
        for i in range(len(arcs)):
            if i in used:
                continue
            chain, anchored, traced = follow((i, 0))
            if worth_keeping(chain, anchored, traced):
                if math.hypot(chain[0][0] - chain[-1][0],
                              chain[0][1] - chain[-1][1]) < 4.0:
                    chain.append(chain[0])
                strokes.append(chain)
        return strokes

    def _nb_fit_grid(self, dots):
        """Find the pulli grid among everything the page offered up.

        Which blobs are pulli and what the lattice is are the same question.
        A speck left behind by a ruled line is indistinguishable from a dot on
        its own, and the specks can easily outnumber the real pulli — but they
        only ever line up in *rows*, never in rows and columns at once. So
        rather than trying to pick the dots first, this tries a range of grid
        spacings over every blob on the page and keeps the spacing that puts
        the most dots on a genuinely two-dimensional lattice.

        Returns (snapped_xy, original_xy, pitch, angle_deg, keep_flags) for
        the dots that made the grid, or None when nothing on the page did."""
        import numpy as np

        if len(dots) < 4:
            return None
        pts = np.array([[d[0], d[1]] for d in dots], dtype=float)

        dist = np.hypot(pts[:, None, 0] - pts[None, :, 0],
                        pts[:, None, 1] - pts[None, :, 1])
        np.fill_diagonal(dist, np.inf)
        nn = dist.min(axis=1)

        # Spacings worth trying: the page's own neighbour distances, sampled
        # across their whole spread. When the specks outnumber the pulli the
        # median is *their* spacing, so the upper quantiles are what carry the
        # real one — and vice versa on a clean page.
        cands = []
        for q in (0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0):
            p = float(np.quantile(nn, q))
            if p > 1.0 and all(abs(p - c) > 0.08 * p for c in cands):
                cands.append(p)
        if not cands:
            return None

        best = None
        for pitch0 in cands:
            fit = self._nb_lattice(pts, pitch0)
            if fit is None:
                continue
            if best is None or fit[3] > best[3]:
                best = fit
        if best is None or best[3] < 4:
            return None

        # Refit on the dots that made the grid, at the spacing the grid itself
        # reported. The first pass had to survive the specks; this one doesn't,
        # so the pitch and angle it returns are the ones worth snapping to.
        if best[3] < len(pts):
            refit = self._nb_lattice(pts[best[4]], best[1])
            if refit is not None and refit[3] >= best[3] * 0.9:
                inner = np.zeros(len(pts), dtype=bool)
                inner[np.nonzero(best[4])[0][refit[4]]] = True
                snapped_all = best[0].copy()
                snapped_all[best[4]] = refit[0]
                best = (snapped_all, refit[1], refit[2], int(inner.sum()), inner)

        snapped, pitch, angle, _score, inliers = best
        keep = [bool(v) for v in inliers]
        return snapped[inliers], pts[inliers], pitch, angle, keep

    @staticmethod
    def _nb_lattice(pts, pitch):
        """Fit a regular lattice of the given spacing to a set of points.

        Returns (snapped_xy, pitch, angle_deg, score, inlier_mask). A point is
        an inlier only when it lands on the lattice *and* shares both its row
        and its column with another point — the test that tells a grid of
        pulli apart from a row of specks along a ruled line."""
        import numpy as np

        if len(pts) < 4 or not np.isfinite(pitch) or pitch <= 1.0:
            return None
        dist = np.hypot(pts[:, None, 0] - pts[None, :, 0],
                        pts[:, None, 1] - pts[None, :, 1])
        np.fill_diagonal(dist, np.inf)

        # Grid angle: neighbour directions are all 90° apart, so they only
        # average sensibly on the 4θ circle — then divide back. Every pair
        # about one cell apart gets a vote, not just each dot's nearest
        # neighbour; a 5×5 grid offers forty-odd such pairs and averaging all
        # of them is what keeps a couple of shaky dots from tilting the page.
        ii, jj = np.nonzero(np.triu(np.abs(dist - pitch) < 0.35 * pitch))
        if len(ii) >= 3:
            vec = pts[jj] - pts[ii]
        else:
            nn_idx = dist.argmin(axis=1)
            vec = pts[nn_idx] - pts
        ang = np.arctan2(vec[:, 1], vec[:, 0])
        theta = float(np.arctan2(np.sin(4 * ang).mean(),
                                 np.cos(4 * ang).mean()) / 4.0)
        # A page laid on a table is never more than a few degrees off. A wild
        # angle means the dots aren't a grid, so don't rotate the design.
        if abs(math.degrees(theta)) > 20.0:
            theta = 0.0

        ctr = pts.mean(axis=0)
        rel = pts - ctr
        c, s = math.cos(-theta), math.sin(-theta)
        rot = np.column_stack([rel[:, 0] * c - rel[:, 1] * s,
                               rel[:, 0] * s + rel[:, 1] * c])

        # Rough angle is only needed to sort the dots into rows and columns.
        # Give every dot the integer (column, row) it belongs to, and note how
        # much company it keeps in each.
        cell = np.zeros((len(pts), 2))
        company = np.ones((len(pts), 2), dtype=int)
        for ax in (0, 1):
            v = rot[:, ax]
            centres, members = [], []
            for i in np.argsort(v):
                if centres and abs(v[i] - centres[-1]) <= 0.45 * pitch:
                    members[-1].append(int(i))
                    centres[-1] = float(np.mean(v[members[-1]]))
                else:
                    centres.append(float(v[i]))
                    members.append([int(i)])
            base = centres[0]
            for ci, idxs in enumerate(members):
                k = round((centres[ci] - base) / pitch)
                for i in idxs:
                    cell[i, ax] = k
                    company[i, ax] = len(idxs)

        kx, ky = cell[:, 0], cell[:, 1]
        # Two distinct columns and two distinct rows, or this isn't a grid —
        # which is exactly how a row of specks along a ruled line gets thrown
        # out, however neatly it happens to line up.
        if len(set(kx.tolist())) < 2 or len(set(ky.tolist())) < 2:
            return None

        # One least-squares fit of the whole lattice — origin, spacing and
        # angle together — to every dot at once:
        #     x = a·kx − b·ky + tx      y = b·kx + a·ky + ty
        # Solving for four numbers over dozens of dots averages out the hand
        # wobble that no per-dot or per-pair estimate can see past.
        n = len(pts)
        A = np.zeros((2 * n, 4))
        rhs = np.empty(2 * n)
        A[0::2, 0], A[0::2, 1], A[0::2, 2] = kx, -ky, 1.0
        A[1::2, 0], A[1::2, 1], A[1::2, 3] = ky, kx, 1.0
        rhs[0::2], rhs[1::2] = pts[:, 0], pts[:, 1]
        try:
            sol = np.linalg.lstsq(A, rhs, rcond=None)[0]
        except np.linalg.LinAlgError:
            return None
        a, b, tx, ty = (float(v) for v in sol)
        fitted_pitch = math.hypot(a, b)
        if not (0.4 * pitch <= fitted_pitch <= 2.5 * pitch):
            return None

        out = np.column_stack([a * kx - b * ky + tx, b * kx + a * ky + ty])
        angle = ((math.degrees(math.atan2(b, a)) + 45.0) % 90.0) - 45.0

        moved = np.hypot(out[:, 0] - pts[:, 0], out[:, 1] - pts[:, 1])
        inliers = ((moved <= 0.25 * fitted_pitch) &
                   (company[:, 0] >= 2) & (company[:, 1] >= 2))

        # A kolam's pulli fill their own block of cells. The printed border on
        # a decorative rangoli picture is a tidy row-and-column lattice too —
        # but a hollow one, a ring of dots around an empty middle, and left to
        # itself it wins the search and drags the whole design's grid out to
        # the edges of the picture. So a lattice has to be reasonably filled
        # in to count as pulli rather than as a frame.
        n_in = int(inliers.sum())
        if n_in >= 4:
            cx, cy = cell[inliers, 0], cell[inliers, 1]
            block = (cx.max() - cx.min() + 1) * (cy.max() - cy.min() + 1)
            if block > 0 and n_in / float(block) < 0.40:
                return out, fitted_pitch, angle, 0, inliers

        return out, fitted_pitch, angle, n_in, inliers

    @staticmethod
    def _nb_crop_to_grid(paths, dots, pitch):
        """Drop strokes that lie well outside the pulli grid.

        Returns (kept_paths, dot_flags), where dot_flags says which of the
        dots fell inside the same box — the caller drops the rest."""
        # Six dots before this is trusted: four specks can fluke a lattice,
        # and cropping to a fluke would throw away the real drawing.
        keep_all = [True] * len(dots)
        if len(dots) < 6 or not paths:
            return paths, keep_all
        import numpy as np

        # Percentiles, not the outright extremes: the odd speck still creeps
        # onto the lattice, and one of those at the far edge of the page would
        # stretch the box back over all the rubbish it came from. The margin
        # is wide enough that clipping the real grid this way costs nothing.
        xs = np.array([d[0] for d in dots], dtype=float)
        ys = np.array([d[1] for d in dots], dtype=float)
        margin = 1.5 * pitch
        x0, x1 = np.percentile(xs, 5) - margin, np.percentile(xs, 95) + margin
        y0, y1 = np.percentile(ys, 5) - margin, np.percentile(ys, 95) + margin
        kept = []
        for pts in paths:
            inside = sum(1 for x, y in pts if x0 <= x <= x1 and y0 <= y <= y1)
            if inside >= 0.6 * len(pts):
                kept.append(pts)
        # Throwing away most of the page means the grid was wrong, not the art.
        if len(kept) < 0.4 * len(paths):
            return paths, keep_all
        flags = [bool(x0 <= d[0] <= x1 and y0 <= d[1] <= y1) for d in dots]
        if sum(flags) < 4:
            flags = keep_all
        return (kept or paths), flags

    def _nb_warp_paths(self, paths, src, dst, pitch):
        """Carry the traced curves along with the dots they were drawn around.

        Each point moves by the distance-weighted average of how far the
        nearest few pulli moved, so a line that hugged a dot still hugs it
        after the dot is straightened."""
        import numpy as np

        if len(src) == 0 or not paths:
            return paths
        disp = dst - src
        soft = (0.35 * pitch) ** 2
        k = min(4, len(src))
        out = []
        for pts in paths:
            P = np.array(pts, dtype=float)
            d2 = ((P[:, None, 0] - src[None, :, 0]) ** 2 +
                  (P[:, None, 1] - src[None, :, 1]) ** 2)
            if len(src) > k:
                idx = np.argpartition(d2, k - 1, axis=1)[:, :k]
            else:
                idx = np.tile(np.arange(len(src)), (len(P), 1))
            wgt = 1.0 / (np.take_along_axis(d2, idx, axis=1) + soft)
            wgt /= wgt.sum(axis=1, keepdims=True)
            dx = (disp[idx, 0] * wgt).sum(axis=1)
            dy = (disp[idx, 1] * wgt).sum(axis=1)
            out.append([(float(px + ddx), float(py + ddy))
                        for (px, py), ddx, ddy in zip(pts, dx, dy)])
        return out

    @staticmethod
    def _nb_smooth_path(pts, passes=2):
        """Take the pencil wobble out of a stroke without losing its shape."""
        if len(pts) < 5:
            return pts
        closed = (abs(pts[0][0] - pts[-1][0]) < 1e-6 and
                  abs(pts[0][1] - pts[-1][1]) < 1e-6)
        work = pts[:-1] if closed else list(pts)
        n = len(work)
        if n < 3:
            return pts
        for _ in range(passes):
            new = []
            for i in range(n):
                if not closed and i in (0, n - 1):
                    new.append(work[i])
                    continue
                a, b, c = work[(i - 1) % n], work[i], work[(i + 1) % n]
                new.append(((a[0] + 2 * b[0] + c[0]) / 4.0,
                            (a[1] + 2 * b[1] + c[1]) / 4.0))
            work = new
        return work + [work[0]] if closed else work

    # ── review / edit step (mirrors the DXF preview) ─────────────────────────
    def _open_notebook_review_popup(self, frame, result, opts):
        session = self._notebook_session or {"book": "Kolam Notebook", "page": 1}
        self._close_notebook_review_popup()
        self.root.update_idletasks()

        W, H = S(660), S(760)
        # Everything below the preview is anchored to the bottom edge and the
        # preview takes what is left, so the buttons can never be pushed off
        # the popup — which is exactly what used to happen on a screen whose
        # UI scale differed from the one the numbers were picked on.
        FOOT_Y  = H - S(52)          # action row
        TOG_Y   = FOOT_Y - S(44)     # option toggles
        STAT_Y  = TOG_Y - S(22)      # status line
        PREV_Y  = S(76)
        CW = max(S(200), min(S(500), STAT_Y - PREV_Y - S(10), W - S(52)))
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = max(S(6), self.root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._notebook_review_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(24),
                                fill=BG_CARD, outline=ACCENT_AMBER, width=2)
        glass.create_text(S(28), S(30),
                          text=f"\U0001f4d6 Page {session['page']} — {session['book']}",
                          anchor="w", fill=TEXT_PRIMARY,
                          font=("Segoe UI", FS(14), "bold"))
        glass.create_text(S(28), S(54),
                          text="Edit → click a stroke → Delete or Make multi-colour.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))

        prev_x = (W - CW) // 2
        prev_y = PREV_Y
        preview = tk.Canvas(popup, width=CW, height=CW, bg=CANVAS_BG,
                            highlightthickness=0)
        preview.place(x=prev_x, y=prev_y)

        status_lbl = tk.Label(popup, text="", bg=BG_CARD, fg=TEXT_DIM,
                              font=("Segoe UI", FS(9), "bold"), anchor="w")
        status_lbl.place(x=S(26), y=STAT_Y, width=W - S(52))

        state = {
            'frame': frame,
            'opts': dict(opts),
            'result': result,
            'remaining': list(result['paths']),
            'dots': list(result['dots']),
            'path_colours': {},
            'edit': False,
            'items': [],
            'action_frame': None,
            'selected_pts': None,
            'draw_dots': bool(opts.get('draw_dots')),
            'show_dots': True,
        }

        all_x = [x for pts in state['remaining'] for x, _ in pts] + \
                [d[0] for d in state['dots']]
        all_y = [y for pts in state['remaining'] for _, y in pts] + \
                [d[1] for d in state['dots']]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        pad = S(20)
        pscale = min((CW - 2*pad) / max(max_x - min_x, 1e-9),
                     (CW - 2*pad) / max(max_y - min_y, 1e-9))

        def to_preview(x, y):
            # Image space and canvas space are both y-down, so no flip here.
            return pad + (x - min_x) * pscale, pad + (y - min_y) * pscale

        def dismiss_action_menu():
            fr = state.get('action_frame')
            if fr is not None:
                try: fr.destroy()
                except Exception: pass
                state['action_frame'] = None
            state['selected_pts'] = None

        def stroke_colour(pts):
            name = state['path_colours'].get(id(pts))
            return COLOUR_PALETTE.get(name, ACCENT_PINK) if name else ACCENT_PINK

        def redraw_preview():
            preview.delete("stroke")
            preview.delete("dot")
            state['items'] = []
            if state['show_dots']:
                r = max(2, S(3))
                for dx, dy, _dr in state['dots']:
                    px, py = to_preview(dx, dy)
                    preview.create_oval(px - r, py - r, px + r, py + r,
                                        fill=ACCENT_CYAN, outline="", tags="dot")
            for pts in state['remaining']:
                flat = [c for x, y in pts for c in to_preview(x, y)]
                if len(flat) < 4:
                    continue
                lw = 3 if id(pts) in state['path_colours'] else 2
                state['items'].append(
                    (preview.create_line(flat, fill=stroke_colour(pts), width=lw,
                                         smooth=True, tags="stroke"), pts))
            bits = [f"{len(state['remaining'])} stroke(s)",
                    f"{len(state['dots'])} pulli"]
            bits.append("snapped to grid" if state['result']['grid']
                        else "no grid found")
            if state['result']['inverted']:
                bits.append("light-on-dark, inverted")
            if state['result']['rules_removed']:
                bits.append("ruled lines removed")
            n_col = len(state['path_colours'])
            if n_col:
                bits.append(f"{n_col} coloured")
            if state['edit']:
                bits.append("click a stroke to delete it")
            status_lbl.config(text="  ·  ".join(bits))

        def delete_selected_stroke():
            pts = state.get('selected_pts')
            dismiss_action_menu()
            if pts is None:
                return
            try:
                state['remaining'].remove(pts)
            except ValueError:
                return
            state['path_colours'].pop(id(pts), None)
            redraw_preview()

        def apply_colour(colour_name):
            pts = state.get('selected_pts')
            dismiss_action_menu()
            if pts is None:
                return
            state['path_colours'][id(pts)] = colour_name
            redraw_preview()

        def show_colour_picker(anchor_x, anchor_y):
            kept_pts = state.get('selected_pts')
            fr_old = state.get('action_frame')
            if fr_old is not None:
                try: fr_old.destroy()
                except Exception: pass
                state['action_frame'] = None
            state['selected_pts'] = kept_pts

            fr = tk.Frame(popup, bg=BG_PANEL, highlightbackground=ACCENT_PURP,
                          highlightthickness=2, bd=0, padx=S(6), pady=S(6))
            state['action_frame'] = fr
            tk.Label(fr, text="Pick a colour", bg=BG_PANEL, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(9), "bold")).pack(anchor="w",
                                                            pady=(S(0), S(4)))
            row = tk.Frame(fr, bg=BG_PANEL)
            row.pack()
            for name, hex_col in COLOUR_PALETTE.items():
                sw = tk.Canvas(row, width=S(22), height=S(22), bg=BG_PANEL,
                               highlightthickness=1,
                               highlightbackground="#ffffff")
                sw.create_rectangle(2, 2, 20, 20, fill=hex_col, outline=hex_col)
                sw.pack(side="left", padx=S(2))
                sw.configure(cursor="hand2")
                sw.bind("<Button-1>", lambda _e, n=name: apply_colour(n))
            tk.Button(fr, text="Cancel", command=dismiss_action_menu,
                      bg=BG_INPUT, fg=TEXT_DIM, relief="flat",
                      font=("Segoe UI", FS(8)), cursor="hand2",
                      activebackground=BG_CARD, activeforeground=TEXT_PRIMARY
                      ).pack(anchor="e", pady=(S(6), S(0)))
            fr.update_idletasks()
            fw, fh = fr.winfo_reqwidth(), fr.winfo_reqheight()
            fr.place(x=max(8, min(anchor_x, W - fw - 8)),
                     y=max(8, min(anchor_y, H - fh - 8)))
            fr.lift()

        def show_action_menu(pts, canvas_x, canvas_y):
            dismiss_action_menu()
            state['selected_pts'] = pts
            fr = tk.Frame(popup, bg=BG_PANEL, highlightbackground=ACCENT_PINK,
                          highlightthickness=2, bd=0, padx=S(6), pady=S(6))
            state['action_frame'] = fr
            ax = prev_x + canvas_x + S(8)
            ay = prev_y + canvas_y + S(8)

            def _act_btn(text, cmd, accent):
                tk.Button(fr, text=text, command=cmd, bg=accent, fg="#ffffff",
                          relief="flat", font=("Segoe UI", FS(9), "bold"),
                          cursor="hand2", activebackground=accent,
                          activeforeground="#ffffff", padx=S(10), pady=S(4)
                          ).pack(side="left", padx=S(3))

            _act_btn("Delete", delete_selected_stroke, ORIGIN_RED)
            _act_btn("Make multi-colour",
                     lambda: show_colour_picker(ax, ay), ACCENT_PURP)
            tk.Button(fr, text="✕", command=dismiss_action_menu, bg=BG_INPUT,
                      fg=TEXT_DIM, relief="flat",
                      font=("Segoe UI", FS(9), "bold"), cursor="hand2",
                      activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
                      padx=S(6), pady=S(4)).pack(side="left", padx=(S(6), S(0)))

            fr.update_idletasks()
            fw, fh = fr.winfo_reqwidth(), fr.winfo_reqheight()
            fr.place(x=max(8, min(ax, W - fw - 8)),
                     y=max(8, min(ay, H - fh - 8)))
            fr.lift()
            for item_id, p in state['items']:
                if p is pts:
                    preview.itemconfigure(item_id, width=4)
                    break

        def on_preview_click(e):
            if not state['edit'] or not state['items']:
                return
            closest = preview.find_closest(e.x, e.y)
            if not closest:
                return
            for item_id, pts in state['items']:
                if item_id == closest[0]:
                    show_action_menu(pts, e.x, e.y)
                    return
            dismiss_action_menu()

        preview.bind("<Button-1>", on_preview_click)
        redraw_preview()

        # ── toggles ─────────────────────────────────────────────────────────
        # A grid of equal-weight columns rather than fixed widths: the row
        # always spans exactly the popup, however long a label gets.
        toggles = tk.Frame(popup, bg=BG_CARD)
        toggles.place(x=S(26), y=TOG_Y, width=W - S(52), height=S(32))
        state['tog_col'] = 0

        def _toggle_btn(text, cmd, on):
            col = state['tog_col']
            state['tog_col'] += 1
            toggles.grid_columnconfigure(col, weight=1, uniform="tog")
            b = ctk.CTkButton(
                toggles, text=text, command=cmd, width=S(60), height=S(30),
                fg_color="transparent", hover_color=BG_INPUT, border_width=1,
                border_color=ACCENT_AMBER if on else GLASS_EDGE,
                text_color=ACCENT_AMBER if on else TEXT_DIM,
                font=("Segoe UI", FS(9), "bold"), corner_radius=S(8))
            b.grid(row=0, column=col, sticky="ew", padx=(0, S(4)))
            return b

        def reprocess(**changes):
            """Re-trace the same photo with different options. The edits made
            so far belong to the old trace, so they're deliberately dropped."""
            new_opts = dict(state['opts'])
            new_opts.update(changes)
            new_opts['draw_dots'] = state['draw_dots']
            saved_frame = state['frame']
            self._close_notebook_review_popup()
            self._digitize_notebook_frame(saved_frame, new_opts)

        _toggle_btn(f"Grid snap: {'ON' if state['opts']['snap'] else 'OFF'}",
                    lambda: reprocess(snap=not state['opts']['snap']),
                    state['opts']['snap'])
        _toggle_btn(f"Ruled lines: {'removed' if state['opts']['remove_rules'] else 'kept'}",
                    lambda: reprocess(remove_rules=not state['opts']['remove_rules']),
                    state['opts']['remove_rules'])

        # Auto → force dark-on-light → force light-on-dark → auto. The label
        # says what is actually in force, including what "auto" decided.
        inv_opt = state['opts']['invert']
        inv_label = ({False: "Ink: dark on light",
                      True: "Ink: light on dark"}).get(
            inv_opt, "Ink: auto → " + ("light" if state['result']['inverted']
                                       else "dark"))
        _toggle_btn(inv_label,
                    lambda: reprocess(invert={"auto": False, False: True,
                                              True: "auto"}[inv_opt]),
                    state['result']['inverted'])

        dots_btn = _toggle_btn(
            f"Draw pulli: {'YES' if state['draw_dots'] else 'no'}",
            lambda: None, state['draw_dots'])

        def toggle_draw_dots():
            state['draw_dots'] = not state['draw_dots']
            dots_btn.configure(
                text=f"Draw pulli: {'YES' if state['draw_dots'] else 'no'}",
                border_color=ACCENT_AMBER if state['draw_dots'] else GLASS_EDGE,
                text_color=ACCENT_AMBER if state['draw_dots'] else TEXT_DIM)
        dots_btn.configure(command=toggle_draw_dots)

        # ── action row ──────────────────────────────────────────────────────
        # Same equal-weight grid as the toggles, so every action stays on
        # screen no matter how the popup is scaled.
        footer = tk.Frame(popup, bg=BG_CARD)
        footer.place(x=S(26), y=FOOT_Y, width=W - S(52), height=S(34))

        def _foot_btn(col, text, cmd, colour, weight=1):
            footer.grid_columnconfigure(col, weight=weight)
            b = ctk.CTkButton(
                footer, text=text, width=S(60), height=S(32),
                fg_color="transparent", hover_color=BG_INPUT, border_width=1,
                border_color=colour, text_color=colour,
                font=("Segoe UI", FS(10), "bold"), command=cmd)
            b.grid(row=0, column=col, sticky="ew", padx=(0, S(5)))
            return b

        edit_btn = _foot_btn(0, "Edit: OFF", lambda: None, TEXT_PRIMARY)

        def toggle_edit():
            state['edit'] = not state['edit']
            dismiss_action_menu()
            edit_btn.configure(
                text=f"Edit: {'ON' if state['edit'] else 'OFF'}",
                border_color=ACCENT_PINK if state['edit'] else GLASS_EDGE,
                text_color=ACCENT_PINK if state['edit'] else TEXT_PRIMARY)
            preview.configure(cursor="hand2" if state['edit'] else "arrow")
            redraw_preview()
        edit_btn.configure(command=toggle_edit)

        _foot_btn(1, "Skip", self._notebook_skip_page, TEXT_DIM)
        _foot_btn(2, "Place on Canvas",
                  lambda: self._notebook_place_page(state), ACCENT_PURP)
        # Pulli Mode: take only the dots and draw the lines by hand.
        _foot_btn(3, "✋ Pulli Mode",
                  lambda: self._notebook_trace_myself(state), ACCENT_CYAN)
        _foot_btn(4, "Save & Next →",
                  lambda: self._notebook_save_page(state, True), ACCENT_GREEN)

        self._fade(popup, 0.0, 0.96, 0.08)
        popup.lift()
        popup.focus_force()
        popup.grab_set()

    def _close_notebook_review_popup(self):
        win = self._notebook_review_popup
        if win is not None:
            try: win.grab_release()
            except Exception: pass
            try: win.destroy()
            except Exception: pass
            self._notebook_review_popup = None

    def _notebook_skip_page(self):
        """Throw this page away and go back for another one."""
        self._close_notebook_review_popup()
        self.log_to_console("Kolam Notebook: page skipped.", "info")
        if self._notebook_session is not None:
            self._open_notebook_capture_dialog()

    # ── saving a digitized page ──────────────────────────────────────────────
    @staticmethod
    def _nb_canvas_map(paths, dots):
        """Fit a photographed page onto the drawing area.

        Returns (to_canvas, scale). Both the traced strokes and the pulli go
        through the same map, so a page placed for the robot and the same
        page's dot grid placed as a Pulli Mode guide land in identical spots.
        """
        xs = [x for pts in paths for x, _ in pts] + [d[0] for d in dots]
        ys = [y for pts in paths for _, y in pts] + [d[1] for d in dots]
        min_x, min_y = min(xs), min(ys)
        span_x = max(max(xs) - min_x, 1e-9)
        span_y = max(max(ys) - min_y, 1e-9)
        scale = min(GRAPH_W / span_x, GRAPH_H / span_y) * 0.95
        off_x = MARGIN_L + (GRAPH_W - span_x * scale) / 2
        off_y = MARGIN_T + (GRAPH_H - span_y * scale) / 2

        def to_canvas(x, y):
            return off_x + (x - min_x) * scale, off_y + (y - min_y) * scale

        return to_canvas, scale

    def _notebook_trace_myself(self, state):
        """Pulli Mode. Put the page's dot grid on the canvas and nothing else,
        so every line in the finished rangoli is one she drew herself."""
        dots = state['dots']
        if not dots:
            self.show_hint_popup("No pulli found on this page to trace over")
            return
        session = self._notebook_session or {"book": "Kolam Notebook", "page": 1}
        # Map with the strokes in view even though they are being thrown away:
        # the dots then sit exactly where the traced page would have sat.
        to_canvas, scale = self._nb_canvas_map(state['remaining'] or [], dots)
        guides = [(*to_canvas(dx, dy), max(2.5, dr * scale))
                  for dx, dy, dr in dots]
        pitch = state['result'].get('pitch')

        self._close_notebook_review_popup()
        self._notebook_session = None
        # Let the review popup finish tearing its grab down before anything
        # else touches the window, or the app is left unresponsive.
        self.root.after(
            0, lambda: self._enter_pulli_mode(
                guides, (pitch * scale if pitch else None),
                f"{session['book']} — Page {session['page']}"))

    def _enter_pulli_mode(self, guides, pitch, label):
        """Canvas → bare dot grid, pen on. Shared by both entry points."""
        self._close_edit_popup()
        # The window is fullscreen and the popup we just closed was topmost;
        # on Windows that combination can leave the main window unfocused and
        # not repainting, which looks like the app has gone see-through.
        # Taking focus back explicitly forces it to redraw.
        try:
            self.root.lift()
            self.root.focus_force()
            self.root.update_idletasks()
        except tk.TclError:
            pass
        self._reset_canvas_state(keep_pulli=True)
        self._set_pulli_guides(guides, pitch=pitch, label=label)
        if not self.pen_mode_var.get():
            self.toggle_pen_mode()
        self.log_to_console(
            f"Pulli Mode: {len(guides)} dot(s) from {self._pulli_label}. "
            f"Draw the lines yourself — the robot lays only what you draw.",
            "recv")
        self.show_hint_popup(
            "Pulli Mode — connect the dots with the pen. Your lines only.")

    def _nb_build_design(self, state):
        """Turn the reviewed page into the same design dict My Designs uses,
        so a digitized page places on the canvas exactly like a saved one."""
        session = self._notebook_session or {"book": "Kolam Notebook", "page": 1}
        paths = state['remaining']
        if not paths:
            return None

        dots = state['dots']
        to_canvas, scale = self._nb_canvas_map(paths, dots)

        canvas_paths = [[to_canvas(x, y) for x, y in pts] for pts in paths]
        indexed = {}
        for idx, pts in enumerate(paths):
            col = state['path_colours'].get(id(pts))
            if col:
                indexed[str(idx)] = col

        def _round(ps):
            return [[[round(x, 2), round(y, 2)] for x, y in p] for p in ps]

        entry = {'paths': _round(canvas_paths), 'colour': None}
        if indexed:
            entry['path_colours'] = indexed
            entry['colour'] = next(iter(COLOUR_PALETTE))
        shapes = [entry]

        if state['draw_dots'] and dots:
            rings = []
            for dx, dy, dr in dots:
                cx, cy = to_canvas(dx, dy)
                rr = max(1.5, dr * scale)
                ring = [(cx + rr * math.cos(t * math.pi / 6),
                         cy + rr * math.sin(t * math.pi / 6))
                        for t in range(13)]
                rings.append(ring)
            shapes.append({'paths': _round(rings), 'colour': None})

        return {
            'name': f"{session['book']} — Page {session['page']}",
            'shapes': shapes,
            'notebook': {
                'book': session['book'],
                'page': session['page'],
                'dots': [[round(d[0], 1), round(d[1], 1)] for d in dots],
                # The same dots in canvas space, so the page can be re-opened
                # in Pulli Mode later without re-photographing it.
                'guide_dots': [[round(v, 2) for v in
                                (*to_canvas(d[0], d[1]), max(2.5, d[2] * scale))]
                               for d in dots],
                'guide_pitch': (round(state['result']['pitch'] * scale, 2)
                                if state['result'].get('pitch') else None),
                'draw_dots': bool(state['draw_dots']),
                'snapped': bool(state['result']['grid']),
                'ruled_lines_removed': bool(state['result']['rules_removed']),
                'captured': time.strftime("%Y-%m-%d %H:%M"),
            },
        }

    def _notebook_place_page(self, state):
        data = self._nb_build_design(state)
        if data is None:
            self.show_hint_popup("Nothing left on this page to place")
            return
        self._close_notebook_review_popup()
        self._place_saved_design(data)
        # Putting a page straight on the canvas ends the sitting — the user
        # wants to work with this design now, not photograph page after page.
        self._notebook_session = None
        self.show_hint_popup("Page placed on the canvas")

    def _notebook_save_page(self, state, go_next):
        data = self._nb_build_design(state)
        if data is None:
            self.show_hint_popup("Nothing left on this page to save")
            return
        session = self._notebook_session or {"book": "Kolam Notebook", "page": 1}
        safe_book = "".join(ch for ch in session['book']
                            if ch.isalnum() or ch in " _-").strip() or "notebook"
        fname = f"notebook_{safe_book}_p{int(session['page']):03d}.json"
        os.makedirs(MY_DESIGNS_DIR, exist_ok=True)
        out_path = os.path.join(MY_DESIGNS_DIR, fname)
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError as e:
            self.log_to_console(f"Kolam Notebook: could not save page — {e}",
                                "err")
            return

        n_strokes = len(data['shapes'][0]['paths'])
        self.log_to_console(
            f"Kolam Notebook: saved '{data['name']}' "
            f"({n_strokes} stroke(s), {len(data['notebook']['dots'])} pulli) "
            f"→ {out_path}", "recv")
        self._close_notebook_review_popup()

        if self._notebook_session is not None:
            self._notebook_session['saved'] = \
                self._notebook_session.get('saved', 0) + 1
            self._notebook_session['page'] = int(session['page']) + 1

        if go_next and self._notebook_session is not None:
            self._open_notebook_capture_dialog()
        else:
            self.show_hint_popup(
                f"Saved as '{data['name']}' under \U0001f4d6 Notebook")

    # ── AI GENERATED DESIGN (OpenAI) ──────────────────────────────────────
    def _get_openai_api_key(self):
        """The OpenAI key, taken from the first place that actually has one.

        In order:
          1. the OPENAI_API_KEY environment variable
          2. openai_key.txt sitting next to the app (AI_KEY_FILE)
          3. HARDCODED_API_KEY below

        Prefer either of the first two. A key pasted into the source travels
        with every copy of the file — the shared folder, the USB stick, the
        bundle handed to the judges — and the only way to undo that is to
        revoke the key at the provider.
        """
        env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if env_key:
            return env_key

        try:
            with open(AI_KEY_FILE, "r", encoding="utf-8") as fh:
                file_key = fh.read().strip()
            if file_key:
                return file_key
        except OSError:
            pass                      # no key file is the normal case

        HARDCODED_API_KEY = ""
        return HARDCODED_API_KEY.strip() or None

    def _forget_openai_api_key(self):
        try:
            if os.path.exists(AI_KEY_FILE):
                os.remove(AI_KEY_FILE)
        except Exception:
            pass

    def _open_rangoli_quiz_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Rangoli Design Preferences")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=S(16), pady=S(16))

        tk.Label(pad, text="Tell us about your design", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(12), "bold")).pack(anchor="w", pady=(S(0), S(12)))

        def add_field(label_text, options):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(10))).pack(anchor="w", pady=(S(6), S(2)))
            var = tk.StringVar(value=options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=S(340), fg_color=BG_INPUT, border_color=GLASS_EDGE,
                button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
                text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(10)))
            combo.pack(anchor="w", pady=(S(0), S(8)))
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
                      text_color="#ffffff", font=("Segoe UI", FS(11), "bold"),
                      height=S(38), corner_radius=S(8)).pack(fill="x", pady=(S(12), S(0)))

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _start_ai_generation_with_quiz(self, quiz_answers):
        theme = (
            f"A rangoli in the {quiz_answers[0]} style, "
            f"made for {quiz_answers[1]}, "
            f"with {quiz_answers[2].lower()} complexity, "
            f"featuring {quiz_answers[3].lower()} colours, "
            f"using {quiz_answers[4].lower()} materials."
        )
        self._start_ai_generation(custom_theme=theme)

    def generate_ai_design(self):
        if self._ai_generating:
            return
        self.selected_preset.set("")
        self._open_design_choice_dialog()

    def _open_design_choice_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("How do you want to generate?")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=S(20), pady=S(20))

        tk.Label(pad, text="How do you want to generate your rangoli?", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", FS(12), "bold")).pack(anchor="w", pady=(S(0), S(16)))

        def on_close():
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row, text="Answer Questions",
            command=lambda: (dlg.destroy(), self._open_rangoli_quiz_dialog()),
            fg_color=ACCENT_PURP, hover_color="#8b5cf6",
            text_color="#ffffff", font=("Segoe UI", FS(11), "bold"),
            height=S(40), corner_radius=S(8)
        ).pack(fill="x", pady=(S(0), S(8)))

        ctk.CTkButton(
            btn_row, text="Surprise Me",
            command=lambda: (dlg.destroy(), self._start_ai_generation()),
            fg_color=ACCENT_CYAN, hover_color="#0891b2",
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(40), corner_radius=S(8)
        ).pack(fill="x", pady=(S(0), S(8)))

        ctk.CTkButton(
            btn_row, text="Type Your Idea",
            command=lambda: (dlg.destroy(), self._open_ai_prompt_dialog()),
            fg_color=ACCENT_AMBER, hover_color="#b45309",
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(40), corner_radius=S(8)
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
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))

        tk.Label(pad, text="Describe the rangoli you'd like",
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold")).pack(anchor="w")
        tk.Label(pad, text="e.g. \"peacock feathers and lotus\", \"Diwali diyas\", "
                            "\"simple geometric with stars\"",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                 justify="left", wraplength=S(360)).pack(anchor="w", pady=(S(2), S(10)))

        entry = ctk.CTkEntry(
            pad, width=S(360), height=S(36),
            placeholder_text="Type your rangoli idea here...",
            fg_color=BG_INPUT, border_color=GLASS_EDGE,
            text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11)))
        entry.pack(fill="x")

        note = tk.Label(
            pad, text="Only rangoli / mandala designs can be generated here — "
                       "anything you type is used as a theme for a rangoli, "
                       "not a literal picture.",
            bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
            justify="left", wraplength=S(360))
        note.pack(anchor="w", pady=(S(6), S(14)))

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
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8)
        ).pack(side="left", expand=True, fill="x", padx=(S(0), S(6)))

        ctk.CTkButton(
            btn_row, text="Surprise Me", command=start_random,
            fg_color=ACCENT_PURP, hover_color=self._lighten(ACCENT_PURP, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8)
        ).pack(side="left", expand=True, fill="x", padx=(S(6), S(0)))

        tk.Label(pad, text="— or —", bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(9))).pack(pady=(S(10), S(6)))

        def start_guided():
            on_close()
            self._open_guided_dialog()

        ctk.CTkButton(
            pad, text="Answer 4 Quick Questions", command=start_guided,
            fg_color=ACCENT_AMBER, hover_color=self._lighten(ACCENT_AMBER, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8)
        ).pack(fill="x")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")

        entry.focus_set()
        dlg.grab_set()

    def _open_guided_dialog(self):
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
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))

        tk.Label(pad, text="A few quick questions", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold")).pack(anchor="w", pady=(S(0), S(10)))

        def add_field(label_text, options, default=None):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(10))).pack(anchor="w", pady=(S(6), S(2)))
            var = tk.StringVar(value=default or options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=S(360), fg_color=BG_INPUT, border_color=GLASS_EDGE,
                button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
                text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(10)))
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
        btn_row.pack(fill="x", pady=(S(16), S(0)))

        ctk.CTkButton(
            btn_row, text="Generate", command=submit,
            fg_color=ACCENT_AMBER, hover_color=self._lighten(ACCENT_AMBER, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8)
        ).pack(fill="x")

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")

        dlg.grab_set()

    def _sanitize_theme(self, text):
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

    def _plotter_constraints_suffix(self):
        """Physical-drawing constraints shared by every rangoli image prompt
        — the design still has to survive contour-tracing into a handful of
        clean outlines a 28mm powder-dispensing robot can actually draw."""
        return (
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

    def _call_openai_for_rangoli_image(self, api_key, custom_theme=None):
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
        prompt = base_prompt + self._plotter_constraints_suffix()
        return self._call_openai_image_generation(api_key, prompt)

    def _call_openai_image_generation(self, api_key, prompt):
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

    # ── IMPORT FROM PHOTO (any photograph → drawable design, offline) ───────
    # Her patterns don't only live in the kolam puthagam: there are paper
    # sketches, chalk drawings on the floor, and old photographs of finished
    # kolams. This points the existing image→paths pipeline at any one of
    # those. No internet, no AI — just the photo, cleaned up.
    def _launch_photo_import(self):
        path = filedialog.askopenfilename(
            title="Choose a photo of a kolam, sketch or drawing",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.bmp *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            self._close_features_popup()
        except Exception:
            pass
        name = os.path.basename(path)
        self.log_to_console(f"Import from photo: reading '{name}'…", "info")

        try:
            import cv2
            import numpy as np
        except ImportError:
            self.log_to_console("Import from photo: opencv-python is required. "
                                "Run: pip install opencv-python", "err")
            return

        try:
            # np.fromfile + imdecode, not imread: imread silently fails on
            # non-ASCII paths on Windows.
            frame = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                                 cv2.IMREAD_COLOR)
        except Exception as e:
            self.log_to_console(f"Import from photo: could not open '{name}' "
                                f"— {e}", "err")
            return
        if frame is None:
            self.log_to_console(f"Import from photo: '{name}' isn't a readable "
                                f"image.", "err")
            return

        # The notebook tracer, not the AI-image tracer. The AI one keeps only
        # outermost contours, so a kolam whose lines all touch comes back as
        # its silhouette and nothing else; this one follows the centre of every
        # stroke, inner loops included, which is what the robot has to draw.
        #
        # Two of its page settings are wrong for an arbitrary photo, though.
        # Rule removal is for printed notebook paper, and it deletes any ink
        # that stays straight across a tenth of the frame — on a floor kolam
        # that is a border line, not a rule. And grid snapping straightens a
        # drawing onto its pulli lattice, which a chalk drawing or an old
        # photograph has no reason to sit on.
        try:
            result = self._nb_process_page(frame, snap=False,
                                           remove_rules=False, invert="auto")
        except ImportError as e:
            self.log_to_console(f"Import from photo: {e}", "err")
            return
        except Exception as e:
            self.log_to_console(f"Import from photo: could not read '{name}' "
                                f"— {e}", "err")
            return

        paths = result['paths']
        if not paths:
            self.log_to_console(
                "Import from photo: no lines could be traced out of that "
                "photo. A flatter, better-lit shot of the drawing — filling "
                "the frame, with no shadow across it — usually fixes it.",
                "err")
            self.show_hint_popup("Nothing traceable in that photo — try more light")
            return

        to_canvas, _scale = self._nb_canvas_map(paths, result['dots'])
        canvas_paths = [[to_canvas(x, y) for x, y in pts] for pts in paths]

        stem = os.path.splitext(name)[0]
        self._place_saved_design({'name': stem,
                                  'shapes': [{'paths': canvas_paths,
                                              'colour': None}]})
        total_pts = sum(len(p) for p in canvas_paths)
        self.log_to_console(
            f"Import from photo: traced {len(canvas_paths)} stroke(s), "
            f"{total_pts} points from '{name}'"
            + (", light-on-dark photo inverted" if result['inverted'] else "")
            + ". Drag or resize it before sending.", "recv")
        self.show_hint_popup(f"Traced {len(canvas_paths)} stroke(s) from the photo")

    # ── PICTURE TO RANGOLI (computer webcam + OpenAI) ───────────────────────
    def _open_picture_capture_dialog(self):
        """Open the computer's own webcam so the user can photograph their
        doorstep/surroundings, then hand it off to the mood/occasion step.
        Uses the default device (index 0) directly — separate from the
        robot's "installed camera" system used by Learn Mode, since this is
        explicitly the computer's built-in/USB webcam, not the rig's."""
        if self._ai_generating:
            return
        existing = getattr(self, '_picture_capture_dlg', None)
        if existing is not None:
            try:
                existing.lift()
                existing.focus_force()
                return
            except Exception:
                self._picture_capture_dlg = None

        try:
            import cv2
        except ImportError:
            self.log_to_console(
                "Picture to Rangoli: OpenCV not available — "
                "run: pip install opencv-python", "err")
            return

        cap = cv2.VideoCapture(0, self._camera_backend())
        if not cap.isOpened():
            cap.release()
            self.log_to_console(
                "Picture to Rangoli: couldn't open the computer's webcam.",
                "err")
            return

        from PIL import Image, ImageTk

        dlg = tk.Toplevel(self.root)
        dlg.title("Picture to Rangoli — Capture Photo")
        dlg.configure(bg=BG_CARD)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        self._picture_capture_dlg = dlg
        state = {"cap": cap, "frozen": None, "after_id": None, "live": True}

        def on_close():
            state["live"] = False
            if state["after_id"] is not None:
                try: dlg.after_cancel(state["after_id"])
                except Exception: pass
            try: state["cap"].release()
            except Exception: pass
            self._picture_capture_dlg = None
            try: dlg.destroy()
            except Exception: pass

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        pad = tk.Frame(dlg, bg=BG_CARD)
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))

        tk.Label(pad, text="Photograph your doorstep / surroundings",
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold")).pack(anchor="w")
        tk.Label(pad, text="The AI will suggest a rangoli inspired by the "
                            "space, the mood you pick, and today's date. "
                            "The photo is only sent to OpenAI for this — "
                            "it isn't saved to disk.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                 justify="left", wraplength=S(420)).pack(anchor="w", pady=(S(2), S(10)))

        view_w, view_h = 420, 315
        video_frame = tk.Frame(pad, bg="#000000", width=view_w, height=view_h)
        video_frame.pack_propagate(False)
        video_frame.pack()
        video_lbl = tk.Label(video_frame, bg="#000000")
        video_lbl.pack(fill="both", expand=True)

        def update_frame():
            if not state["live"]:
                return
            ok, frame = state["cap"].read()
            if ok and frame is not None:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb).resize((view_w, view_h))
                state["photo_img"] = ImageTk.PhotoImage(pil_img)
                video_lbl.configure(image=state["photo_img"])
            state["after_id"] = dlg.after(66, update_frame)

        btn_row = tk.Frame(pad, bg=BG_CARD)
        btn_row.pack(fill="x", pady=(S(12), S(0)))

        def do_capture():
            ok, frame = state["cap"].read()
            if not ok or frame is None:
                self.log_to_console(
                    "Picture to Rangoli: couldn't grab a frame — try again.",
                    "err")
                return
            state["frozen"] = frame
            state["live"] = False
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb).resize((view_w, view_h))
            state["photo_img"] = ImageTk.PhotoImage(pil_img)
            video_lbl.configure(image=state["photo_img"])
            capture_btn.pack_forget()
            retake_btn.pack(side="left", expand=True, fill="x", padx=(S(0), S(6)))
            use_btn.pack(side="left", expand=True, fill="x", padx=(S(6), S(0)))

        def do_retake():
            state["frozen"] = None
            state["live"] = True
            retake_btn.pack_forget()
            use_btn.pack_forget()
            capture_btn.pack(fill="x")
            update_frame()

        def do_use():
            frame = state["frozen"]
            if frame is None:
                return
            on_close()
            self._open_picture_to_rangoli_details(frame)

        capture_btn = ctk.CTkButton(
            btn_row, text="📷 Capture Photo", command=do_capture,
            fg_color=ACCENT_PINK, hover_color=self._lighten(ACCENT_PINK, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8))
        capture_btn.pack(fill="x")

        retake_btn = ctk.CTkButton(
            btn_row, text="Retake", command=do_retake,
            fg_color=BG_INPUT, hover_color=GLASS_EDGE,
            text_color=TEXT_PRIMARY, font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8))
        use_btn = ctk.CTkButton(
            btn_row, text="Use This Photo →", command=do_use,
            fg_color=ACCENT_CYAN, hover_color=self._lighten(ACCENT_CYAN, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8))

        update_frame()

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _open_picture_to_rangoli_details(self, frame):
        """Mood + occasion step, shown after a photo is captured/confirmed.
        `frame` is the raw BGR numpy frame from OpenCV (kept in memory only)."""
        existing = getattr(self, '_ai_dialog', None)
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass
            self._ai_dialog = None

        dlg = tk.Toplevel(self.root)
        dlg.title("Picture to Rangoli — Mood & Occasion")
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
        pad.pack(fill="both", expand=True, padx=S(18), pady=S(16))

        tk.Label(pad, text="What's the mood and occasion?", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", FS(13), "bold")
                 ).pack(anchor="w", pady=(S(0), S(6)))

        today_str = time.strftime("%A, %d %B %Y")
        tk.Label(pad, text=f"Today: {today_str}", bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(9))).pack(anchor="w", pady=(S(0), S(12)))

        def add_field(label_text, options):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(10))).pack(anchor="w", pady=(S(6), S(2)))
            var = tk.StringVar(value=options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=S(360), fg_color=BG_INPUT, border_color=GLASS_EDGE,
                button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
                text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
                dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", FS(10)))
            combo.pack(anchor="w")
            return var

        mood_var = add_field(
            "1. What mood are you going for?",
            ["Surprise me / let AI decide", "Joyful & festive",
             "Calm & peaceful", "Devotional & spiritual",
             "Vibrant & energetic", "Elegant & minimalist", "Romantic"])

        occasion_var = add_field(
            "2. Occasion?",
            ["Auto-detect from today's date",
             "Everyday / no specific occasion", "Diwali", "Pongal / Sankranti",
             "Onam", "Navratri", "Ugadi / Gudi Padwa", "Wedding",
             "Housewarming", "Other festival / celebration"])

        def submit():
            mood = mood_var.get()
            occasion = occasion_var.get()
            on_close()
            self._start_picture_to_rangoli_generation(
                frame, mood, occasion, today_str)

        ctk.CTkButton(
            pad, text="Generate My Rangoli", command=submit,
            fg_color=ACCENT_PINK, hover_color=self._lighten(ACCENT_PINK, -30),
            text_color="#0d0b2b", font=("Segoe UI", FS(11), "bold"),
            height=S(38), corner_radius=S(8)
        ).pack(fill="x", pady=(S(14), S(0)))

        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{max(rx,0)}+{max(ry,0)}")
        dlg.grab_set()

    def _start_picture_to_rangoli_generation(self, frame, mood, occasion,
                                              date_str):
        if self._ai_generating:
            return
        api_key = self._get_openai_api_key()
        if not api_key:
            self.log_to_console(
                "Picture to Rangoli: no API key entered, so nothing was "
                "generated.", "err")
            return

        self._ai_generating = True
        self.log_to_console(
            "Picture to Rangoli: looking at your photo and sketching a "
            "design...", "info")
        self.show_hint_popup("Turning your photo into a rangoli...")
        threading.Thread(
            target=self._picture_to_rangoli_worker,
            args=(api_key, frame, mood, occasion, date_str), daemon=True
        ).start()

    def _call_openai_vision_scene_description(self, api_key, jpg_bytes):
        import base64
        data_url = "data:image/jpeg;base64," + \
            base64.b64encode(jpg_bytes).decode("ascii")
        body = {
            "model": "gpt-4o-mini",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "In one short sentence (under 30 words), describe "
                        "the setting in this photo of a doorstep/entrance "
                        "area — its colours, materials, architectural "
                        "style, and overall feel. Do not describe or "
                        "identify any people, and do not try to identify "
                        "the location — describe the space and atmosphere "
                        "only."
                    )},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            "max_tokens": 120,
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
        return result["choices"][0]["message"]["content"].strip()

    def _build_picture_to_rangoli_prompt(self, scene_desc, mood, occasion,
                                          date_str):
        mood_clause = ""
        if mood and not mood.startswith("Surprise"):
            mood_clause = (
                f" The design should feel {mood.lower()} in its choice of "
                "motif shapes and rhythm."
            )

        if occasion.startswith("Auto-detect"):
            occasion_clause = (
                f" Today's date is {date_str}. If this date falls on or "
                "near a widely observed Indian festival or seasonal "
                "occasion, let that occasion subtly influence the motifs; "
                "otherwise treat it as an everyday design."
            )
        elif occasion.startswith("Everyday"):
            occasion_clause = (
                f" Today's date is {date_str}; this is an everyday design, "
                "not tied to any specific festival."
            )
        else:
            occasion_clause = (
                f" This is being made for {occasion} (today is {date_str})."
            )

        base_prompt = (
            "Create an original traditional Indian rangoli / mandala "
            "floor-art design, loosely inspired by this real doorstep/"
            f"entrance setting: \"{scene_desc}\". Use the setting only as "
            "soft inspiration for motif style and rhythm — the result must "
            "still be unmistakably a rangoli: a radially symmetric pattern "
            "of petal, floral, or geometric motifs arranged around a "
            "centre point, not a realistic illustration, photo, or scene."
            + mood_clause + occasion_clause +
            " Black outlines only on a white background. No fills, colors, "
            "shading, gradients, textures, or 3D rendering."
        )
        return base_prompt + self._plotter_constraints_suffix()

    def _picture_to_rangoli_worker(self, api_key, frame, mood, occasion,
                                    date_str):
        try:
            import cv2
            ok, jpg_buf = cv2.imencode(".jpg", frame)
            if not ok:
                raise ValueError("Could not encode the captured photo.")
            scene_desc = self._call_openai_vision_scene_description(
                api_key, jpg_buf.tobytes())
            prompt = self._build_picture_to_rangoli_prompt(
                scene_desc, mood, occasion, date_str)
            img_bytes = self._call_openai_image_generation(api_key, prompt)
            canvas_paths, tk_img = self._extract_paths_from_image_bytes(img_bytes)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            if e.code == 401:
                self._forget_openai_api_key()
                msg = ("Picture to Rangoli: that API key was rejected. "
                       "Try again to enter a fresh one.")
            else:
                msg = (f"Picture to Rangoli: OpenAI returned an error "
                       f"({e.code}). {body[:200]}")
            self.root.after(0, lambda: self.log_to_console(msg, "err"))
        except ImportError as e:
            err = str(e)
            self.root.after(0, lambda: self.log_to_console(
                f"Picture to Rangoli: missing library ({err}).", "err"))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda: self.log_to_console(
                f"Picture to Rangoli: something went wrong ({err}).", "err"))
        else:
            self.root.after(0, lambda: self._apply_ai_design(canvas_paths, tk_img))
        finally:
            self._ai_generating = False
            self.root.after(0, lambda: self.hide_hint_popup(instant=True))

    # ── Hint popup ────────────────────────────────────────────────────────────
    def on_shape_type_selected(self, event=None):
        # A half-drawn line must not survive a switch to another shape.
        self._cancel_line_draw()
        if self.shape_type.get() == "Select":
            self.hide_hint_popup(instant=True)
        elif self.shape_type.get() == "Line":
            self.show_hint_popup("Click the start point of the line")
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
        self._draw_rounded_rect(glass, 4, 4, w-4, h-4, radius=S(20),
                                fill=BG_CARD, outline=ACCENT_CYAN, width=1)
        glass.create_text(w//2, h//2, text=message,
                          fill=ACCENT_CYAN, font=("Segoe UI", FS(10), "bold"))
        self.hint_popup = popup
        self._fade(popup, 0.0, 0.95, 0.08)
        self.hint_after_id = self.root.after(4500, self.hide_hint_popup)

    def _arm_colour_emptied_button(self, event, colour=""):
        """Enable the canvas Emptied button and show a modal colour-switch popup."""
        self._pending_colour_event = event
        self.colour_emptied_btn.configure(
            state="normal", fg_color=ACCENT_AMBER, hover_color=ACCENT_AMBER,
            text_color="#ffffff")
        self._show_colour_switch_popup(colour)

    def _show_colour_switch_popup(self, colour):
        """Blocking-style UI: operator must empty the nozzle, then continue."""
        self._close_colour_switch_popup()
        self.root.update_idletasks()

        W, H = S(420), S(260)
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try:
            popup.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._colour_switch_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(
            glass, 4, 4, W - 4, H - 4, radius=S(20),
            fill=BG_CARD, outline=ACCENT_AMBER, width=2)
        glass.create_text(
            24, 28, text="Colour change required", anchor="w",
            fill=TEXT_PRIMARY, font=("Segoe UI", FS(14), "bold"))

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=24, y=56, width=W - 48, height=H - 76)

        tk.Label(
            body,
            text="The nozzle is open at origin.\n"
                 "Empty out the current colour, then load the next one.",
            bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
            justify="left", wraplength=W - 56,
        ).pack(anchor="w", pady=(S(0), S(14)))

        next_row = tk.Frame(body, bg=BG_CARD)
        next_row.pack(anchor="w", fill="x", pady=(S(0), S(18)))
        tk.Label(
            next_row, text="Next colour:", bg=BG_CARD, fg=TEXT_DIM,
            font=("Segoe UI", FS(10), "bold"),
        ).pack(side="left")

        swatch = COLOUR_PALETTE.get(colour, ACCENT_AMBER)
        sw = tk.Canvas(next_row, width=S(22), height=S(22), bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(S(10), S(8)))
        sw.create_oval(2, 2, 20, 20, fill=swatch, outline="#ffffff", width=1)
        tk.Label(
            next_row, text=colour or "next colour", bg=BG_CARD, fg=TEXT_PRIMARY,
            font=("Segoe UI", FS(12), "bold"),
        ).pack(side="left")

        ctk.CTkButton(
            body,
            text="Colour emptied, continue",
            command=self._on_colour_emptied_click,
            fg_color="#f97316", hover_color="#fb923c",
            border_width=2, border_color="#facc15",
            text_color="#ffffff", font=("Segoe UI", FS(12), "bold"),
            height=S(42), corner_radius=S(10),
        ).pack(fill="x", pady=(S(4), S(0)))

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        try:
            popup.grab_set()
        except tk.TclError:
            pass

    def _close_colour_switch_popup(self):
        popup = self._colour_switch_popup
        if popup is None:
            return
        try:
            popup.grab_release()
        except Exception:
            pass
        try:
            popup.destroy()
        except Exception:
            pass
        self._colour_switch_popup = None

    def _on_colour_emptied_click(self):
        event = self._pending_colour_event
        if event is None:
            return
        self._pending_colour_event = None
        self.colour_emptied_btn.configure(
            state="disabled", fg_color="#4b5563", hover_color="#4b5563",
            text_color=TEXT_DIM)
        self._close_colour_switch_popup()
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
    def _draw_dxf_thumbnail(self, canvas, raw_paths, cx, cy, size):
        all_x = [x for pts in raw_paths for x, _ in pts]
        all_y = [y for pts in raw_paths for _, y in pts]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        span = max(max_x - min_x, max_y - min_y, 1e-9)
        scale = (size * 2) / span
        mid_x = (min_x + max_x) / 2
        mid_y = (min_y + max_y) / 2

        def to_canvas(x, y):
            return cx + (x - mid_x) * scale, cy - (y - mid_y) * scale

        for pts in raw_paths:
            if len(pts) < 2:
                continue
            flat = [c for pt in pts for c in to_canvas(*pt)]
            canvas.create_line(flat, fill=ACCENT_BLUE, width=2, smooth=True)

    def _load_predesigned_dxf_library(self):
        """Pull funnel.dxf / image.dxf fresh from Downloads. No editing here."""
        library = {}
        for name, filename in PREDESIGNED_DXF.items():
            full_path = os.path.join(DOWNLOADS_DIR, filename)
            if not os.path.isfile(full_path):
                self.log_to_console(f"Pre-designed library: '{filename}' not found "
                                     f"in Downloads.", "err")
                continue
            raw_paths, err = self._parse_dxf_file(full_path)
            if err:
                self.log_to_console(f"Pre-designed library: {filename} — {err}", "err")
                continue
            library[name] = raw_paths
        return library

    # ── My Designs (save canvas → gallery) ───────────────────────────────────
    def _save_design_to_gallery(self):
        if not self.shapes:
            self.show_hint_popup("Nothing on the canvas to save yet")
            return
        dlg = ctk.CTkInputDialog(text="Name this design:",
                                 title="Save to My Designs")
        name = (dlg.get_input() or "").strip()
        if not name:
            return
        safe = "".join(ch for ch in name
                       if ch.isalnum() or ch in " _-").strip() or "design"
        data = {'name': name, 'shapes': []}
        for s in self.shapes:
            entry = {'paths': [[[round(x, 2), round(y, 2)] for x, y in p]
                               for p in self._shape_paths(s) if len(p) >= 2],
                     'colour': s.get('colour')}
            if s.get('path_colours'):
                entry['path_colours'] = {str(k): v for k, v
                                         in s['path_colours'].items()}
            if entry['paths']:
                data['shapes'].append(entry)
        os.makedirs(MY_DESIGNS_DIR, exist_ok=True)
        out_path = os.path.join(MY_DESIGNS_DIR, safe + ".json")
        try:
            with open(out_path, "w") as fh:
                json.dump(data, fh)
        except OSError as e:
            self.log_to_console(f"Could not save design: {e}", "err")
            return
        self.log_to_console(f"Design saved to gallery: {out_path}", "info")
        self.show_hint_popup(f"Saved '{name}' to My Designs in the gallery")

    def _load_saved_designs(self):
        out = []
        if not os.path.isdir(MY_DESIGNS_DIR):
            return out
        for fn in sorted(os.listdir(MY_DESIGNS_DIR)):
            if not fn.lower().endswith(".json"):
                continue
            full = os.path.join(MY_DESIGNS_DIR, fn)
            try:
                # Explicitly UTF-8: without it Python opens in the Windows
                # default code page, which cannot decode a design whose name
                # or book title is in an Indian script. Everything written
                # here escapes non-ASCII anyway, so this also reads every
                # design saved by earlier versions unchanged.
                with open(full, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get('shapes'):
                    out.append((data.get('name', fn[:-5]), full, data))
            except Exception as e:
                self.log_to_console(f"My Designs: could not read {fn} — {e}",
                                    "err")
        return out

    def _place_saved_design(self, data):
        self._close_gallery_popup()
        added = 0
        for entry in data.get('shapes', []):
            paths = [[(float(x), float(y)) for x, y in p]
                     for p in entry.get('paths', []) if len(p) >= 2]
            if not paths:
                continue
            shape = {'type': 'Pen', 'paths': paths,
                     'x': paths[0][0][0], 'y': paths[0][0][1],
                     'size': 0, 'colour': entry.get('colour')}
            pc = entry.get('path_colours') or {}
            if pc:
                shape['path_colours'] = {int(k): v for k, v in pc.items()}
            self.shapes.append(shape)
            added += 1
        self.selected_shape_index = None
        self.redraw()
        self.log_to_console(
            f"Placed saved design '{data.get('name', '?')}' "
            f"({added} shape(s)).", "recv")

    @staticmethod
    def _saved_guide_dots(data):
        """Canvas-space pulli stored with a digitized page, if it has any.
        Pages saved before Pulli Mode existed simply don't offer it."""
        nb = data.get('notebook')
        if not isinstance(nb, dict):
            return []
        out = []
        for d in nb.get('guide_dots') or []:
            try:
                out.append((float(d[0]), float(d[1]), float(d[2])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _pulli_from_saved(self, data):
        """Re-open a saved notebook page as its dot grid alone."""
        guides = self._saved_guide_dots(data)
        if not guides:
            self.show_hint_popup("This page has no pulli grid saved")
            return
        nb = data.get('notebook') or {}
        self._close_gallery_popup()
        self.root.after(
            0, lambda: self._enter_pulli_mode(
                guides, nb.get('guide_pitch'),
                data.get('name', 'Notebook page')))

    def _delete_saved_design(self, name, full_path):
        if not messagebox.askyesno("Delete design",
                                   f"Delete '{name}' from My Designs?"):
            return
        try:
            os.remove(full_path)
            self.log_to_console(f"Deleted saved design '{name}'.", "info")
        except OSError as e:
            self.log_to_console(f"Could not delete '{name}': {e}", "err")
        self.show_gallery_popup()

    @staticmethod
    def _nb_page_no(data):
        """Page number of a digitized notebook design, or 0 if it isn't one."""
        nb = data.get('notebook')
        if not isinstance(nb, dict):
            return 0
        try:
            return int(nb.get('page', 0))
        except (TypeError, ValueError):
            return 0

    def _draw_flat_paths_thumbnail(self, canvas, paths, cx, cy, size):
        """Thumbnail for canvas-coordinate (y-down) paths."""
        xs = [x for p in paths for x, _ in p]
        ys = [y for p in paths for _, y in p]
        if not xs:
            return
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        sc = (size * 2) / span
        ox = cx - (min(xs) + max(xs)) / 2 * sc
        oy = cy - (min(ys) + max(ys)) / 2 * sc
        for p in paths:
            if len(p) < 2:
                continue
            flat = [c for x, y in p for c in (x * sc + ox, y * sc + oy)]
            canvas.create_line(flat, fill=ACCENT_PURP, width=2, smooth=True)

    # ── Daily rotation from her notebook ("Kolam of the Day") ────────────────
    # One page a morning, festival aware, never yesterday's again. Everything
    # here reads the digitized pages already in My Designs — nothing is
    # generated, so what the household draws is always one of her own designs.

    @staticmethod
    def _daily_page_key(full_path):
        """Stable id for a page: its filename. Survives renaming the book."""
        return os.path.basename(full_path)

    @staticmethod
    def _daily_complexity(data):
        """How much work a page is — stroke count plus total stroke length in
        canvas px, plus its pulli count. Measured off her own page, so
        'elaborate' means elaborate in her hand, not by some rubric."""
        paths = [p for entry in data.get('shapes', [])
                 for p in entry.get('paths', []) if len(p) >= 2]
        length = 0.0
        for p in paths:
            for (x0, y0), (x1, y1) in zip(p, p[1:]):
                length += math.hypot(x1 - x0, y1 - y0)
        nb = data.get('notebook') or {}
        return len(paths) * 12 + length / 10.0 + len(nb.get('dots') or []) * 4

    def _daily_pages(self):
        """Every digitized notebook page available to the rotation."""
        return [(name, full, data) for name, full, data
                in self._load_saved_designs()
                if isinstance(data.get('notebook'), dict)]

    def _load_rotation_state(self):
        try:
            with open(DAILY_ROTATION_FILE, encoding="utf-8") as fh:
                state = json.load(fh)
            if isinstance(state, dict):
                state.setdefault('history', [])
                state.setdefault('shown_on', "")
                state.setdefault('reroll', 0)
                return state
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            self.log_to_console(f"Kolam of the Day: could not read "
                                f"{os.path.basename(DAILY_ROTATION_FILE)} — {e}",
                                "err")
        return {'history': [], 'shown_on': "", 'reroll': 0}

    def _save_rotation_state(self, state):
        state['history'] = state.get('history', [])[-(DAILY_HISTORY_KEEP * 2):]
        try:
            with open(DAILY_ROTATION_FILE, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=1)
        except OSError as e:
            self.log_to_console(f"Kolam of the Day: could not save the "
                                f"rotation history — {e}", "err")

    def _pick_daily_page(self, day, reroll=0):
        """Choose today's page. Returns ``(entry, occasion_label, note)`` or
        ``(None, ...)`` when the notebook is still empty.

        Deterministic for a given (day, reroll): re-opening the app on the same
        morning proposes the same page rather than a fresh lottery each time.
        """
        label, note, elaborate = _daily_occasion(day)
        pages = self._daily_pages()
        if not pages:
            return None, label, note

        state  = self._load_rotation_state()
        history = [h for h in state.get('history', []) if isinstance(h, dict)]
        recent = [h.get('key') for h in history
                  if h.get('date') != day.isoformat()]
        recent.reverse()                      # most recent first
        # "Show another" must actually show another, so the page already
        # proposed this morning joins the front of the avoid list.
        if reroll:
            recent = [h.get('key') for h in history
                      if h.get('date') == day.isoformat()] + recent

        # Skip whatever has come up lately, but never let the rule starve the
        # pool: with three pages in the book, "not the last two" is the most
        # that can be honoured.
        avoid = set(recent[:max(0, min(len(recent), len(pages) - 1,
                                       DAILY_HISTORY_KEEP))])
        fresh = [p for p in pages if self._daily_page_key(p[1]) not in avoid]
        if not fresh:
            # Yesterday's page is the one exclusion that always applies.
            yesterday = recent[0] if recent else None
            fresh = [p for p in pages
                     if self._daily_page_key(p[1]) != yesterday] or pages

        # Festival / Margazhi / Friday mornings draw from the fuller half of
        # what's left; ordinary mornings from the quicker half.
        fresh.sort(key=lambda t: self._daily_complexity(t[2]),
                   reverse=bool(elaborate))
        tier = fresh[:max(3, (len(fresh) + 1) // 2)]

        rng = random.Random(f"{day.isoformat()}|{reroll}|{len(pages)}")
        return rng.choice(tier), label, note

    def _record_daily_pick(self, day, entry, reroll):
        """Remember what was proposed today so tomorrow doesn't repeat it."""
        state = self._load_rotation_state()
        key   = self._daily_page_key(entry[1])
        hist  = [h for h in state.get('history', [])
                 if isinstance(h, dict) and h.get('date') != day.isoformat()]
        hist.append({'date': day.isoformat(), 'key': key,
                     'name': entry[0]})
        state['history']  = hist
        state['shown_on'] = day.isoformat()
        state['reroll']   = int(reroll)
        self._save_rotation_state(state)

    def _maybe_show_daily_kolam(self):
        """Called once at launch: propose a page if today hasn't been proposed
        yet. Silent when the notebook is empty — nothing to rotate through —
        and silent for the rest of the day once it has been shown."""
        today = datetime.date.today()
        if self._load_rotation_state().get('shown_on') == today.isoformat():
            return
        if not self._daily_pages():
            return
        self.show_daily_kolam_popup(resume_designs=True)

    def _launch_daily_kolam(self):
        """Features popup > Kolam of the Day."""
        self._close_features_popup()
        self.root.after(10, self.show_daily_kolam_popup)

    def show_daily_kolam_popup(self, reroll=None, resume_designs=False):
        # The launch-time design chooser holds a modal grab, so a card merely
        # stacked on top of it would look right and swallow every click. Close
        # it, and put it back if the morning's page is waved off.
        if resume_designs or self._design_options_popup is not None:
            resume_designs = (resume_designs
                              or self._design_options_popup is not None)
            self._close_design_options_popup()
        # Close any previous card first — it clears the resume flag, so the
        # flag for this one is set afterwards.
        self._close_daily_popup(resume=False)
        self._daily_resume = bool(resume_designs)
        today = datetime.date.today()
        if reroll is None:
            state  = self._load_rotation_state()
            reroll = (int(state.get('reroll', 0))
                      if state.get('shown_on') == today.isoformat() else 0)

        entry, occasion, note = self._pick_daily_page(today, reroll)
        if entry is None:
            self.show_hint_popup("No notebook pages yet — digitize a page "
                                 "first (Features › Kolam Notebook)")
            if self._daily_resume:
                self._daily_resume = False
                self._open_design_options_popup()
            return
        name, _full, data = entry
        self._record_daily_pick(today, entry, reroll)

        self.root.update_idletasks()
        W, H = S(430), S(560)
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = self.root.winfo_screenheight() // 2 - H // 2

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        # Opaque, like the design chooser: alpha fades on overrideredirect
        # windows make hit-testing flaky and clicks start missing.
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._daily_popup = popup

        accent = ACCENT_AMBER if occasion else ACCENT_CYAN
        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0, takefocus=0)
        glass.place(x=0, y=0, width=W, height=H)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(22),
                                fill=BG_CARD, outline=accent, width=2)
        glass.create_text(24, 28, text="Kolam of the Day", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", FS(15), "bold"))
        glass.create_text(24, 52, text=today.strftime("%A, %d %B %Y"),
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(13), "bold"), cursor="hand2")
        close_lbl.place(x=W-38, y=16)
        close_lbl.bind("<Button-1>", lambda e: self._close_daily_popup())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=20, y=76, width=W-40, height=H-96)
        body.lift()

        if occasion:
            tag = tk.Label(body, text=f"  {occasion}  ", bg=accent,
                           fg="#1a1a1a", font=("Segoe UI", FS(10), "bold"))
            tag.pack(anchor="w", pady=(0, S(6)))

        nb = data.get('notebook') or {}
        book = str(nb.get('book') or "Kolam Notebook")
        page = self._nb_page_no(data)
        tk.Label(body, text=f"{book} — Page {page}" if page else name,
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold")).pack(anchor="w")
        if note:
            tk.Label(body, text=note, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(9)), wraplength=W-70,
                     justify="left").pack(anchor="w", pady=(S(2), 0))

        thumb = tk.Canvas(body, width=S(240), height=S(240), bg=CANVAS_BG,
                          highlightthickness=0)
        thumb.pack(pady=(S(10), S(6)))
        all_paths = [p for entry_s in data.get('shapes', [])
                     for p in entry_s.get('paths', [])]
        self._draw_flat_paths_thumbnail(thumb, all_paths, S(120), S(120), S(96))

        n_strokes = len(all_paths)
        n_dots    = len(nb.get('dots') or [])
        tk.Label(body,
                 text=f"{n_strokes} stroke(s) · {n_dots} pulli · "
                      f"traced {nb.get('captured', 'earlier')}",
                 bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(9))).pack(anchor="w")

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", pady=(S(10), 0))
        # _small_btn packs right-to-left, so the last one added sits leftmost.
        self._small_btn(btns, "Not today", self._close_daily_popup,
                        BG_INPUT, self._lighten(BG_INPUT, 20))
        self._small_btn(btns, "Show another",
                        lambda: self.show_daily_kolam_popup(
                            reroll + 1, resume_designs=self._daily_resume),
                        ACCENT_PURP, self._lighten(ACCENT_PURP, -30))
        if self._saved_guide_dots(data):
            self._small_btn(btns, "✋ Pulli grid",
                            lambda d=data: self._daily_pulli(d),
                            ACCENT_CYAN, self._lighten(ACCENT_CYAN, -30))
        self._small_btn(btns, "Draw this",
                        lambda d=data: self._daily_place(d),
                        ACCENT_GREEN, self._lighten(ACCENT_GREEN, -30))

        self.log_to_console(
            f"Kolam of the Day ({today.isoformat()}"
            f"{', ' + occasion if occasion else ''}): '{name}'.", "info")

        popup.lift()
        popup.focus_force()
        try:
            popup.grab_set()
        except tk.TclError:
            pass
        # Lay the card out fully before the first click can land on it.
        popup.update_idletasks()
        body.lift()

    def _daily_place(self, data):
        # The user has chosen today's design — don't put the chooser back.
        self._close_daily_popup(resume=False)
        self._place_saved_design(data)
        self.show_hint_popup("Today's page is on the canvas")

    def _daily_pulli(self, data):
        self._close_daily_popup(resume=False)
        self._pulli_from_saved(data)

    def _close_daily_popup(self, resume=True):
        popup = self._daily_popup
        self._daily_popup = None
        resume = bool(resume and self._daily_resume)
        self._daily_resume = False
        if popup is None:
            return
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        if resume:
            # Waved off at launch — hand the morning back to the chooser it
            # was covering, so the app never lands on a dead canvas.
            self.root.after(10, self._open_design_options_popup)

    def show_gallery_popup(self):
        self._close_gallery_popup()
        self.root.update_idletasks()

        W, H = S(860), S(620)
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
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(24),
                                fill=BG_CARD, outline=ACCENT_BLUE, width=2)
        glass.create_text(28, 30, text="Rangoli Gallery", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", FS(16), "bold"))
        glass.create_text(28, 54, text="Click a design to place it. "
                          "\U0001f4e4 on a notebook page sends it to the "
                          "family; \U0001f48c opens one they sent you.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", FS(9)))

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(14), "bold"), cursor="hand2")
        close_lbl.place(x=W-44, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._close_gallery_popup())

        # Taking a page *in* belongs where the pages are, not only at the
        # bottom of the Features list — that is the first place anyone looks
        # for "where did the kolam they sent me go".
        recv_btn = self._color_button(
            popup, "\U0001f4e5  Receive a kolam",
            lambda: (self._close_gallery_popup(),
                     self.root.after(10, self._open_family_share_popup)),
            ACCENT_PINK, width=S(190), height=S(32), font_size=FS(11),
            corner_radius=S(10))
        recv_btn.place(x=W - S(250), y=S(18))

        grid_outer = tk.Frame(popup, bg=BG_CARD)
        grid_top = 90
        grid_outer.place(x=26, y=grid_top, width=W-52, height=H-grid_top-24)

        library = self._load_predesigned_dxf_library()
        saved_all = self._load_saved_designs()
        # Digitized notebook pages carry a 'notebook' block; they get their own
        # section so a 40-page puthagam doesn't bury the hand-saved designs.
        notebook = [t for t in saved_all if isinstance(t[2].get('notebook'), dict)]
        saved    = [t for t in saved_all if not isinstance(t[2].get('notebook'), dict)]

        # Scrollable card area so the My Designs section always fits.
        scroll_cv = tk.Canvas(grid_outer, bg=BG_CARD, highlightthickness=0)
        scroll_sb = tk.Scrollbar(grid_outer, orient="vertical",
                                 command=scroll_cv.yview)
        inner = tk.Frame(scroll_cv, bg=BG_CARD)
        scroll_cv.configure(yscrollcommand=scroll_sb.set)
        scroll_sb.pack(side="right", fill="y")
        scroll_cv.pack(side="left", fill="both", expand=True)
        scroll_cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: scroll_cv.configure(
            scrollregion=scroll_cv.bbox("all")))
        popup.bind_all("<MouseWheel>", lambda e: scroll_cv.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        cols = 3
        row = 0

        def _make_card(r, c, name, draw_thumb, on_click, on_delete=None,
                       on_pulli=None, on_share=None, on_open=None):
            card = tk.Frame(inner, bg=BG_INPUT, cursor="hand2")
            card.grid(row=r, column=c, padx=S(8), pady=S(8), sticky="n")
            thumb = tk.Canvas(card, width=S(140), height=S(140), bg=CANVAS_BG,
                              highlightthickness=0)
            thumb.pack(padx=S(6), pady=(S(6), S(2)))
            draw_thumb(thumb)
            tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(10), "bold")).pack(pady=(S(0), S(8)))
            for widget in [card, thumb] + list(card.winfo_children()):
                widget.bind("<Button-1>", lambda e: on_click())
            if on_delete is not None:
                del_lbl = tk.Label(card, text="✕", bg=BG_INPUT, fg=TEXT_DIM,
                                   font=("Segoe UI", FS(10), "bold"),
                                   cursor="hand2")
                del_lbl.place(relx=1.0, y=2, x=-6, anchor="ne")
                del_lbl.bind("<Button-1>", lambda e: (on_delete(), "break")[1])
            if on_pulli is not None:
                # Re-open a digitized page as a bare dot grid to draw over.
                pul_lbl = tk.Label(card, text="✋", bg=BG_INPUT,
                                   fg=ACCENT_CYAN,
                                   font=("Segoe UI", FS(10), "bold"),
                                   cursor="hand2")
                pul_lbl.place(x=6, y=2, anchor="nw")
                pul_lbl.bind("<Button-1>", lambda e: (on_pulli(), "break")[1])
            if on_share is not None:
                # Send this page to the family.
                shr_lbl = tk.Label(card, text="📤", bg=BG_INPUT,
                                   fg=ACCENT_GREEN,
                                   font=("Segoe UI", FS(10), "bold"),
                                   cursor="hand2")
                shr_lbl.place(x=S(26), y=2, anchor="nw")
                shr_lbl.bind("<Button-1>", lambda e: (on_share(), "break")[1])
            if on_open is not None:
                # A page somebody sent: open what came with it.
                opn_lbl = tk.Label(card, text="💌", bg=BG_INPUT,
                                   fg=ACCENT_PINK,
                                   font=("Segoe UI", FS(10), "bold"),
                                   cursor="hand2")
                opn_lbl.place(x=S(46), y=2, anchor="nw")
                opn_lbl.bind("<Button-1>", lambda e: (on_open(), "break")[1])

        def _section_header(r, text, colour):
            tk.Label(inner, text=text, bg=BG_CARD, fg=colour,
                     font=("Segoe UI", FS(13), "bold")
                     ).grid(row=r, column=0, columnspan=cols, sticky="w",
                            padx=S(8), pady=(S(14), S(2)))
            return r + 1

        def _saved_cards(r, entries, label_of, pulli=False, share=False):
            """Lay out saved-design cards from row r; returns the next free row."""
            for idx, (name, full, data) in enumerate(entries):
                dr, c = divmod(idx, cols)
                all_paths = [p for entry in data['shapes']
                             for p in entry.get('paths', [])]
                _make_card(
                    r + dr, c, label_of(name, data),
                    lambda t, ap=all_paths:
                        self._draw_flat_paths_thumbnail(t, ap, 70, 70, 56),
                    lambda d=data: self._place_saved_design(d),
                    on_delete=lambda nm=name, fp=full:
                        self._delete_saved_design(nm, fp),
                    on_pulli=((lambda d=data: self._pulli_from_saved(d))
                              if pulli and self._saved_guide_dots(data)
                              else None),
                    on_share=((lambda d=data: self._share_from_gallery(d))
                              if share else None),
                    on_open=((lambda d=data: self._open_received_from_gallery(d))
                             if share and self._share_block(data) else None))
            return r + (len(entries) + cols - 1) // cols

        if not library and not saved and not notebook:
            tk.Label(inner, text="No designs found. Place funnel.dxf and "
                     "image.dxf in your Downloads folder, or save your own "
                     "with the \U0001f4be Save button.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(12), "bold"),
                     wraplength=W-100, justify="center").grid(
                row=0, column=0, columnspan=cols, pady=S(60))
        else:
            for idx, (name, raw_paths) in enumerate(library.items()):
                r, c = divmod(idx, cols)
                _make_card(
                    r, c, name,
                    lambda t, rp=raw_paths:
                        self._draw_dxf_thumbnail(t, rp, 70, 70, 56),
                    lambda nm=name, rp=raw_paths:
                        self._choose_dxf_design(nm, rp))
            row = (len(library) + cols - 1) // cols

            if saved:
                row = _section_header(row, "★ My Designs", ACCENT_PURP)
                row = _saved_cards(row, saved, lambda nm, _d: nm)

            if notebook:
                # One sub-section per book, pages in order. A page a relative
                # sent is filed under *their* name, not merged into this
                # household's book of the same title — whose kolam it is is
                # the most useful thing about it.
                books = {}
                for name, full, data in notebook:
                    nb = data['notebook']
                    who = self._share_block(data).get('from')
                    title = str(nb.get('book') or "Notebook")
                    key = (f"\U0001f48c {who}'s notebook — {title}" if who
                           else f"\U0001f4d6 Notebook — {title}")
                    books.setdefault(key, []).append((name, full, data))
                for book in sorted(books):
                    pages = books[book]
                    pages.sort(key=lambda t: self._nb_page_no(t[2]))
                    row = _section_header(
                        row, book,
                        ACCENT_PINK if book.startswith("\U0001f48c")
                        else ACCENT_AMBER)
                    row = _saved_cards(
                        row, pages,
                        lambda nm, d: (f"Page {self._nb_page_no(d)}"
                                       if self._nb_page_no(d) else nm),
                        pulli=True, share=True)

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        popup.grab_set()

    def _share_from_gallery(self, data):
        """📤 on a notebook page — close the gallery, open the send dialog."""
        self._close_gallery_popup()
        self.root.after(10, lambda: self._open_share_popup(data))

    def _open_received_from_gallery(self, data):
        """💌 on a received page — the note, photo and voice note again."""
        self._close_gallery_popup()
        self.root.after(
            10, lambda: self._show_received_page(
                data, self._share_block(data).get("page_id", "")))

    def _choose_dxf_design(self, name, raw_paths):
        """Place a pre-designed DXF straight onto the canvas — no edit step."""
        self._close_gallery_popup()
        self._finalize_dxf_import(PREDESIGNED_DXF.get(name, name), raw_paths)

    def _close_gallery_popup(self):
        popup = self._gallery_popup
        if popup is None:
            return
        try: self.root.unbind_all("<MouseWheel>")
        except Exception: pass
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

    # ── Family Sharing: the notebook that lives in two houses ────────────────
    # The digitized puthagam used to sit in one house. This section lets a page
    # travel to the grandchild's copy of the app and lets the finished rangoli
    # come back, carried by whatever the family already uses — WhatsApp, email,
    # a USB stick. Two transports, deliberately:
    #
    #   * a .kolam file — JSON, full detail, and it can carry a photo of the
    #     page and a voice note in her own voice;
    #   * a QR code — the design alone, small enough to hold a phone up to a
    #     laptop screen when sending a file is the hard part.
    #
    # Nothing here talks to a server. That is not a limitation to apologise
    # for: no account can lapse, no service can be discontinued, and the pages
    # stay the family's own.

    def _load_share_sender(self):
        """Who this copy of the app signs pages as. Remembered so a
        grandmother types her name once, not once per page."""
        try:
            with open(SHARE_SENDER_FILE, encoding="utf-8") as fh:
                self._share_name = str(json.load(fh).get("name", "") or "")[:40]
        except FileNotFoundError:
            self._share_name = ""
        except (OSError, ValueError, AttributeError) as e:
            self._share_name = ""
            self.log_to_console(f"Family Sharing: could not read the saved "
                                f"sender name — {e}", "err")

    def _save_share_sender(self, name):
        self._share_name = (name or "").strip()[:40]
        try:
            with open(SHARE_SENDER_FILE, "w", encoding="utf-8") as fh:
                json.dump({"name": self._share_name}, fh)
        except OSError as e:
            self.log_to_console(f"Family Sharing: could not save the sender "
                                f"name — {e}", "err")

    @staticmethod
    def _share_dirs():
        for d in (SHARE_DIR, SHARE_INBOX_DIR, SHARE_REPLY_DIR, SHARE_MEDIA_DIR):
            os.makedirs(d, exist_ok=True)

    @staticmethod
    def _share_safe(text, fallback="kolam"):
        """A filename fragment safe on Windows, kept short."""
        out = "".join(ch for ch in str(text or "")
                      if ch.isalnum() or ch in " _-").strip()
        return (out or fallback)[:40]

    # ── QR building ──────────────────────────────────────────────────────────
    @staticmethod
    def _share_qr_render(matrix, px_per_module=SHARE_QR_PX_PER_MODULE):
        """QR matrix → a greyscale image with the standard 4-module quiet zone.

        The single place a code is turned into pixels, so that what gets
        verified below is pixel-for-pixel what gets shown and saved. An
        earlier version verified one rendering and displayed another, and
        happily offered codes the display size could not actually resolve.
        """
        import cv2
        import numpy as np
        quiet = 4
        padded = cv2.copyMakeBorder(matrix.astype(np.uint8), quiet, quiet,
                                    quiet, quiet, cv2.BORDER_CONSTANT,
                                    value=255)
        return cv2.resize(padded, None, fx=px_per_module, fy=px_per_module,
                          interpolation=cv2.INTER_NEAREST)

    def _share_qr_readable(self, matrix, text):
        """Is this code one a real camera can read?

        Two separate problems make this necessary. OpenCV's QR decoder cannot
        read every code OpenCV's QR encoder produces — version 23 comes out
        unreadable in this build at any size. And a dense code that decodes
        perfectly from a clean bitmap can still fail through a lens. So the
        code is decoded back from its own rendering, from that rendering
        blurred, and from a stand-in for a camera frame: shrunk to 60%,
        softened and given sensor noise. That last one is the honest test,
        because the app's own scanner is a USB webcam pointed at somebody
        else's screen, not a bitmap.

        A code that fails any of these is never offered — offering it would be
        promising a scan that will not happen.
        """
        import cv2
        import numpy as np
        detector = cv2.QRCodeDetector()
        img = self._share_qr_render(matrix)
        small = cv2.GaussianBlur(
            cv2.resize(img, None, fx=0.6, fy=0.6,
                       interpolation=cv2.INTER_AREA), (3, 3), 0)
        noise = np.random.RandomState(0).randint(-12, 12, small.shape)
        camera = np.clip(small.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        for candidate in (img, cv2.GaussianBlur(img, (5, 5), 0), camera):
            try:
                if detector.detectAndDecode(candidate)[0] != text:
                    return False
            except cv2.error:
                return False
        return True

    def _share_qr_matrix(self, text):
        """Encode ``text`` as a QR that has been proven readable.

        Padding the payload with trailing spaces (harmless — they are stripped
        on import) pushes the encoder onto a different version, which is how a
        payload that lands on a bad one gets past it.

        Returns ``(matrix, ec_label, padded_text)``, or ``None`` if nothing in
        the budget survived _share_qr_readable.
        """
        import cv2
        # L first: for a given payload it yields the fewest modules, and a
        # sparser code beats a denser one with more error correction when the
        # limit is the camera rather than the printing.
        levels = (("L", cv2.QRCodeEncoder_CORRECT_LEVEL_L),
                  ("M", cv2.QRCodeEncoder_CORRECT_LEVEL_M),
                  ("Q", cv2.QRCodeEncoder_CORRECT_LEVEL_Q))
        for pad in (0, 4, 8, 12):
            padded = text + " " * pad
            for label, level in levels:
                params = cv2.QRCodeEncoder_Params()
                params.correction_level = level
                try:
                    matrix = cv2.QRCodeEncoder_create(params).encode(padded)
                except cv2.error:
                    break             # too big for L ⇒ too big for M and Q
                if matrix is None or not len(matrix):
                    continue
                if matrix.shape[0] > SHARE_QR_MAX_MODULES:
                    # Encoding a dense code costs about a second, so prune
                    # rather than measure: more error correction and more
                    # padding both only ever make the code bigger. If the
                    # cheapest combination is already too dense, so is every
                    # remaining one, and the caller should simplify instead.
                    if label == "L" and pad == 0:
                        return None
                    break
                if self._share_qr_readable(matrix, padded):
                    return matrix, label, padded
        return None

    def _share_prepare_qr(self, data, sender, note):
        """Build the smallest honest QR for ``data``.

        Full detail is tried first. A page too detailed for a code that scans
        is simplified a step at a time, and the result records exactly how
        many points survived, so the UI can say so rather than quietly
        shipping a rougher kolam than the one she drew. Runs off the UI
        thread — encoding a dense code takes about a second and the search may
        try several.
        """
        strokes = _share_design_points(data)
        full = sum(len(p) for p, _ in strokes)
        for tol in SHARE_SIMPLIFY_STEPS:
            try:
                blob = _share_encode(data, sender=sender, note=note, tol=tol)
            except ValueError:
                return None
            text = SHARE_MAGIC + _b45_encode(blob)
            if len(text) > SHARE_QR_MAX_CHARS:
                continue              # too big to be worth the encoder's time
            built = self._share_qr_matrix(text)
            if built is None:
                continue
            matrix, ec, padded = built
            kept = full if tol <= 0 else sum(
                len(_share_simplify(p, tol)) for p, _ in strokes)
            return {"matrix": matrix, "text": padded, "ec": ec, "tol": tol,
                    "bytes": len(blob), "modules": int(matrix.shape[0]),
                    "points": kept, "full_points": full}
        return None

    def _share_qr_image(self, matrix, px_per_module=SHARE_QR_PX_PER_MODULE):
        """The QR as a PIL image — at the verified size unless told otherwise."""
        from PIL import Image
        return Image.fromarray(
            self._share_qr_render(matrix, px_per_module)).convert("RGB")

    # ── Attachments ──────────────────────────────────────────────────────────
    @staticmethod
    def _share_photo_bytes(path):
        """Re-encode a photo small enough to live inside a share file.

        A 4MB phone snap base64s into something nobody wants to send; at
        SHARE_PHOTO_MAX_DIM it is still clearly a photograph of a kolam.
        """
        import cv2
        import numpy as np
        # np.fromfile + imdecode, not imread: imread silently returns None for
        # a path with non-ASCII characters in it on Windows, and a family that
        # names its folders in Tamil would hit exactly that.
        try:
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                               cv2.IMREAD_COLOR)
        except OSError as e:
            raise ValueError(f"that file could not be read — {e}") from None
        if img is None:
            raise ValueError("that file could not be read as an image")
        h, w = img.shape[:2]
        longest = max(h, w)
        if longest > SHARE_PHOTO_MAX_DIM:
            f = SHARE_PHOTO_MAX_DIM / float(longest)
            img = cv2.resize(img, (max(1, int(w * f)), max(1, int(h * f))),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img,
                               [cv2.IMWRITE_JPEG_QUALITY, SHARE_PHOTO_QUALITY])
        if not ok:
            raise ValueError("that image could not be re-encoded")
        return buf.tobytes()

    @staticmethod
    def _share_human_size(n):
        return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"

    def _share_play_audio(self, path):
        """Hand a voice note to whatever the machine already plays audio with.
        WhatsApp notes arrive as .opus/.m4a, which no bundled Python audio
        module reads — the OS default player does."""
        if not path or not os.path.isfile(path):
            self.show_hint_popup("That voice note is missing")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)                      # Windows
            else:
                import subprocess
                subprocess.Popen(
                    ["open" if sys.platform == "darwin" else "xdg-open", path])
        except Exception as e:
            self.log_to_console(f"Family Sharing: could not play {path} — {e}",
                                "err")
            self.show_hint_popup("Could not open the voice note")

    # ── Bundle: the .kolam file ──────────────────────────────────────────────
    def _share_bundle(self, data, sender, note, photo=None, voice=None,
                      compact=None):
        """Assemble the dict written out as a .kolam file.

        The design travels twice: once as plain JSON (exactly the shape My
        Designs already uses, so an older copy of the app could still read the
        page out of it by hand) and once as the compact string, so the same
        file can be turned back into a QR on the far side without re-deriving
        anything.
        """
        nb = data.get('notebook') or {}
        bundle = {
            "kolam_share": 1,
            "kind": "page",
            "page_id": _share_page_id(nb.get('book'), nb.get('page'), sender),
            "from": sender,
            "sent": time.strftime("%Y-%m-%d %H:%M"),
            "app": "Rangoli-Bot",
            "design": data,
            "note": note or "",
        }
        if compact:
            bundle["compact"] = compact
        if photo:
            bundle["photo"] = {
                "name": os.path.basename(photo.get("name") or "page.jpg"),
                "data": base64.b64encode(photo["bytes"]).decode("ascii"),
            }
        if voice:
            bundle["voice"] = {
                "name": os.path.basename(voice.get("name") or "voice"),
                "data": base64.b64encode(voice["bytes"]).decode("ascii"),
            }
        return bundle

    @staticmethod
    def _share_parse_bundle(raw):
        """Validate anything claiming to be a share file. Raises ValueError
        with something a person can act on."""
        if not isinstance(raw, dict):
            return None
        if not raw.get("kolam_share"):
            raise ValueError("this file is not a shared kolam")
        try:
            version = int(raw.get("kolam_share"))
        except (TypeError, ValueError):
            raise ValueError("this file is not a shared kolam") from None
        if version > 1:
            raise ValueError(
                "this page was sent by a newer version of the app — update "
                "this copy to open it")
        kind = raw.get("kind") or "page"
        if kind not in ("page", "reply"):
            raise ValueError(f"unknown share kind {kind!r}")
        design = raw.get("design")
        if kind == "page":
            if not isinstance(design, dict) or not design.get("shapes"):
                # A page whose compact string survived but whose JSON did not
                # is still recoverable — the caller re-derives it below.
                if not raw.get("compact"):
                    raise ValueError("this share file has no kolam in it")
        return raw

    def _share_write_media(self, blob_b64, name, prefix):
        """Drop an embedded photo/voice note next to the app and return its
        path, so the receiving UI has a real file to show and play."""
        self._share_dirs()
        raw = base64.b64decode(blob_b64, validate=False)
        stem, ext = os.path.splitext(os.path.basename(name or ""))
        ext = ext if 0 < len(ext) <= 6 else ".bin"
        out = os.path.join(SHARE_MEDIA_DIR,
                           f"{prefix}_{self._share_safe(stem, 'media')}{ext}")
        n = 1
        while os.path.exists(out):
            out = os.path.join(
                SHARE_MEDIA_DIR,
                f"{prefix}_{self._share_safe(stem, 'media')}_{n}{ext}")
            n += 1
        with open(out, "wb") as fh:
            fh.write(raw)
        return out

    # ── Sending a page ───────────────────────────────────────────────────────
    def _close_share_popup(self):
        popup = self._share_popup
        self._share_popup = None
        if popup is None:
            return
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self.root.focus_force()

    def _share_shell(self, W, H, title, subtitle, outline=ACCENT_CYAN):
        """Popup shell in the house style; returns (popup, body frame)."""
        self._close_share_popup()
        self.root.update_idletasks()
        H = min(H, self.root.winfo_screenheight() - S(60))
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = max(S(10), self.root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._share_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(22),
                                fill=BG_CARD, outline=outline, width=2)
        glass.create_text(28, 30, text=title, anchor="w", fill=TEXT_PRIMARY,
                          font=("Segoe UI", FS(16), "bold"))
        top = 60
        if subtitle:
            glass.create_text(28, 56, text=subtitle, anchor="w", fill=TEXT_DIM,
                              font=("Segoe UI", FS(9)), width=W-90)
            top = 84

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(14), "bold"), cursor="hand2")
        close_lbl.place(x=W-42, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._close_share_popup())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=26, y=top, width=W-52, height=H-top-22)
        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        return popup, body

    def _open_share_popup(self, data):
        """Compose what goes to the grandchild: who it is from, a note, an
        optional photo of the page and an optional voice note."""
        nb = data.get('notebook') or {}
        W, H = S(660), S(700)
        page_no = self._nb_page_no(data)
        title = data.get('name') or "Kolam page"
        popup, body = self._share_shell(
            W, H, "Send this page to the family",
            f"“{title}” goes as one file you can send on WhatsApp, or as a QR "
            f"code they can scan off this screen. Nothing is uploaded "
            f"anywhere — you are the one who sends it.")

        state = {"data": data, "photo": None, "voice": None,
                 "book": nb.get('book') or "Kolam Notebook", "page": page_no}

        # Preview + facts
        head = tk.Frame(body, bg=BG_CARD)
        head.pack(fill="x", pady=(0, S(10)))
        thumb = tk.Canvas(head, width=S(120), height=S(120), bg=CANVAS_BG,
                          highlightthickness=0)
        thumb.pack(side="left")
        all_paths = [p for e in data.get('shapes', [])
                     for p in e.get('paths', [])]
        self._draw_flat_paths_thumbnail(thumb, all_paths, S(60), S(60), S(48))
        facts = tk.Frame(head, bg=BG_CARD)
        facts.pack(side="left", fill="both", expand=True, padx=(S(14), 0))
        n_strokes = len([p for p in all_paths if len(p) >= 2])
        n_dots = len(self._saved_guide_dots(data))
        tk.Label(facts, text=title, bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(13), "bold"), anchor="w",
                 wraplength=W-S(200), justify="left").pack(anchor="w")
        tk.Label(facts,
                 text=f"{n_strokes} stroke{'' if n_strokes == 1 else 's'}"
                      + (f"  ·  {n_dots} pulli" if n_dots else "")
                      + (f"  ·  captured {nb.get('captured')}"
                         if nb.get('captured') else ""),
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 anchor="w").pack(anchor="w", pady=(S(4), 0))

        # From
        tk.Label(body, text="From", bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(10), "bold")).pack(anchor="w",
                                                         pady=(S(6), S(2)))
        from_var = tk.StringVar(value=self._share_name or "")
        from_entry = ctk.CTkEntry(body, textvariable=from_var,
                                  placeholder_text="Ajji",
                                  height=S(34), font=("Segoe UI", FS(11)))
        from_entry.pack(fill="x")

        # Note
        tk.Label(body, text="A note to send with it (optional)", bg=BG_CARD,
                 fg=TEXT_DIM, font=("Segoe UI", FS(10), "bold")).pack(
                     anchor="w", pady=(S(10), S(2)))
        note_box = tk.Text(body, height=3, bg=BG_INPUT, fg=TEXT_PRIMARY,
                           insertbackground=TEXT_PRIMARY, relief="flat",
                           font=("Segoe UI", FS(10)), wrap="word",
                           highlightthickness=1, highlightbackground=GLASS_EDGE)
        note_box.pack(fill="x")

        # Attachments
        att = tk.Frame(body, bg=BG_CARD)
        att.pack(fill="x", pady=(S(12), 0))
        photo_lbl = tk.Label(att, text="No photo attached", bg=BG_CARD,
                             fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                             anchor="w", wraplength=W-S(230), justify="left")
        voice_lbl = tk.Label(att, text="No voice note attached", bg=BG_CARD,
                             fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                             anchor="w", wraplength=W-S(230), justify="left")

        prow = tk.Frame(att, bg=BG_CARD); prow.pack(fill="x", pady=(0, S(6)))
        self._color_button(prow, "📷  Attach a photo",
                           lambda: self._share_pick_photo(state, photo_lbl),
                           ACCENT_PURP, width=S(180), height=S(32),
                           font_size=FS(10)).pack(side="left")
        photo_lbl.pack(in_=prow, side="left", padx=(S(10), 0))

        vrow = tk.Frame(att, bg=BG_CARD); vrow.pack(fill="x")
        self._color_button(vrow, "🎙  Attach a voice note",
                           lambda: self._share_pick_voice(state, voice_lbl),
                           ACCENT_PINK, width=S(180), height=S(32),
                           font_size=FS(10)).pack(side="left")
        voice_lbl.pack(in_=vrow, side="left", padx=(S(10), 0))

        tk.Label(body,
                 text="Record the voice note on your phone the way you always "
                      "do, then attach the file — it travels inside the same "
                      "share file.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(8)),
                 wraplength=W-S(70), justify="left").pack(anchor="w",
                                                          pady=(S(6), 0))

        status = tk.Label(body, text="", bg=BG_CARD, fg=ACCENT_CYAN,
                          font=("Segoe UI", FS(9), "bold"),
                          wraplength=W-S(70), justify="left")
        status.pack(anchor="w", pady=(S(8), 0))
        state["status"] = status

        def _collect():
            state["sender"] = from_var.get().strip()
            state["note"] = note_box.get("1.0", "end").strip()
            self._save_share_sender(state["sender"])
            return state

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(side="bottom", fill="x", pady=(S(10), 0))
        self._color_button(btns, "💾  Save share file…",
                           lambda: self._share_save_file(_collect()),
                           ACCENT_GREEN, width=S(200), height=S(40),
                           font_size=FS(12)).pack(side="left")
        self._color_button(btns, "🔳  Show QR code",
                           lambda: self._share_start_qr(_collect()),
                           ACCENT_BLUE, width=S(180), height=S(40),
                           font_size=FS(12)).pack(side="left", padx=(S(10), 0))
        popup.grab_set()

    def _share_pick_photo(self, state, label):
        path = filedialog.askopenfilename(
            title="Attach a photo",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            blob = self._share_photo_bytes(path)
        except Exception as e:
            label.configure(text=f"Could not attach that photo — {e}",
                            fg=ACCENT_PINK)
            self.log_to_console(f"Family Sharing: photo rejected — {e}", "err")
            return
        state["photo"] = {"name": os.path.basename(path), "bytes": blob}
        label.configure(
            text=f"✓ {os.path.basename(path)} ({self._share_human_size(len(blob))})",
            fg=ACCENT_GREEN)

    def _share_pick_voice(self, state, label):
        path = filedialog.askopenfilename(
            title="Attach a voice note",
            filetypes=[("Audio files",
                        " ".join("*" + e for e in SHARE_AUDIO_EXTS)),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            size = os.path.getsize(path)
            if size > SHARE_VOICE_MAX_BYTES:
                label.configure(
                    text=f"That voice note is {self._share_human_size(size)} — "
                         f"keep it under "
                         f"{self._share_human_size(SHARE_VOICE_MAX_BYTES)}",
                    fg=ACCENT_PINK)
                return
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as e:
            label.configure(text=f"Could not read that file — {e}",
                            fg=ACCENT_PINK)
            return
        state["voice"] = {"name": os.path.basename(path), "bytes": blob}
        label.configure(
            text=f"✓ {os.path.basename(path)} ({self._share_human_size(len(blob))})",
            fg=ACCENT_GREEN)

    def _share_save_file(self, state):
        """Write the .kolam file the family actually sends."""
        sender = state.get("sender") or ""
        if not sender:
            state["status"].configure(
                text="Put your name in the From box first — the grandchild's "
                     "app shows who a page came from.", fg=ACCENT_PINK)
            return
        data = state["data"]
        try:
            blob = _share_encode(data, sender=sender, note=state.get("note", ""))
            compact = SHARE_MAGIC + _b45_encode(blob)
        except ValueError as e:
            state["status"].configure(text=str(e), fg=ACCENT_PINK)
            return
        bundle = self._share_bundle(data, sender, state.get("note", ""),
                                    photo=state.get("photo"),
                                    voice=state.get("voice"), compact=compact)
        default = (f"{self._share_safe(state['book'], 'kolam')}"
                   f"_p{int(state['page'] or 0):03d}{SHARE_EXT}")
        out = filedialog.asksaveasfilename(
            title="Save the page to send", initialfile=default,
            defaultextension=SHARE_EXT,
            filetypes=[("Kolam share file", "*" + SHARE_EXT),
                       ("JSON", "*.json")])
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, ensure_ascii=False)
            size = os.path.getsize(out)
        except OSError as e:
            state["status"].configure(text=f"Could not save — {e}",
                                      fg=ACCENT_PINK)
            self.log_to_console(f"Family Sharing: save failed — {e}", "err")
            return
        extras = []
        if state.get("photo"): extras.append("photo")
        if state.get("voice"): extras.append("voice note")
        state["status"].configure(
            text=f"✓ Saved {os.path.basename(out)} "
                 f"({self._share_human_size(size)}"
                 + (" including the " + " and ".join(extras) if extras else "")
                 + "). Send it to them however you normally would.",
            fg=ACCENT_GREEN)
        self.log_to_console(
            f"Family Sharing: wrote {out} ({self._share_human_size(size)}) "
            f"from '{sender}'.", "recv")

    def _share_start_qr(self, state):
        """Kick off the QR build on a worker thread — encoding a dense code
        takes about a second and the search may try several."""
        sender = state.get("sender") or ""
        if not sender:
            state["status"].configure(
                text="Put your name in the From box first.", fg=ACCENT_PINK)
            return
        state["status"].configure(text="Building the QR code…", fg=ACCENT_AMBER)
        data, note = state["data"], state.get("note", "")
        page = {"book": state["book"], "page": state["page"],
                "name": data.get("name") or "Kolam page"}

        def worker():
            try:
                built = self._share_prepare_qr(data, sender, note)
                err = None
            except Exception as e:                       # noqa: BLE001
                built, err = None, e
            self.root.after(
                0, lambda: self._share_qr_ready(state, built, err, page, sender))

        threading.Thread(target=worker, daemon=True).start()

    def _share_qr_ready(self, state, built, err, page, sender):
        status = state.get("status")
        try:
            alive = status is not None and status.winfo_exists()
        except tk.TclError:
            alive = False
        if err is not None:
            self.log_to_console(f"Family Sharing: QR build failed — {err}",
                                "err")
            if alive:
                status.configure(text=f"Could not build a QR code — {err}",
                                 fg=ACCENT_PINK)
            return
        if built is None:
            # Honest failure: this page is genuinely too detailed for one code.
            if alive:
                status.configure(
                    text="This page has too much detail to fit in a QR code "
                         "that scans reliably. Send it as a share file "
                         "instead — that keeps every stroke.",
                    fg=ACCENT_AMBER)
            self.log_to_console(
                "Family Sharing: no QR in the size budget for this page — "
                "offered the file instead.", "info")
            return
        if alive:
            status.configure(text="✓ QR ready", fg=ACCENT_GREEN)
        self._show_share_qr(built, page, sender)

    def _show_share_qr(self, built, page, sender):
        """Put the QR on screen at a size a phone can actually read, and say
        plainly whether it carries the page in full."""
        from PIL import ImageTk
        img = self._share_qr_image(built["matrix"])       # full size, for saving

        # The code was proven readable at SHARE_QR_PX_PER_MODULE. Show it at
        # exactly that size whenever the screen has room, because shrinking it
        # spends the very margin the check was measuring.
        #
        # Where the screen is too short, re-render at a smaller *whole* number
        # of pixels per module rather than rescaling the big image: resampling
        # 8px modules down to 4.55px makes some modules wider than others,
        # which is precisely what a decoder cannot cope with. Fewer pixels per
        # module still scans from a phone held close; it is the webcam-across-
        # the-desk case that needs the full size, so that is what the note
        # below tells the user.
        box = max(S(280), self.root.winfo_screenheight() - S(360))
        per = max(3, min(SHARE_QR_PX_PER_MODULE,
                         box // (built["modules"] + 8)))
        full = per >= SHARE_QR_PX_PER_MODULE
        shown = (img if full else
                 self._share_qr_image(built["matrix"], px_per_module=per))
        W = max(S(560), shown.width + S(90))
        H = shown.height + S(340)
        popup, body = self._share_shell(
            W, H, "Scan this on the other phone or laptop",
            f"“{page['name']}” from {sender}. Open the family's copy of the "
            f"app, choose Receive a kolam ▸ Scan a QR code, and hold this up.",
            outline=ACCENT_BLUE)

        photo = ImageTk.PhotoImage(shown)
        holder = tk.Label(body, image=photo, bg="#ffffff", bd=0)
        holder.image = photo            # keep a reference or Tk drops it
        holder.pack(pady=(S(4), S(10)))

        dropped = built["full_points"] - built["points"]
        if built["tol"] > 0 and dropped > 0:
            detail = (f"⚠ Simplified to fit: {built['points']} of "
                      f"{built['full_points']} points "
                      f"({dropped} smoothed away). The shape is hers; the "
                      f"finest wobbles are not. Send the share file instead "
                      f"if you want every point.")
            colour = ACCENT_AMBER
        else:
            detail = (f"✓ Full detail — all {built['full_points']} points of "
                      f"the page, exactly as digitized.")
            colour = ACCENT_GREEN
        tk.Label(body, text=detail, bg=BG_CARD, fg=colour,
                 font=("Segoe UI", FS(10), "bold"), wraplength=W-S(70),
                 justify="left").pack(anchor="w")
        if not full:
            tk.Label(body,
                     text="⚠ This screen is too short to show the code at full "
                          "size. Scan it with a phone held close and it reads "
                          "fine; a webcam further back needs the full-size "
                          "copy, so save the PNG and send that.",
                     bg=BG_CARD, fg=ACCENT_AMBER,
                     font=("Segoe UI", FS(9), "bold"), wraplength=W-S(70),
                     justify="left").pack(anchor="w", pady=(S(6), 0))
        tk.Label(body,
                 text=f"QR version {(built['modules'] - 17) // 4}, error "
                      f"correction {built['ec']}, {built['bytes']} bytes. "
                      f"Before being shown, this code was decoded back from "
                      f"its own image — sharp, blurred, and shrunk to a "
                      f"camera-sized frame — so it is known to scan.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(8)),
                 wraplength=W-S(70), justify="left").pack(anchor="w",
                                                          pady=(S(6), 0))
        saved = tk.Label(body, text="", bg=BG_CARD, fg=ACCENT_GREEN,
                         font=("Segoe UI", FS(9), "bold"),
                         wraplength=W-S(70), justify="left")
        saved.pack(anchor="w", pady=(S(6), 0))

        row = tk.Frame(body, bg=BG_CARD)
        row.pack(side="bottom", fill="x", pady=(S(8), 0))
        self._color_button(
            row, "🖼  Save QR as PNG…",
            lambda: self._share_save_qr_png(img, page, saved),
            ACCENT_GREEN, width=S(200), height=S(38),
            font_size=FS(11)).pack(side="left")
        self._color_button(
            row, "📋  Copy the text",
            lambda: self._share_copy_text(built["text"], saved),
            ACCENT_PURP, width=S(170), height=S(38),
            font_size=FS(11)).pack(side="left", padx=(S(10), 0))
        popup.grab_set()

    def _share_save_qr_png(self, img, page, label):
        default = (f"{self._share_safe(page['book'], 'kolam')}"
                   f"_p{int(page['page'] or 0):03d}_qr.png")
        out = filedialog.asksaveasfilename(
            title="Save the QR code", initialfile=default,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")])
        if not out:
            return
        try:
            img.save(out)
        except (OSError, ValueError) as e:
            label.configure(text=f"Could not save — {e}", fg=ACCENT_PINK)
            return
        label.configure(text=f"✓ Saved {os.path.basename(out)} — send it like "
                             f"any other picture.", fg=ACCENT_GREEN)
        self.log_to_console(f"Family Sharing: QR saved to {out}", "recv")

    def _share_copy_text(self, text, label):
        """The compact string on the clipboard, for pasting into a chat when
        even a picture is awkward."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
        except tk.TclError as e:
            label.configure(text=f"Could not copy — {e}", fg=ACCENT_PINK)
            return
        label.configure(
            text=f"✓ {len(text)} characters copied — paste it into the chat; "
                 f"they choose Receive a kolam ▸ Paste a kolam code.",
            fg=ACCENT_GREEN)

    # ── Receiving a page ─────────────────────────────────────────────────────
    def _launch_family_share(self):
        """Features popup ▸ Family Sharing."""
        self._close_features_popup()
        self.root.after(10, self._open_family_share_popup)

    def _open_family_share_popup(self):
        """The hub: take a page in, or look at what has come back."""
        W, H = S(620), S(600)
        replies = self._share_reply_records()
        received = self._share_received_records()
        popup, body = self._share_shell(
            W, H, "Family Sharing",
            "Pages travel between houses as a file or a QR code. To send one, "
            "open the Gallery and press 📤 on a notebook page.")

        def _row(title, blurb, btn, cmd, colour):
            fr = tk.Frame(body, bg=BG_CARD)
            fr.pack(fill="x", pady=(0, S(4)))
            tk.Label(fr, text=title, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(12), "bold")).pack(side="left")
            self._color_button(fr, btn, cmd, colour, width=S(150),
                               height=S(32), font_size=FS(10)).pack(side="right")
            tk.Label(body, text=blurb, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(9)), wraplength=W-S(70),
                     justify="left").pack(anchor="w", pady=(0, S(12)))

        _row("Open a shared file",
             "A .kolam file sent on WhatsApp or email, or copied off a USB "
             "stick. This is the one that carries the photo and the voice "
             "note as well.",
             "📂  Open file…", self._share_import_file, ACCENT_GREEN)
        _row("Open a QR picture",
             "The QR code as a picture — the PNG they saved, a screenshot, or "
             "a photo of it on their screen. This is the one to use when the "
             "code arrived on your phone rather than on a screen in front of "
             "you.",
             "🖼  Open picture…", self._share_import_qr_image, ACCENT_AMBER)
        _row("Scan with the camera",
             "Point the installed camera at the QR code on their screen or on "
             "a printout. Carries the kolam itself — no photo or voice note.",
             "📷  Scan…", self._share_open_scanner, ACCENT_BLUE)
        _row("Paste a kolam code",
             "The KOLAM1:… text from a chat message, when sending a file is "
             "the awkward part.",
             "📋  Paste…", self._share_import_paste, ACCENT_PURP)

        tk.Frame(body, bg=GLASS_EDGE, height=1).pack(fill="x", pady=(S(4), S(12)))

        tk.Label(body,
                 text=f"{len(received)} page{'' if len(received) == 1 else 's'} "
                      f"received  ·  "
                      f"{len(replies)} rangoli sent back to you",
                 bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(11), "bold")).pack(anchor="w")
        tk.Label(body,
                 text="Received pages sit in the 📖 Notebook section of the "
                      "Gallery alongside your own, and can be learned "
                      "step by step in Learn Mode.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                 wraplength=W-S(70), justify="left").pack(anchor="w",
                                                          pady=(S(4), S(10)))

        row = tk.Frame(body, bg=BG_CARD)
        row.pack(side="bottom", fill="x")
        if replies:
            self._color_button(
                row, f"🎉  See what they drew ({len(replies)})",
                self._open_share_replies, ACCENT_PINK, width=S(250),
                height=S(40), font_size=FS(12)).pack(side="left")
        self._color_button(row, "Close", self._close_share_popup, ACCENT_PURP,
                           width=S(120), height=S(40),
                           font_size=FS(12)).pack(side="right")
        popup.grab_set()

    # ── the three ways in ────────────────────────────────────────────────────
    def _share_import_file(self):
        path = filedialog.askopenfilename(
            title="Open a shared kolam",
            filetypes=[("Kolam share file", "*" + SHARE_EXT + " *.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as e:
            messagebox.showerror("Family Sharing",
                                 f"That file could not be read:\n\n{e}")
            return
        self._share_accept(raw, origin=os.path.basename(path))

    def _share_import_qr_image(self):
        """Read a QR out of a picture file.

        The likeliest way a code actually arrives: she saves the PNG, sends it
        on WhatsApp, and it lands in the grandchild's Downloads. There is no
        screen to point a camera at in that story, so pointing a camera cannot
        be the only way in.
        """
        path = filedialog.askopenfilename(
            title="Open a picture of a QR code",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        import cv2
        import numpy as np
        try:
            img = cv2.imdecode(np.fromfile(path, dtype=np.uint8),
                               cv2.IMREAD_GRAYSCALE)
        except OSError as e:
            messagebox.showerror("Family Sharing",
                                 f"That picture could not be read:\n\n{e}")
            return
        if img is None:
            messagebox.showerror("Family Sharing",
                                 "That file could not be read as a picture.")
            return

        text = ""
        # A photo of a screen is often too small, too big, skewed or unevenly
        # lit for a first pass, so give the detectors a few versions of the
        # same image before concluding there is no code in it.
        for prepared in self._share_qr_candidates(img):
            found = self._share_read_qr(prepared)
            if found and found.strip().startswith(SHARE_MAGIC):
                text = found
                break
            text = text or found
        if not text:
            messagebox.showerror(
                "Family Sharing",
                "No QR code could be read in that picture.\n\nTry a sharper "
                "or larger copy — the PNG saved by the sending app works "
                "best. If the code will not read at all, ask them to send "
                "the share file instead.")
            return
        self._share_accept_compact(text, origin=os.path.basename(path))

    def _share_read_qr(self, img):
        """Decode a QR out of one image, trying both detectors.

        The plain QRCodeDetector is faster and handles a clean, square-on code
        well. The Aruco-based one is markedly better on a code photographed at
        an angle off a lit screen — a case the plain detector cannot even find
        the corners of — which is exactly how a QR reaches somebody who was
        sent a picture of it. It is not in every OpenCV build, so its absence
        is not an error.
        """
        import cv2
        for make in (getattr(cv2, "QRCodeDetector", None),
                     getattr(cv2, "QRCodeDetectorAruco", None)):
            if make is None:
                continue
            try:
                found = make().detectAndDecode(img)[0]
            except (cv2.error, AttributeError, TypeError):
                continue
            if found:
                return found
        return ""

    @staticmethod
    def _share_qr_candidates(gray):
        """The same picture a few ways, cheapest first."""
        import cv2
        yield gray
        h, w = gray.shape[:2]
        longest = max(h, w)
        if longest < 900:             # a small screenshot of a dense code
            f = 900.0 / longest
            yield cv2.resize(gray, None, fx=f, fy=f,
                             interpolation=cv2.INTER_CUBIC)
        if longest > 1800:            # a full-resolution phone photo
            f = 1800.0 / longest
            yield cv2.resize(gray, None, fx=f, fy=f,
                             interpolation=cv2.INTER_AREA)
        # A photo of a lit screen: uneven brightness across the frame.
        yield cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 31, 5)
        yield cv2.threshold(gray, 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

    def _share_import_paste(self):
        dlg = ctk.CTkInputDialog(
            text="Paste the kolam code (it starts with KOLAM1:)",
            title="Paste a kolam code")
        text = (dlg.get_input() or "").strip()
        if not text:
            return
        self._share_accept_compact(text, origin="pasted code")

    def _share_accept_compact(self, text, origin):
        """A KOLAM1:… string from a QR or a chat message."""
        text = (text or "").strip()
        if not text.startswith(SHARE_MAGIC):
            messagebox.showerror(
                "Family Sharing",
                "That does not look like a kolam code — it should start "
                f"with {SHARE_MAGIC}")
            return
        try:
            # Trailing spaces are the encoder's version padding, not content.
            blob = _b45_decode(text[len(SHARE_MAGIC):].strip())
            design, meta = _share_decode(blob)
        except ValueError as e:
            messagebox.showerror("Family Sharing", f"That code is not usable:\n\n{e}")
            return
        bundle = self._share_bundle(design, meta["sender"], meta["note"],
                                    compact=text)
        bundle["simplified"] = meta["simplified"]
        self._share_accept(bundle, origin=origin)

    def _share_accept(self, raw, origin):
        """Validate a bundle and file it — pages into the notebook, replies
        into the replies list."""
        try:
            bundle = self._share_parse_bundle(raw)
        except ValueError as e:
            messagebox.showerror("Family Sharing", str(e))
            return
        if bundle is None:
            messagebox.showerror("Family Sharing",
                                 "That file is not a shared kolam.")
            return
        try:
            if (bundle.get("kind") or "page") == "reply":
                self._share_accept_reply(bundle, origin)
            else:
                self._share_accept_page(bundle, origin)
        except (OSError, ValueError) as e:
            messagebox.showerror("Family Sharing",
                                 f"That page could not be saved:\n\n{e}")
            self.log_to_console(f"Family Sharing: import failed — {e}", "err")

    def _share_accept_page(self, bundle, origin):
        design = bundle.get("design")
        if not isinstance(design, dict) or not design.get("shapes"):
            # Fall back to the compact string when the JSON half is unusable.
            compact = (bundle.get("compact") or "").strip()
            if not compact.startswith(SHARE_MAGIC):
                raise ValueError("this share file has no kolam in it")
            design, _meta = _share_decode(
                _b45_decode(compact[len(SHARE_MAGIC):].strip()))

        sender = str(bundle.get("from") or "").strip() or "Someone"
        nb = design.setdefault('notebook', {})
        book = str(nb.get('book') or "Shared kolam")
        page = nb.get('page') or 0
        page_id = bundle.get("page_id") or _share_page_id(book, page, sender)

        self._share_dirs()
        photo_path = voice_path = ""
        if isinstance(bundle.get("photo"), dict):
            photo_path = self._share_write_media(
                bundle["photo"].get("data", ""),
                bundle["photo"].get("name", "page.jpg"), page_id)
        if isinstance(bundle.get("voice"), dict):
            voice_path = self._share_write_media(
                bundle["voice"].get("data", ""),
                bundle["voice"].get("name", "voice"), page_id)

        # The share block is what makes a received page different from one of
        # this household's own: it records who drew it and what they said.
        nb['share'] = {
            "from": sender,
            "note": str(bundle.get("note") or ""),
            "sent": str(bundle.get("sent") or ""),
            "received": time.strftime("%Y-%m-%d %H:%M"),
            "page_id": page_id,
            "photo": photo_path,
            "voice": voice_path,
            "simplified": bool(bundle.get("simplified")),
            "origin": origin,
        }
        design['name'] = f"{book} — Page {page}" if page else book

        os.makedirs(MY_DESIGNS_DIR, exist_ok=True)
        fname = (f"shared_{self._share_safe(sender, 'family')}_"
                 f"{self._share_safe(book, 'kolam')}_p{int(page or 0):03d}.json")
        out = os.path.join(MY_DESIGNS_DIR, fname)
        existed = os.path.exists(out)
        with open(out, "w", encoding="utf-8") as fh:
            # Escaped ASCII deliberately, not ensure_ascii=False: this file is
            # read back by the gallery, and a book named in Tamil written as
            # raw UTF-8 is unreadable to a reader that opens it in the Windows
            # default code page. \uXXXX escapes survive either way.
            json.dump(design, fh)
        with open(os.path.join(SHARE_INBOX_DIR, page_id + SHARE_EXT), "w",
                  encoding="utf-8") as fh:
            json.dump(bundle, fh, ensure_ascii=False)

        n = len([p for e in design['shapes'] for p in e.get('paths', [])
                 if len(p) >= 2])
        self.log_to_console(
            f"Family Sharing: received '{design['name']}' from {sender} "
            f"({n} stroke(s), via {origin}) → {out}"
            + (" [replaced the earlier copy]" if existed else ""), "recv")
        self._show_received_page(design, page_id)

    def _share_accept_reply(self, bundle, origin):
        """A finished rangoli coming back from the grandchild."""
        self._share_dirs()
        page_id = bundle.get("page_id") or ""
        who = str(bundle.get("from") or "").strip() or "Someone"
        photo_path = ""
        if isinstance(bundle.get("photo"), dict):
            photo_path = self._share_write_media(
                bundle["photo"].get("data", ""),
                bundle["photo"].get("name", "rangoli.jpg"),
                f"reply_{page_id or 'x'}")
        bundle["photo_path"] = photo_path
        bundle["received"] = time.strftime("%Y-%m-%d %H:%M")
        bundle["origin"] = origin
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out = os.path.join(SHARE_REPLY_DIR,
                           f"{page_id or 'reply'}_{stamp}{SHARE_EXT}")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, ensure_ascii=False)
        self.log_to_console(
            f"Family Sharing: {who} sent back a finished rangoli "
            f"('{bundle.get('page_name') or 'a page'}') via {origin} → {out}",
            "recv")
        self._show_one_reply(bundle)

    # ── QR scanning with the installed camera ────────────────────────────────
    def _share_open_scanner(self):
        """Live view from the installed camera, decoding QR codes as they
        come into frame."""
        if self._camera_index is None:
            messagebox.showinfo(
                "Family Sharing",
                "No camera is installed yet.\n\nOpen Learn Mode ▸ camera step "
                "to scan for one and install it, then come back — or use "
                "Open file / Paste a kolam code instead.")
            return
        cap, _frame = self._open_camera_device(self._camera_index)
        if cap is None:
            messagebox.showerror(
                "Family Sharing",
                "The installed camera didn't respond — check the USB cable.")
            return
        self._share_scan_cap = cap

        W, H = S(620), S(600)
        popup, body = self._share_shell(
            W, H, "Scan their QR code",
            "Hold the code steady in the frame. It is read the moment it is "
            "sharp enough — nothing to press.", outline=ACCENT_BLUE)
        view = tk.Label(body, bg="#000000", bd=0)
        view.pack(fill="both", expand=True)
        status = tk.Label(body, text="Looking for a code…", bg=BG_CARD,
                          fg=ACCENT_AMBER, font=("Segoe UI", FS(10), "bold"),
                          wraplength=W-S(70), justify="left")
        status.pack(anchor="w", pady=(S(8), 0))
        self._color_button(body, "Stop", self._share_close_scanner,
                           ACCENT_PINK, width=S(120), height=S(36),
                           font_size=FS(11)).pack(side="bottom", anchor="e",
                                                  pady=(S(8), 0))
        popup.grab_set()
        self._share_scan_tick(view, status)

    def _share_scan_tick(self, view, status):
        import cv2
        from PIL import Image, ImageTk
        cap = self._share_scan_cap
        if cap is None:
            return
        try:
            if not view.winfo_exists():
                self._share_close_scanner()
                return
        except tk.TclError:
            self._share_close_scanner()
            return
        ok, frame = cap.read()
        if ok and frame is not None:
            # Same two-detector read as the picture importer: a webcam looking
            # at somebody else's screen is never quite square-on to it.
            text = self._share_read_qr(frame)
            if text and text.strip().startswith(SHARE_MAGIC):
                status.configure(text="✓ Got it — reading the kolam…",
                                 fg=ACCENT_GREEN)
                self._share_close_scanner()
                self._share_accept_compact(text, origin="QR scan")
                return
            if text:
                status.configure(
                    text="That is a QR code, but not a kolam code.",
                    fg=ACCENT_AMBER)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img.thumbnail((view.winfo_width() or S(400),
                           view.winfo_height() or S(300)))
            photo = ImageTk.PhotoImage(img)
            view.configure(image=photo)
            view.image = photo
        self._share_scan_after = self.root.after(
            60, lambda: self._share_scan_tick(view, status))

    def _share_close_scanner(self):
        if self._share_scan_after is not None:
            try: self.root.after_cancel(self._share_scan_after)
            except Exception: pass
            self._share_scan_after = None
        cap, self._share_scan_cap = self._share_scan_cap, None
        if cap is not None:
            try: cap.release()
            except Exception: pass
        self._close_share_popup()

    # ── What came in ─────────────────────────────────────────────────────────
    @staticmethod
    def _share_block(data):
        """The 'somebody sent this' block of a saved design, or {}."""
        nb = data.get('notebook')
        if not isinstance(nb, dict):
            return {}
        sh = nb.get('share')
        return sh if isinstance(sh, dict) else {}

    def _share_received_records(self):
        """Saved designs that arrived from someone else."""
        return [(name, full, data) for name, full, data
                in self._load_saved_designs() if self._share_block(data)]

    def _share_reply_records(self):
        """Finished rangoli sent back to this house, newest first."""
        out = []
        if not os.path.isdir(SHARE_REPLY_DIR):
            return out
        for fn in sorted(os.listdir(SHARE_REPLY_DIR), reverse=True):
            if not fn.lower().endswith(SHARE_EXT):
                continue
            try:
                with open(os.path.join(SHARE_REPLY_DIR, fn),
                          encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict) and raw.get("kind") == "reply":
                    out.append(raw)
            except (OSError, ValueError) as e:
                self.log_to_console(
                    f"Family Sharing: could not read reply {fn} — {e}", "err")
        return out

    def _share_photo_thumb(self, parent, path, box):
        """Show an attached photo, or say why it can't be shown."""
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            img.thumbnail((box, box))
            photo = ImageTk.PhotoImage(img)
            lbl = tk.Label(parent, image=photo, bg=BG_CARD, bd=0)
            lbl.image = photo
            return lbl
        except Exception as e:                            # noqa: BLE001
            self.log_to_console(
                f"Family Sharing: could not show {path} — {e}", "err")
            return tk.Label(parent, text="(photo could not be opened)",
                            bg=BG_CARD, fg=TEXT_DIM,
                            font=("Segoe UI", FS(9)))

    def _show_received_page(self, design, page_id):
        """The card shown the moment a page arrives — and again from the
        Gallery. Everything she sent with it is here in one place."""
        share = self._share_block(design)
        sender = share.get("from") or "Someone"
        W, H = S(640), S(700)
        popup, body = self._share_shell(
            W, H, f"{sender} sent you a kolam",
            f"“{design.get('name', 'A page')}”"
            + (f"  ·  sent {share['sent']}" if share.get("sent") else ""),
            outline=ACCENT_PINK)

        top = tk.Frame(body, bg=BG_CARD)
        top.pack(fill="x")
        thumb = tk.Canvas(top, width=S(150), height=S(150), bg=CANVAS_BG,
                          highlightthickness=0)
        thumb.pack(side="left")
        paths = [p for e in design.get('shapes', []) for p in e.get('paths', [])]
        self._draw_flat_paths_thumbnail(thumb, paths, S(75), S(75), S(60))
        if share.get("photo") and os.path.isfile(share["photo"]):
            self._share_photo_thumb(top, share["photo"], S(150)).pack(
                side="left", padx=(S(12), 0))

        if share.get("note"):
            tk.Label(body, text=f"“{share['note']}”", bg=BG_CARD,
                     fg=TEXT_PRIMARY, font=("Segoe UI", FS(12), "italic"),
                     wraplength=W-S(70), justify="left").pack(
                         anchor="w", pady=(S(14), S(4)))

        n = len([p for p in paths if len(p) >= 2])
        dots = len(self._saved_guide_dots(design))
        tk.Label(body,
                 text=f"{n} stroke{'' if n == 1 else 's'}"
                      + (f"  ·  {dots} pulli" if dots else "")
                      + (f"  ·  arrived by {share.get('origin')}"
                         if share.get("origin") else ""),
                 bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(9))).pack(anchor="w", pady=(S(6), 0))
        if share.get("simplified"):
            # A QR-carried page may have been smoothed to fit. Say so — the
            # child should know this is her kolam, not every last wobble of it.
            tk.Label(body,
                     text="This came by QR code, so the finest detail was "
                          "smoothed away to make it fit. Ask them for the "
                          "share file if you want every stroke exactly.",
                     bg=BG_CARD, fg=ACCENT_AMBER, font=("Segoe UI", FS(9)),
                     wraplength=W-S(70), justify="left").pack(anchor="w",
                                                              pady=(S(6), 0))

        if share.get("voice") and os.path.isfile(share["voice"]):
            self._color_button(
                body, "🔊  Play their voice note",
                lambda: self._share_play_audio(share["voice"]),
                ACCENT_AMBER, width=S(230), height=S(38),
                font_size=FS(11)).pack(anchor="w", pady=(S(12), 0))

        row = tk.Frame(body, bg=BG_CARD)
        row.pack(side="bottom", fill="x", pady=(S(10), 0))
        self._color_button(
            row, "🎓  Learn this one",
            lambda: self._learn_shared_page(design),
            ACCENT_GREEN, width=S(190), height=S(42),
            font_size=FS(12)).pack(side="left")
        self._color_button(
            row, "Place on the canvas",
            lambda: (self._close_share_popup(),
                     self._place_saved_design(design)),
            ACCENT_BLUE, width=S(190), height=S(42),
            font_size=FS(12)).pack(side="left", padx=(S(10), 0))
        self._color_button(row, "Close", self._close_share_popup, ACCENT_PURP,
                           width=S(100), height=S(42),
                           font_size=FS(12)).pack(side="right")
        popup.grab_set()

    def _open_share_replies(self):
        """Everything the grandchildren have sent back."""
        replies = self._share_reply_records()
        W, H = S(700), S(640)
        popup, body = self._share_shell(
            W, H, "What they drew",
            "Rangoli finished from your pages and sent back.",
            outline=ACCENT_PINK)
        if not replies:
            tk.Label(body, text="Nothing has come back yet.", bg=BG_CARD,
                     fg=TEXT_DIM, font=("Segoe UI", FS(12), "bold")).pack(
                         pady=S(50))
            popup.grab_set()
            return

        scroll = tk.Canvas(body, bg=BG_CARD, highlightthickness=0)
        bar = tk.Scrollbar(body, orient="vertical", command=scroll.yview)
        inner = tk.Frame(scroll, bg=BG_CARD)
        scroll.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        scroll.pack(side="left", fill="both", expand=True)
        scroll.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: scroll.configure(scrollregion=scroll.bbox("all")))
        popup.bind_all("<MouseWheel>", lambda e: scroll.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        for rep in replies:
            card = tk.Frame(inner, bg=BG_INPUT, cursor="hand2")
            card.pack(fill="x", pady=(0, S(8)), padx=S(2))
            path = rep.get("photo_path") or ""
            if path and os.path.isfile(path):
                self._share_photo_thumb(card, path, S(110)).pack(
                    side="left", padx=S(8), pady=S(8))
            info = tk.Frame(card, bg=BG_INPUT)
            info.pack(side="left", fill="both", expand=True, padx=(S(4), S(8)),
                      pady=S(8))
            who = rep.get("from") or "Someone"
            tk.Label(info, text=f"{who} — {rep.get('page_name') or 'your page'}",
                     bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(12), "bold"), anchor="w",
                     wraplength=W-S(220), justify="left").pack(anchor="w")
            # The score is shown only when a real photo was judged. A sample
            # verdict is practice, not a mark, and must never read as one.
            if rep.get("scored_by") == "ai" and rep.get("score") is not None:
                tk.Label(info,
                         text=f"AI scored it {rep['score']}/"
                              f"{rep.get('out_of', 10)}",
                         bg=BG_INPUT, fg=ACCENT_GREEN,
                         font=("Segoe UI", FS(10), "bold")).pack(anchor="w")
            elif rep.get("score") is not None:
                tk.Label(info, text="Practice — not scored by the AI",
                         bg=BG_INPUT, fg=TEXT_DIM,
                         font=("Segoe UI", FS(9))).pack(anchor="w")
            if rep.get("note"):
                tk.Label(info, text=f"“{rep['note']}”", bg=BG_INPUT,
                         fg=TEXT_PRIMARY, font=("Segoe UI", FS(10), "italic"),
                         wraplength=W-S(220), justify="left",
                         anchor="w").pack(anchor="w", pady=(S(4), 0))
            tk.Label(info,
                     text=(f"sent {rep.get('sent', '')}"
                           + (f"  ·  received {rep['received']}"
                              if rep.get("received") else "")),
                     bg=BG_INPUT, fg=TEXT_DIM,
                     font=("Segoe UI", FS(8))).pack(anchor="w", pady=(S(4), 0))
        popup.grab_set()

    def _show_one_reply(self, rep):
        """The card shown the moment a reply is imported."""
        who = rep.get("from") or "Someone"
        W, H = S(600), S(620)
        popup, body = self._share_shell(
            W, H, f"{who} drew your kolam! 🎉",
            f"“{rep.get('page_name') or 'Your page'}”"
            + (f"  ·  sent {rep['sent']}" if rep.get("sent") else ""),
            outline=ACCENT_PINK)
        path = rep.get("photo_path") or ""
        if path and os.path.isfile(path):
            self._share_photo_thumb(body, path, S(340)).pack(pady=(S(4), S(10)))
        else:
            tk.Label(body, text="(no photo came with this one)", bg=BG_CARD,
                     fg=TEXT_DIM, font=("Segoe UI", FS(10))).pack(pady=S(20))
        if rep.get("note"):
            tk.Label(body, text=f"“{rep['note']}”", bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(12), "italic"), wraplength=W-S(70),
                     justify="left").pack(anchor="w", pady=(0, S(6)))
        if rep.get("scored_by") == "ai" and rep.get("score") is not None:
            tk.Label(body,
                     text=f"The AI scored it {rep['score']}/"
                          f"{rep.get('out_of', 10)}.",
                     bg=BG_CARD, fg=ACCENT_GREEN,
                     font=("Segoe UI", FS(11), "bold")).pack(anchor="w")
        elif rep.get("score") is not None:
            tk.Label(body,
                     text="No photo was scored by the AI — this is practice "
                          "they logged, not a mark.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                     wraplength=W-S(70), justify="left").pack(anchor="w")
        self._color_button(body, "Close", self._close_share_popup, ACCENT_PURP,
                           width=S(120), height=S(40),
                           font_size=FS(12)).pack(side="bottom", anchor="e",
                                                  pady=(S(10), 0))
        popup.grab_set()

    # ── Sending the finished rangoli back ────────────────────────────────────
    def _share_compose_reply(self, verdict, scored_by):
        """The return leg: the child's finished rangoli goes back to whoever
        sent the page.

        The mark travels with it, but tagged with how it was arrived at. A
        sample verdict is the app's placeholder, not a judgement of anything
        the child drew, and it must never reach a grandmother looking like
        one — so ``scored_by`` rides along and the receiving card reads it.
        """
        src = self._learn_share_src or {}
        who = src.get("sender") or "them"
        photo = self._learn_photo_path
        W, H = S(620), S(660)
        popup, body = self._share_shell(
            W, H, f"Send your rangoli back to {who}",
            f"“{self._learn_design}” — the page {who} sent you. This makes one "
            f"file to send back the same way it arrived.", outline=ACCENT_PINK)

        state = {"photo": None}
        if photo and os.path.isfile(photo):
            self._share_photo_thumb(body, photo, S(230)).pack(pady=(0, S(8)))
            try:
                state["photo"] = {"name": os.path.basename(photo),
                                  "bytes": self._share_photo_bytes(photo)}
            except Exception as e:                        # noqa: BLE001
                self.log_to_console(
                    f"Family Sharing: could not attach the rangoli photo — {e}",
                    "err")
        if state["photo"] is None:
            tk.Label(body,
                     text="No photo of your rangoli was taken, so this will go "
                          "back as just a message. Take one at the camera step "
                          "if you want them to see it.",
                     bg=BG_CARD, fg=ACCENT_AMBER, font=("Segoe UI", FS(10)),
                     wraplength=W-S(70), justify="left").pack(anchor="w",
                                                              pady=(0, S(8)))

        tk.Label(body, text="From", bg=BG_CARD, fg=TEXT_DIM,
                 font=("Segoe UI", FS(10), "bold")).pack(anchor="w")
        from_var = tk.StringVar(value=self._share_name or "")
        ctk.CTkEntry(body, textvariable=from_var, placeholder_text="Your name",
                     height=S(34), font=("Segoe UI", FS(11))).pack(fill="x")

        tk.Label(body, text="Say something back (optional)", bg=BG_CARD,
                 fg=TEXT_DIM, font=("Segoe UI", FS(10), "bold")).pack(
                     anchor="w", pady=(S(10), S(2)))
        note_box = tk.Text(body, height=3, bg=BG_INPUT, fg=TEXT_PRIMARY,
                           insertbackground=TEXT_PRIMARY, relief="flat",
                           font=("Segoe UI", FS(10)), wrap="word",
                           highlightthickness=1, highlightbackground=GLASS_EDGE)
        note_box.pack(fill="x")

        if scored_by == "ai":
            score_line = (f"The AI scored it {verdict.get('score')}/"
                          f"{verdict.get('out_of', 10)} — that goes with it.")
            colour = ACCENT_GREEN
        else:
            score_line = ("No photo was scored by the AI, so this goes back "
                          "marked as practice rather than as a score.")
            colour = TEXT_DIM
        tk.Label(body, text=score_line, bg=BG_CARD, fg=colour,
                 font=("Segoe UI", FS(9), "bold"), wraplength=W-S(70),
                 justify="left").pack(anchor="w", pady=(S(8), 0))

        status = tk.Label(body, text="", bg=BG_CARD, fg=ACCENT_GREEN,
                          font=("Segoe UI", FS(9), "bold"),
                          wraplength=W-S(70), justify="left")
        status.pack(anchor="w", pady=(S(6), 0))

        row = tk.Frame(body, bg=BG_CARD)
        row.pack(side="bottom", fill="x", pady=(S(10), 0))
        self._color_button(
            row, "💾  Save the reply…",
            lambda: self._share_save_reply(
                src, verdict, scored_by, state.get("photo"),
                from_var.get().strip(), note_box.get("1.0", "end").strip(),
                status),
            ACCENT_GREEN, width=S(200), height=S(40),
            font_size=FS(12)).pack(side="left")
        self._color_button(row, "Not now", self._close_share_popup,
                           ACCENT_PURP, width=S(120), height=S(40),
                           font_size=FS(12)).pack(side="right")
        popup.grab_set()

    def _share_save_reply(self, src, verdict, scored_by, photo, sender, note,
                          status):
        if not sender:
            status.configure(text="Put your name in the From box first.",
                             fg=ACCENT_PINK)
            return
        self._save_share_sender(sender)
        bundle = {
            "kolam_share": 1,
            "kind": "reply",
            "page_id": src.get("page_id") or _share_page_id(
                src.get("book"), src.get("page"), src.get("sender")),
            "page_name": self._learn_design or "your page",
            "from": sender,
            "to": src.get("sender") or "",
            "sent": time.strftime("%Y-%m-%d %H:%M"),
            "app": "Rangoli-Bot",
            "note": note or "",
            "score": verdict.get("score"),
            "out_of": verdict.get("out_of", 10),
            # Carried verbatim so the receiving end can tell a real mark from
            # the app's placeholder — see _share_compose_reply.
            "scored_by": scored_by,
            "lesson": self._learn_lesson,
        }
        if photo:
            bundle["photo"] = {
                "name": photo["name"],
                "data": base64.b64encode(photo["bytes"]).decode("ascii"),
            }
        default = (f"reply_{self._share_safe(sender, 'me')}_"
                   f"{time.strftime('%Y%m%d')}{SHARE_EXT}")
        out = filedialog.asksaveasfilename(
            title="Save the reply to send", initialfile=default,
            defaultextension=SHARE_EXT,
            filetypes=[("Kolam share file", "*" + SHARE_EXT),
                       ("JSON", "*.json")])
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, ensure_ascii=False)
            size = os.path.getsize(out)
        except OSError as e:
            status.configure(text=f"Could not save — {e}", fg=ACCENT_PINK)
            self.log_to_console(f"Family Sharing: reply save failed — {e}",
                                "err")
            return
        status.configure(
            text=f"✓ Saved {os.path.basename(out)} "
                 f"({self._share_human_size(size)}). Send it to "
                 f"{src.get('sender') or 'them'} however the page came.",
            fg=ACCENT_GREEN)
        self.log_to_console(
            f"Family Sharing: reply for '{bundle['page_name']}' written to "
            f"{out} ({scored_by}-scored).", "recv")

    # ── Learn Mode ────────────────────────────────────────────────────────────
    def _toggle_learn_mode(self):
        """Features popup: enter or leave the guided rangoli lesson."""
        if self.learn_mode_var.get():
            self._exit_learn_mode()
            return
        self.learn_mode_var.set(True)
        self._close_features_popup()
        self.log_to_console("Learn Mode started.", "info")
        if not self._learn_intro_seen:
            self._show_learn_intro()
        else:
            self._open_learn_gallery()

    def _exit_learn_mode(self):
        self.learn_mode_var.set(False)
        self._stop_learn_video()
        self._close_learn_popup()
        self._close_features_popup()
        self.log_to_console("Learn Mode ended.", "info")

    def _learn_shell(self, W, H, title, subtitle=None, outline=ACCENT_GREEN,
                     mascot=None):
        """Build a standard Learn-Mode popup shell; return (popup, body frame).

        In Kid Mode the same shell comes out as a cream cartoon card with a
        chunky border, the playful font, a peacock mascot with a speech bubble,
        and a skin pass scheduled over whatever the caller builds next. Every
        Learn-Mode screen goes through here, so that is the whole hook — no
        screen needs to know Kid Mode exists.
        """
        self._close_learn_popup()
        self.root.update_idletasks()
        kid = self.kid_mode
        if kid:
            H = min(H + S(74), self.root.winfo_screenheight() - S(40))
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = max(S(10), self.root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._learn_popup = popup

        card_bg = self.k_card()
        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._kid_glass = glass if kid else None
        self._draw_rounded_rect(
            glass, 4, 4, W-4, H-4, radius=S(30) if kid else S(22),
            fill=card_bg, outline=KID_THEME["outline"] if kid else outline,
            width=S(5) if kid else 2)
        # Only pass `width` when we want wrapping — Tk canvas items take a screen
        # distance here and None is not one.
        title_kw = {"width": W - 90} if kid else {}
        glass.create_text(28, 30, text=title, anchor="w", fill=self.k_ink(),
                          font=self.k_font(FS(17) if kid else FS(16), "bold"),
                          **title_kw)
        top = 60
        if subtitle:
            glass.create_text(28, 58 if kid else 56, text=subtitle, anchor="w",
                              fill=self.k_dim(),
                              font=self.k_font(FS(10) if kid else FS(9)),
                              width=W-90)
            top = 88 if kid else 84

        close_lbl = tk.Label(popup, text="✕", bg=card_bg, fg=self.k_dim(),
                             font=self.k_font(FS(14), "bold"), cursor="hand2")
        close_lbl.place(x=W-42, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._exit_learn_mode())

        if kid:
            # The mascot sits between the header and the screen's own content.
            holder = self._kid_build_mascot(popup, mascot or "idle")
            if holder is not None:
                holder.place(x=26, y=top)
                top += S(74)

        body = tk.Frame(popup, bg=card_bg)
        body.place(x=26, y=top, width=W-52, height=H-top-22)

        if kid:
            # The caller builds its widgets after this returns, so the restyle
            # has to wait for the event loop to come back round.
            popup.after(0, lambda: self._kid_skin_tree(popup))

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        return popup, body

    def _show_learn_intro(self):
        """One-time 'how to make a rangoli' video, played silently in a popup.

        Frames are decoded with OpenCV and blitted onto a Tk label, so the
        lesson stays inside the app — and there is never any audio.
        """
        self._learn_intro_seen = True

        # The clip is shot 9:16 (the YouTube Shorts / Reels shape), so height
        # is what runs out first — size off the screen height, then derive the
        # width, and never let the popup chrome push it off-screen.
        vid_h = max(360, min(720, self.root.winfo_screenheight() - 300))
        vid_w = int(round(vid_h * 9 / 16))
        self._learn_video_size = (vid_w, vid_h)

        # A 9:16 frame is narrow, so the popup needs a floor wide enough for the
        # title, the subtitle and the two buttons under the video.
        W = max(420, vid_w + 60)
        H = vid_h + 210
        # Build the shell first: it closes the previous Learn popup, and that
        # teardown stops any capture that is already running.
        popup, body = self._learn_shell(
            W, H, "How to make a Rangoli",
            "Watch this once, then start your first guided design.")

        if self._open_learn_capture() is None:
            # Nothing to play — don't strand the student on a blank popup.
            self.log_to_console(
                "Learn Mode: intro video unavailable, skipping to designs.",
                "err")
            self._open_learn_gallery()
            return

        holder = tk.Frame(body, bg="#000000")
        holder.pack(side="top")
        self._learn_video_label = tk.Label(holder, bg="#000000",
                                           width=vid_w, height=vid_h)
        self._learn_video_label.pack()

        controls = tk.Frame(body, bg=BG_CARD)
        controls.pack(side="bottom", fill="x", pady=(S(12), S(0)))
        self._learn_video_pause_btn = self._color_button(
            controls, "Pause", self._toggle_learn_video_pause,
            "#334155", width=S(110), height=S(44), font_size=FS(12))
        self._learn_video_pause_btn.pack(side="left")
        # Live from the first frame, so it doubles as "skip the video".
        self._color_button(
            controls, "Start Interactive learning →", self._open_learn_gallery,
            ACCENT_GREEN, width=max(160, (W - 52) - 122), height=S(44),
            font_size=FS(13), text_color="#06281c").pack(side="left", padx=(S(12), S(0)))

        self.log_to_console("Learn Mode: playing the intro video (muted).",
                            "info")
        self._learn_video_paused = False
        self._learn_video_tick()

    def _open_learn_capture(self):
        """Open the intro clip, or return None if it cannot be played."""
        self._stop_learn_video()
        if not os.path.isfile(LEARN_VIDEO_FILE):
            return None
        try:
            import cv2
            from PIL import Image, ImageTk       # noqa: F401  (checked here)
        except ImportError as exc:
            self.log_to_console(f"Intro video needs OpenCV/Pillow: {exc}", "err")
            return None
        try:
            cap = cv2.VideoCapture(LEARN_VIDEO_FILE)
        except Exception as exc:
            self.log_to_console(f"Intro video failed to open: {exc}", "err")
            return None
        if not cap.isOpened():
            cap.release()
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        self._learn_video_delay = 1000.0 / fps if fps > 1 else 40.0
        self._learn_video_cap = cap
        return cap

    def _learn_video_tick(self):
        """Decode and show the next frame, then schedule the one after it."""
        self._learn_video_after = None
        cap   = self._learn_video_cap
        label = self._learn_video_label
        if cap is None or label is None:
            return
        try:
            if not label.winfo_exists():
                return
        except tk.TclError:
            return

        import cv2
        from PIL import Image, ImageTk

        start = time.perf_counter()
        ok, frame = cap.read()
        if not ok:                                   # end of clip — loop it
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if not ok:
                self._stop_learn_video()
                return

        vid_w, vid_h = self._learn_video_size
        frame = cv2.resize(frame, (vid_w, vid_h), interpolation=cv2.INTER_AREA)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame)
        if self._learn_video_img is None:
            # One PhotoImage reused for every frame — rebuilding it each time
            # leaks Tk image handles and makes playback stutter.
            self._learn_video_img = ImageTk.PhotoImage(pil_img)
            label.configure(image=self._learn_video_img, width=vid_w,
                            height=vid_h)
            label.image = self._learn_video_img
        else:
            self._learn_video_img.paste(pil_img)

        if self._learn_video_paused:
            return
        spent = (time.perf_counter() - start) * 1000.0
        delay = max(1, int(self._learn_video_delay - spent))
        self._learn_video_after = self.root.after(delay, self._learn_video_tick)

    def _toggle_learn_video_pause(self):
        self._learn_video_paused = not self._learn_video_paused
        btn = self._learn_video_pause_btn
        if btn is not None:
            try:
                btn.configure(text="Play" if self._learn_video_paused
                              else "Pause")
            except tk.TclError:
                self._learn_video_pause_btn = None
        if not self._learn_video_paused and self._learn_video_after is None:
            self._learn_video_tick()

    def _stop_learn_video(self):
        """Halt playback and drop every frame-loop reference."""
        if self._learn_video_after is not None:
            try: self.root.after_cancel(self._learn_video_after)
            except Exception: pass
            self._learn_video_after = None
        cap, self._learn_video_cap = self._learn_video_cap, None
        if cap is not None:
            try: cap.release()
            except Exception: pass
        self._learn_video_label     = None
        self._learn_video_pause_btn = None
        self._learn_video_img       = None
        self._learn_video_paused    = False

    def _open_learn_gallery(self):
        """Gallery of designs to learn — the robot teaches one part at a time."""
        W, H = S(720), S(560)
        meta = self._learn_level_meta()
        scored = len(self._learn_scored_attempts())
        sym = self._learn_symmetry_mode()
        blurb = (meta['blurb'] if self._learn_lesson == "full" else
                 "Watch the design traced as the fewest unbroken lines the "
                 "maths allows, and see where the line has to stop."
                 if self._learn_lesson == "oneline" else
                 f"The robot draws one half, you draw the reflection across "
                 f"{LEARN_SYMMETRY_LABELS[sym]} ({sym}).")
        if self.kid_mode:
            popup, body = self._learn_shell(
                W, H, "Pick a rangoli to make!",
                f"{self._kid_progress_line()}      {blurb}",
                outline=KID_THEME["outline"], mascot="idle")
        else:
            popup, body = self._learn_shell(
                W, H, f"Choose a design to learn  ·  {self._learn_level_label()}",
                f"{blurb}  "
                f"({scored} scored rangoli so far — your level is saved between "
                f"sessions and moves with your AI scores.)", outline=ACCENT_BLUE)

        # Lesson type. Drawing a whole rangoli never isolates symmetry, so it
        # gets its own lesson rather than being folded into the normal walk.
        picker = tk.Frame(body, bg=BG_CARD)
        picker.pack(fill="x", pady=(S(0), S(8)))
        labels = ((("full", "🎨 Make the whole thing"),
                   ("symmetry", "🦋 Mirror game"),
                   ("oneline", "🪄 Magic one-line trick"))
                  if self.kid_mode else
                  (("full", "Whole design"),
                   ("symmetry", "⇄ Symmetry challenge"),
                   ("oneline", "◉ One continuous line")))
        for key, label in labels:
            on = (self._learn_lesson == key)
            b = tk.Label(picker, text=label,
                         bg=ACCENT_CYAN if on else BG_INPUT,
                         fg="#06281c" if on else TEXT_DIM,
                         font=("Segoe UI", FS(10), "bold"),
                         padx=S(14), pady=S(6), cursor="hand2")
            b.pack(side="left", padx=(S(0), S(8)))
            b.bind("<Button-1>", lambda e, k=key: self._set_learn_lesson(k))
        if self.kid_mode:
            sb = tk.Label(picker, text="🏅 My stickers", bg=ACCENT_PINK,
                          fg="#4a1d2f", font=("Segoe UI", FS(10), "bold"),
                          padx=S(14), pady=S(6), cursor="hand2")
            sb.pack(side="right")
            sb.bind("<Button-1>", lambda e: self._open_kid_stickers())

        scroll_cv = tk.Canvas(body, bg=BG_CARD, highlightthickness=0)
        scroll_sb = tk.Scrollbar(body, orient="vertical", command=scroll_cv.yview)
        inner = tk.Frame(scroll_cv, bg=BG_CARD)
        scroll_cv.configure(yscrollcommand=scroll_sb.set)
        scroll_sb.pack(side="right", fill="y")
        scroll_cv.pack(side="left", fill="both", expand=True)
        scroll_cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: scroll_cv.configure(
            scrollregion=scroll_cv.bbox("all")))
        popup.bind_all("<MouseWheel>", lambda e: scroll_cv.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        cols = 3
        for idx, (name, meta) in enumerate(PRESET_DESIGNS.items()):
            r, c = divmod(idx, cols)
            paths = meta['generator'](0, 0, 100)
            card = tk.Frame(inner, bg=BG_INPUT, cursor="hand2")
            card.grid(row=r, column=c, padx=S(8), pady=S(8), sticky="n")
            thumb = tk.Canvas(card, width=S(150), height=S(150), bg=CANVAS_BG,
                              highlightthickness=0)
            thumb.pack(padx=S(6), pady=(S(6), S(2)))
            self._learn_draw_preview(thumb, paths, 75, 75, 62, flip=True)
            tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(10), "bold")).pack()
            diff = meta.get('difficulty', '')
            tk.Label(card, text=diff, bg=BG_INPUT,
                     fg=DIFFICULTY_COLORS.get(diff, TEXT_DIM),
                     font=("Segoe UI", FS(9), "bold")).pack(pady=(S(0), S(8)))
            for w in [card, thumb] + list(card.winfo_children()):
                w.bind("<Button-1>", lambda e, nm=name: self._choose_learn_design(nm))

        # Real pages — the household's own digitized notebook, and pages a
        # relative sent. Learning grandma's actual kolam is the point of the
        # whole notebook feature, so it belongs in the same gallery as the
        # built-in shapes rather than behind another menu.
        row = (len(PRESET_DESIGNS) + cols - 1) // cols
        pages = self._learn_page_entries()
        if pages:
            tk.Label(inner,
                     text=self.kid_pick("📖 Real kolams from your family",
                                        "📖 From the notebook"),
                     bg=BG_CARD, fg=ACCENT_AMBER,
                     font=("Segoe UI", FS(13), "bold")).grid(
                         row=row, column=0, columnspan=cols, sticky="w",
                         padx=S(8), pady=(S(14), S(2)))
            row += 1
            for idx, (name, _full, data) in enumerate(pages):
                r, c = divmod(idx, cols)
                parts = [p for e in data.get('shapes', [])
                         for p in e.get('paths', []) if len(p) >= 2]
                share = self._share_block(data)
                card = tk.Frame(inner, bg=BG_INPUT, cursor="hand2")
                card.grid(row=row + r, column=c, padx=S(8), pady=S(8),
                          sticky="n")
                thumb = tk.Canvas(card, width=S(150), height=S(150),
                                  bg=CANVAS_BG, highlightthickness=0)
                thumb.pack(padx=S(6), pady=(S(6), S(2)))
                # flip=False: a hand-drawn page is not symmetric, so it has to
                # be previewed the way it was photographed.
                self._learn_draw_preview(thumb, parts, 75, 75, 62, flip=False)
                tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                         font=("Segoe UI", FS(10), "bold"),
                         wraplength=S(140)).pack()
                tk.Label(card,
                         text=(f"from {share['from']}" if share.get("from")
                               else f"{len(parts)} parts"),
                         bg=BG_INPUT,
                         fg=ACCENT_PINK if share.get("from") else TEXT_DIM,
                         font=("Segoe UI", FS(9), "bold")).pack(
                             pady=(S(0), S(8)))
                for w in [card, thumb] + list(card.winfo_children()):
                    w.bind("<Button-1>",
                           lambda e, d=data: self._choose_learn_page(d))

    # ── Learning a page out of the notebook, or one the family sent ──────────
    def _learn_page_entries(self):
        """Digitized and received pages that can be taught, received first.

        A page from a relative is the whole point of Family Sharing, so it
        leads; this household's own notebook follows.
        """
        pages = [(name, full, data) for name, full, data
                 in self._load_saved_designs()
                 if isinstance(data.get('notebook'), dict)
                 and [p for e in data.get('shapes', [])
                      for p in e.get('paths', []) if len(p) >= 2]]
        pages.sort(key=lambda t: (0 if self._share_block(t[2]) else 1,
                                  self._nb_page_no(t[2]), t[0]))
        return pages

    @staticmethod
    def _learn_fit_paths(paths):
        """Keep a page inside the drawable area.

        Pages digitized here are already mapped into the canvas, and a page
        from a relative was mapped into a canvas of the same nominal size, so
        this is normally a no-op. It matters for a page that was saved after
        being dragged around the canvas: the robot must not be handed a lesson
        that runs off the mat.
        """
        pts = [pt for p in paths for pt in p]
        if not pts:
            return paths
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = 24
        lo_x, hi_x = MARGIN_L + pad, MARGIN_L + GRAPH_W - pad
        lo_y, hi_y = MARGIN_T + pad, MARGIN_T + GRAPH_H - pad
        if x0 >= lo_x and x1 <= hi_x and y0 >= lo_y and y1 <= hi_y:
            return paths
        scale = min((hi_x - lo_x) / max(x1 - x0, 1e-6),
                    (hi_y - lo_y) / max(y1 - y0, 1e-6), 1.0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        tx, ty = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
        return [[(tx + (x - cx) * scale, ty + (y - cy) * scale) for x, y in p]
                for p in paths]

    def _learn_shared_page(self, data):
        """'Learn this one' from a received-page card."""
        self._close_share_popup()
        self._close_gallery_popup()
        if not self.learn_mode_var.get():
            self.learn_mode_var.set(True)
            self.log_to_console("Learn Mode started from a shared page.", "info")
        self.root.after(10, lambda: self._choose_learn_page(data))

    def _choose_learn_page(self, data):
        """Teach a digitized or received page, exactly like a preset design.

        Learn Mode only ever works off ``_learn_parts``, so a page slots
        straight in. Two things differ from a preset: the strokes are already
        in canvas space the right way up (see _learn_tf_for), and when the
        child reaches Pulli Mode the scaffold is the page's *own* pulli rather
        than dots sampled off the strokes — those are the dots the grandmother
        actually drew around.
        """
        paths = [[(float(x), float(y)) for x, y in p]
                 for e in data.get('shapes', [])
                 for p in e.get('paths', []) if len(p) >= 2]
        if not paths:
            self.show_hint_popup("That page has no strokes to teach")
            return
        share = self._share_block(data)
        nb = data.get('notebook') or {}

        self._learn_design   = data.get('name') or "Notebook page"
        self._learn_parts    = self._learn_fit_paths(paths)
        self._learn_flip_y   = False
        self._learn_center   = (MARGIN_L + GRAPH_W // 2, MARGIN_T + GRAPH_H // 2)
        self._learn_share_src = ({
            "sender":  share.get("from") or "",
            "book":    nb.get('book') or "",
            "page":    nb.get('page') or 0,
            "page_id": share.get("page_id") or "",
        } if share else None)
        self._learn_done_parts = set()
        self._learn_robot_idx  = None
        self._learn_streaming  = False
        self._learn_next_free  = 0
        self._learn_student_idx = None
        self._learn_level_note = None
        self._learn_sym_pairs  = []
        self._learn_sym_idx    = 0
        self._learn_dots       = []
        self._learn_dots_laid  = False

        if self._learn_lesson == "symmetry":
            self._learn_start_symmetry(self._learn_design)
            return
        if self._learn_lesson == "oneline":
            self._open_learn_oneline()
            return

        robot_parts = self._learn_build_plan()
        if self._learn_is_pulli():
            own = [(d[0], d[1]) for d in self._saved_guide_dots(data)]
            self._learn_dots = own or self._learn_pulli_dots()
        self.log_to_console(
            f"Learn Mode: '{self._learn_design}'"
            + (f" (from {share.get('from')})" if share.get("from") else "")
            + f" has {len(self._learn_parts)} parts at "
              f"{self._learn_level_label()} — robot draws {len(robot_parts)}, "
              f"you draw {len(self._learn_parts) - len(robot_parts)}.", "info")

        if self._learn_dots:
            self._open_learn_pulli_step()
            return
        self._learn_advance()

    def _set_learn_lesson(self, kind):
        """Switch between the whole-design walk and the symmetry drill."""
        if kind == self._learn_lesson:
            return
        self._learn_lesson = kind
        self._open_learn_gallery()

    def _choose_learn_design(self, name):
        """Split the design between student and robot, then start the lesson."""
        cx = MARGIN_L + GRAPH_W // 2
        cy = MARGIN_T + GRAPH_H // 2
        size = min(GRAPH_W, GRAPH_H) * 0.35
        paths = PRESET_DESIGNS[name]['generator'](cx, cy, size)
        self._learn_design = name
        self._learn_parts = [p for p in paths if len(p) >= 2]
        self._learn_center = (cx, cy)
        # A preset is nobody's page: clear whatever the last lesson left, so a
        # built-in design can never offer to be "sent back" to a relative.
        self._learn_flip_y = True
        self._learn_share_src = None
        if not self._learn_parts:
            self.show_hint_popup("That design has no parts to teach")
            return
        self._learn_done_parts = set()
        self._learn_robot_idx = None
        self._learn_streaming = False
        self._learn_next_free = 0
        self._learn_student_idx = None
        self._learn_level_note = None
        self._learn_sym_pairs = []
        self._learn_sym_idx = 0
        # Cleared before the branch so a previous Pulli Mode run can't leave
        # its scaffold behind in a symmetry lesson.
        self._learn_dots = []
        self._learn_dots_laid = False

        if self._learn_lesson == "symmetry":
            self._learn_start_symmetry(name)
            return
        if self._learn_lesson == "oneline":
            # A visualisation, not a lesson — no camera step, nothing scored.
            self._open_learn_oneline()
            return

        robot_parts = self._learn_build_plan()
        self._learn_dots = (self._learn_pulli_dots()
                            if self._learn_is_pulli() else [])
        self.log_to_console(
            f"Learn Mode: '{name}' has {len(self._learn_parts)} parts at "
            f"{self._learn_level_label()} — robot draws "
            f"{len(robot_parts)}, you draw "
            f"{len(self._learn_parts) - len(robot_parts)}.", "info")

        if self._learn_dots:
            # Pulli Mode: the dots go down first, as one whole scaffold, then
            # every line is the child's.
            self._open_learn_pulli_step()
            return
        self._learn_advance()

    # ── Kid Mode: config, theme, skin ────────────────────────────────────────
    # Same read/write pattern as the camera and language configs: a small JSON
    # file next to the app, loaded in __init__, never allowed to break anything
    # if it is missing or corrupt.

    def _load_kid_mode_config(self):
        try:
            with open(KID_MODE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.kid_mode   = bool(data.get("kid_mode", False))
            # Sounds stay off unless the file explicitly says otherwise —
            # a classroom of 30 tablets chirping is nobody's idea of a feature.
            self.kid_sounds = bool(data.get("sounds", False))
        except Exception:
            self.kid_mode   = False
            self.kid_sounds = False

    def _save_kid_mode_config(self):
        try:
            with open(KID_MODE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"kid_mode": self.kid_mode,
                           "sounds":   self.kid_sounds}, f, indent=2)
        except OSError as e:
            self.log_to_console(f"Kid Mode: couldn't save the setting — {e}", "err")

    def _toggle_kid_mode(self):
        self.kid_mode = not self.kid_mode
        self._save_kid_mode_config()
        self.log_to_console(
            f"Kid Mode {'ON — Learn Mode is now a game' if self.kid_mode else 'OFF'}.",
            "info")
        # Repaint the Settings popup so the button label follows the state, and
        # restart any open Learn screen under the new skin.
        if self._settings_popup is not None:
            self._open_settings_popup()
        if self._learn_popup is not None:
            self._open_learn_gallery()

    def _toggle_kid_sounds(self):
        self.kid_sounds = not self.kid_sounds
        self._save_kid_mode_config()
        if self.kid_sounds:
            self._kid_sound("cheer")
        if self._settings_popup is not None:
            self._open_settings_popup()

    # ── theme accessors: normal value, or the kid one ─────────────────────────
    def k_card(self):   return KID_THEME["card"]   if self.kid_mode else BG_CARD
    def k_input(self):  return KID_THEME["input"]  if self.kid_mode else BG_INPUT
    def k_ink(self):    return KID_THEME["ink"]    if self.kid_mode else TEXT_PRIMARY
    def k_dim(self):    return KID_THEME["dim"]    if self.kid_mode else TEXT_DIM
    def k_canvas(self): return KID_THEME["canvas"] if self.kid_mode else CANVAS_BG

    def k_family(self):
        """The playful font family, or Segoe UI when Kid Mode is off.

        Resolved against the families Tk actually reports, because naming a font
        that isn't installed doesn't fail — it silently substitutes something
        else, which is how a cartoon screen ends up in Times New Roman.
        """
        if not self.kid_mode:
            return "Segoe UI"
        if self._kid_font is None:
            try:
                import tkinter.font as _tkf
                have = {f.lower() for f in _tkf.families(self.root)}
            except Exception:
                have = set()
            self._kid_font = next(
                (f for f in KID_FONT_CANDIDATES if f.lower() in have), "Segoe UI")
            self.log_to_console(f"Kid Mode: using the '{self._kid_font}' font.",
                                "info")
        return self._kid_font

    def k_font(self, size, weight="normal"):
        return (self.k_family(), size, weight)

    def kid_pick(self, kid_text, adult_text):
        """Choose between child wording and the normal wording."""
        return kid_text if self.kid_mode else adult_text

    def _kid_skin_tree(self, widget, depth=0):
        """Recolour a finished Learn-Mode widget tree for Kid Mode.

        Applied as one pass over the built tree rather than threading a theme
        argument through every screen: the Learn Mode screens are ordinary
        tk widgets using the module colour constants, so remapping those
        constants after the fact restyles all of them — including the ones added
        later — without touching a single call site.
        """
        if not self.kid_mode or depth > 12:
            return
        try:
            kids = widget.winfo_children()
        except tk.TclError:
            return
        for w in kids:
            # CTk widgets are composites (an internal canvas plus a label), so
            # restyle them through their own API and never walk inside — poking
            # at their internals with raw bg/fg breaks how they draw themselves.
            if isinstance(w, ctk.CTkBaseClass):
                if isinstance(w, ctk.CTkButton):
                    try:
                        w.configure(corner_radius=S(18),
                                    font=(self.k_family(), FS(13), "bold"))
                    except Exception:
                        pass
                continue
            try:
                cls = w.winfo_class()
                if cls == "Label" or cls == "Frame" or cls == "Canvas":
                    cur_bg = str(w.cget("bg"))
                    if cur_bg == BG_CARD:
                        w.configure(bg=KID_THEME["card"])
                    elif cur_bg == BG_INPUT:
                        w.configure(bg=KID_THEME["input"])
                    elif cur_bg == CANVAS_BG:
                        w.configure(bg=KID_THEME["canvas"])
                if cls == "Label":
                    cur_fg = str(w.cget("fg"))
                    if cur_fg == TEXT_PRIMARY:
                        w.configure(fg=KID_THEME["ink"])
                    elif cur_fg == TEXT_DIM:
                        w.configure(fg=KID_THEME["dim"])
                    f = w.cget("font")
                    fam, size, weight = self._kid_parse_font(f)
                    if fam:
                        w.configure(font=(self.k_family(), size, weight))
            except (tk.TclError, ValueError):
                pass
            self._kid_skin_tree(w, depth + 1)

    @staticmethod
    def _kid_parse_font(spec):
        """Pull (family, size, weight) out of whatever Tk hands back for a font.

        Tk returns a tuple sometimes and a flat string like
        '{Segoe UI} 11 bold' others, so both shapes have to be handled or the
        skin pass throws on the first Label it meets.
        """
        try:
            if isinstance(spec, (tuple, list)) and spec:
                fam = str(spec[0])
                size = int(spec[1]) if len(spec) > 1 else 10
                weight = str(spec[2]) if len(spec) > 2 else "normal"
                return fam, size, weight
            text = str(spec)
            if not text:
                return None, 10, "normal"
            weight = "bold" if "bold" in text.lower() else "normal"
            nums = [int(t) for t in text.replace("{", " ").replace("}", " ").split()
                    if t.lstrip("-").isdigit()]
            size = abs(nums[0]) if nums else 10
            fam = text.split("}")[0].lstrip("{") if "{" in text else text.split()[0]
            return fam, size, weight
        except Exception:
            return None, 10, "normal"

    # ── Kid Mode: sounds (muted unless switched on) ───────────────────────────
    def _kid_sound(self, kind):
        """Tiny beep cue. No-op unless Kid Mode AND sounds are both on.

        winsound is stdlib but Windows-only and blocks for the duration of the
        beep, so it runs on a daemon thread and every failure is swallowed —
        a missing sound device must never interrupt a lesson.
        """
        if not (self.kid_mode and self.kid_sounds):
            return
        tunes = {"cheer": [(880, 90), (1175, 90), (1568, 130)],
                 "oops":  [(440, 130), (349, 160)],
                 "step":  [(1047, 60)],
                 "badge": [(784, 80), (988, 80), (1319, 80), (1568, 160)]}
        notes = tunes.get(kind)
        if not notes:
            return

        def _play():
            try:
                import winsound
                for freq, ms in notes:
                    winsound.Beep(freq, ms)
            except Exception:
                pass
        threading.Thread(target=_play, daemon=True).start()

    # ── Kid Mode: the mascot ──────────────────────────────────────────────────
    # A peacock drawn from canvas primitives rather than an image file, so it
    # ships with the source and can't go missing. It has four moods and reacts
    # to what the child just did.

    KID_MASCOT_LINES = {
        "idle":   ["Let's make a rangoli!", "Ready when you are!",
                   "Pick a pretty one!"],
        "watch":  ["Watch closely…", "Ooh, look at that line!",
                   "My turn — then yours!"],
        "cheer":  ["Wow! Nice one!", "You did it!", "Beautiful!",
                   "High five!", "You're getting good at this!"],
        "oops":   ["Nearly! Try that bit again.", "Wobbly lines are fine —"
                   " keep going!", "Every rangoli takes practice!",
                   "Go slow, you've got this."],
    }

    def _kid_set_mood(self, mood, line=None):
        """Point the mascot at a mood and say something."""
        if not self.kid_mode:
            return
        self._kid_mascot_mood = mood
        if line is None:
            opts = self.KID_MASCOT_LINES.get(mood) or [""]
            line = opts[random.randrange(len(opts))]
        lbl = self._kid_bubble
        if lbl is not None:
            try:
                if lbl.winfo_exists():
                    lbl.configure(text=line)
            except tk.TclError:
                self._kid_bubble = None
        if mood == "cheer":
            self._kid_sound("cheer")
        elif mood == "oops":
            self._kid_sound("oops")

    def _kid_build_mascot(self, parent, mood="idle"):
        """Place the mascot plus its speech bubble into a Learn-Mode popup."""
        if not self.kid_mode:
            return None
        holder = tk.Frame(parent, bg=KID_THEME["card"])
        cv = tk.Canvas(holder, width=S(74), height=S(74),
                       bg=KID_THEME["card"], highlightthickness=0)
        cv.pack(side="left")
        self._kid_mascot = cv
        self._kid_mascot_mood = mood
        self._kid_mascot_phase = 0
        self._kid_bubble = tk.Label(
            holder, text="", bg=KID_THEME["input"], fg=KID_THEME["ink"],
            font=self.k_font(FS(10), "bold"), padx=S(10), pady=S(6),
            justify="left", wraplength=S(230))
        self._kid_bubble.pack(side="left", padx=(S(8), 0))
        self._kid_set_mood(mood)
        self._kid_mascot_tick()
        return holder

    def _kid_mascot_tick(self):
        """Animate the peacock: a gentle bob, a shimmering tail, a blink."""
        cv = self._kid_mascot
        try:
            alive = cv is not None and cv.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._kid_mascot = None
            self._kid_mascot_after = None
            return

        p = self._kid_mascot_phase
        self._kid_mascot_phase = p + 1
        mood = self._kid_mascot_mood
        cv.delete("all")

        cx, cy = S(37), S(46)
        bob = math.sin(p * 0.25) * (S(3) if mood == "cheer" else S(1.4))
        # tail fan behind the body
        n = 7
        for i in range(n):
            ang = math.radians(-160 + i * (120 / (n - 1)))
            spread = S(26) + (S(4) * math.sin(p * 0.2 + i) if mood == "cheer"
                              else S(1) * math.sin(p * 0.1 + i))
            tx = cx + spread * math.cos(ang)
            ty = cy + spread * math.sin(ang) - S(6) + bob
            shade = ["#0ea5e9", "#06b6d4", "#14b8a6", "#22c55e",
                     "#14b8a6", "#06b6d4", "#0ea5e9"][i]
            cv.create_line(cx, cy + bob, tx, ty, fill=shade, width=S(3),
                           capstyle="round")
            cv.create_oval(tx - S(4), ty - S(4), tx + S(4), ty + S(4),
                           fill=shade, outline="#134e4a", width=1)
            cv.create_oval(tx - S(1.6), ty - S(1.6), tx + S(1.6), ty + S(1.6),
                           fill="#fde047", outline="")
        # body
        cv.create_oval(cx - S(11), cy - S(6) + bob, cx + S(11), cy + S(15) + bob,
                       fill="#1d4ed8", outline="#1e3a8a", width=2)
        # head + neck
        hy = cy - S(17) + bob
        cv.create_line(cx, cy + bob, cx, hy, fill="#1d4ed8", width=S(6),
                       capstyle="round")
        cv.create_oval(cx - S(7), hy - S(7), cx + S(7), hy + S(7),
                       fill="#2563eb", outline="#1e3a8a", width=2)
        # crest
        for k in (-1, 0, 1):
            cv.create_line(cx + k * S(3), hy - S(6),
                           cx + k * S(5), hy - S(12), fill="#facc15", width=2)
            cv.create_oval(cx + k * S(5) - 2, hy - S(12) - 2,
                           cx + k * S(5) + 2, hy - S(12) + 2,
                           fill="#facc15", outline="")
        # eye — blinks every so often, squeezes shut when cheering
        blink = (p % 46) in (0, 1, 2)
        if blink or mood == "cheer":
            cv.create_line(cx + S(1), hy - S(1), cx + S(6), hy - S(1),
                           fill="#0f172a", width=2)
        else:
            cv.create_oval(cx + S(1), hy - S(3), cx + S(5), hy + S(1),
                           fill="#0f172a", outline="")
        # beak, tilted down a little when encouraging
        droop = S(2) if mood == "oops" else 0
        cv.create_polygon(cx + S(6), hy - S(1) + droop,
                          cx + S(13), hy + S(1) + droop,
                          cx + S(6), hy + S(3) + droop,
                          fill="#f59e0b", outline="#b45309")
        # feet
        cv.create_line(cx - S(4), cy + S(15) + bob, cx - S(4), cy + S(19) + bob,
                       fill="#f59e0b", width=2)
        cv.create_line(cx + S(4), cy + S(15) + bob, cx + S(4), cy + S(19) + bob,
                       fill="#f59e0b", width=2)

        delay = 70 if mood == "cheer" else 130
        self._kid_mascot_after = self.root.after(delay, self._kid_mascot_tick)

    def _kid_stop_mascot(self):
        if self._kid_mascot_after is not None:
            try: self.root.after_cancel(self._kid_mascot_after)
            except Exception: pass
            self._kid_mascot_after = None
        self._kid_mascot = None
        self._kid_bubble = None

    # ── Kid Mode: confetti ────────────────────────────────────────────────────
    def _kid_confetti_burst(self, n=34):
        """Throw confetti across the open Learn popup's backdrop canvas."""
        if not self.kid_mode:
            return
        cv = self._kid_glass
        try:
            if cv is None or not cv.winfo_exists():
                return
            W = int(cv.cget("width"))
        except (tk.TclError, ValueError):
            return
        self._kid_stop_confetti()
        self._kid_confetti = []
        for _ in range(n):
            x = random.uniform(W * 0.1, W * 0.9)
            y = random.uniform(-S(40), S(40))
            col = KID_CONFETTI_COLORS[random.randrange(len(KID_CONFETTI_COLORS))]
            size = random.uniform(S(4), S(9))
            try:
                item = cv.create_rectangle(x, y, x + size, y + size * 0.6,
                                           fill=col, outline="",
                                           tags="kid_confetti")
            except tk.TclError:
                return
            self._kid_confetti.append({
                "id": item, "x": x, "y": y,
                "vx": random.uniform(-S(1.2), S(1.2)),
                "vy": random.uniform(S(2.4), S(5.2)),
                "w": size, "h": size * 0.6})
        self._kid_confetti_step(0)

    def _kid_confetti_step(self, frame):
        cv = self._kid_glass
        try:
            alive = cv is not None and cv.winfo_exists()
            H = int(cv.cget("height")) if alive else 0
        except (tk.TclError, ValueError):
            alive, H = False, 0
        if not alive or frame > 90:
            self._kid_stop_confetti()
            return
        for c in self._kid_confetti:
            c["x"] += c["vx"]
            c["y"] += c["vy"]
            c["vy"] += S(0.12)              # a little gravity
            try:
                cv.coords(c["id"], c["x"], c["y"],
                          c["x"] + c["w"], c["y"] + c["h"])
            except tk.TclError:
                pass
        self._kid_confetti = [c for c in self._kid_confetti if c["y"] < H + S(20)]
        if not self._kid_confetti:
            self._kid_stop_confetti()
            return
        self._kid_confetti_after = self.root.after(
            24, lambda: self._kid_confetti_step(frame + 1))

    def _kid_stop_confetti(self):
        if self._kid_confetti_after is not None:
            try: self.root.after_cancel(self._kid_confetti_after)
            except Exception: pass
            self._kid_confetti_after = None
        cv = self._kid_glass
        if cv is not None:
            try:
                if cv.winfo_exists():
                    cv.delete("kid_confetti")
            except tk.TclError:
                pass
        self._kid_confetti = []

    def _kid_celebrate(self, line=None):
        """The full "you finished a bit" reaction: confetti, cheer, mascot."""
        if not self.kid_mode:
            return
        self._kid_confetti_burst()
        self._kid_set_mood("cheer", line)

    # ── Kid Mode: the powder-bottle buddy that walks the simulated line ──────
    def _kid_draw_buddy(self, canvas, x, y, phase, tag="kid_buddy", scale=1.0):
        """Draw the little squeeze-bottle character at the pen tip.

        Replaces the plain green dot in the simulators: same position, same
        meaning, but it leans into the direction of travel and its body
        squashes as it "walks", so a child reads it as somebody drawing rather
        than a cursor moving.
        """
        try:
            canvas.delete(tag)
        except tk.TclError:
            return
        u = lambda v: v * scale
        squash = math.sin(phase * 0.4) * u(1.6)
        lean = math.sin(phase * 0.2) * u(1.2)
        try:
            # bottle body
            canvas.create_oval(x - u(7) + lean, y - u(15) - squash,
                               x + u(7) + lean, y + u(3),
                               fill="#fb7185", outline="#9f1239", width=2,
                               tags=tag)
            # cap / nozzle pointing at the line
            canvas.create_polygon(x - u(3) + lean, y - u(15) - squash,
                                  x + u(3) + lean, y - u(15) - squash,
                                  x + u(1.5), y - u(21) - squash,
                                  x - u(1.5), y - u(21) - squash,
                                  fill="#f59e0b", outline="#b45309", tags=tag)
            # eyes + smile
            for ex in (-u(3), u(2.4)):
                canvas.create_oval(x + ex + lean, y - u(10) - squash,
                                   x + ex + u(2.4) + lean, y - u(7) - squash,
                                   fill="#ffffff", outline="", tags=tag)
                canvas.create_oval(x + ex + u(0.7) + lean, y - u(9.4) - squash,
                                   x + ex + u(1.9) + lean, y - u(8) - squash,
                                   fill="#0f172a", outline="", tags=tag)
            canvas.create_arc(x - u(3.4) + lean, y - u(8) - squash,
                              x + u(3.4) + lean, y - u(3) - squash,
                              start=200, extent=140, style="arc",
                              outline="#7f1d1d", width=2, tags=tag)
            # the drop leaving the nozzle
            canvas.create_oval(x - u(1.4), y + u(1), x + u(1.4), y + u(4),
                               fill="#fda4af", outline="", tags=tag)
        except tk.TclError:
            pass

    def _kid_sparkle(self, canvas, x, y, tag="kid_spark"):
        """Drop a sparkle behind the buddy, culling the oldest so the trail
        stays a trail rather than slowly filling the canvas."""
        if not self.kid_mode:
            return
        col = KID_SPARKLE_COLORS[random.randrange(len(KID_SPARKLE_COLORS))]
        r = random.uniform(1.6, 3.4)
        ox = x + random.uniform(-5.0, 5.0)
        oy = y + random.uniform(-5.0, 5.0)
        try:
            item = canvas.create_text(ox, oy, text="✦", fill=col,
                                      font=("Segoe UI", int(6 + r)), tags=tag)
        except tk.TclError:
            return
        self._kid_sparkles.append((canvas, item))
        while len(self._kid_sparkles) > KID_MAX_SPARKLES:
            old_cv, old_item = self._kid_sparkles.pop(0)
            try:
                old_cv.delete(old_item)
            except tk.TclError:
                pass

    def _kid_clear_sparkles(self):
        for cv, item in self._kid_sparkles:
            try:
                cv.delete(item)
            except tk.TclError:
                pass
        self._kid_sparkles = []

    # ── Kid Mode: stars, stickers, streaks ────────────────────────────────────
    @staticmethod
    def _kid_stars_for(score, out_of=10):
        """Score → 1-5 stars. The number itself is still shown alongside; this
        is a friendlier reading of it, not a replacement for the data."""
        try:
            norm = float(score) * 10.0 / (float(out_of) or 10.0)
        except (TypeError, ValueError):
            return 0
        return sum(1 for t in KID_STAR_THRESHOLDS if norm >= t)

    def _kid_star_text(self, score, out_of=10):
        n = self._kid_stars_for(score, out_of)
        return "★" * n + "☆" * (5 - n)

    def _kid_practice_days(self):
        """Distinct calendar days that have a recorded session, newest first."""
        days = set()
        for s in self._learn_sessions:
            ts = str(s.get("timestamp") or "")
            if len(ts) >= 10:
                days.add(ts[:10])
        return sorted(days, reverse=True)

    def _kid_streak(self):
        """Consecutive days up to today with at least one session.

        Counts practice, not scores, so a child who drew today gets credit even
        if there was no camera or no API key. A gap of one day ends it.
        """
        days = self._kid_practice_days()
        if not days:
            return 0
        import datetime
        try:
            today = datetime.date.fromisoformat(time.strftime("%Y-%m-%d"))
            latest = datetime.date.fromisoformat(days[0])
        except ValueError:
            return 0
        if (today - latest).days > 1:
            return 0                      # streak already broken
        streak, cursor = 1, latest
        for d in days[1:]:
            try:
                dd = datetime.date.fromisoformat(d)
            except ValueError:
                continue
            if (cursor - dd).days == 1:
                streak += 1
                cursor = dd
            elif (cursor - dd).days == 0:
                continue                  # same day, already counted
            else:
                break
        return streak

    def _kid_earned_badges(self):
        """Which stickers are genuinely earned, by key.

        Every rule reads the profile. Score-based rules use AI-scored rows only,
        for the same reason the level does: the fallback verdict is a hardcoded
        9/10 and would hand out a sticker nobody earned.
        """
        ai = [s for s in self._learn_sessions if s.get("scored_by") == "ai"]
        def norm(s):
            try:
                return float(s.get("score", 0)) * 10.0 / (float(s.get("out_of") or 10))
            except (TypeError, ValueError):
                return 0.0
        sym_good = sum(1 for s in ai
                       if s.get("lesson") == "symmetry" and norm(s) >= 8)
        earned = set()
        if ai:
            earned.add("first")
        if any(norm(s) >= 9 for s in ai):
            earned.add("steady")
        if sym_good >= 3:
            earned.add("symmetry")
        if self._kid_oneline_solved:
            earned.add("oneline")
        if self._learn_level >= LEARN_MAX_LEVEL:
            earned.add("pulli")
        if self._kid_streak() >= 7:
            earned.add("streak")
        return earned

    def _kid_progress_line(self):
        """One-line summary for the top of the kid gallery."""
        streak = self._kid_streak()
        earned = len(self._kid_earned_badges())
        bits = []
        if streak >= 2:
            bits.append(f"🔥 {streak}-day streak!")
        elif streak == 1:
            bits.append("🔥 Practising today!")
        bits.append(f"🏅 {earned} of {len(KID_BADGES)} stickers")
        bits.append(f"⭐ Level {self._learn_level}")
        return "   ".join(bits)

    def _open_kid_stickers(self):
        """The sticker book — every badge, earned or still to go."""
        earned = self._kid_earned_badges()
        streak = self._kid_streak()
        W, H = S(600), S(560)
        _, body = self._learn_shell(
            W, H, "My stickers",
            f"You've earned {len(earned)} of {len(KID_BADGES)}. "
            f"{'Keep the streak going!' if streak else 'Draw today to start a streak!'}",
            outline=KID_THEME["outline"], mascot="cheer" if earned else "idle")

        days = len(self._kid_practice_days())
        top = tk.Label(
            body,
            text=(f"🔥 {streak}-day streak" if streak else "No streak yet") +
                 f"   ·   {days} day{'' if days == 1 else 's'} of practice"
                 f"   ·   {len(self._learn_sessions)} rangoli drawn",
            bg=self.k_card(), fg=self.k_ink(), font=self.k_font(FS(11), "bold"))
        top.pack(anchor="w", pady=(0, S(10)))

        grid = tk.Frame(body, bg=self.k_card())
        grid.pack(fill="both", expand=True)
        for i, b in enumerate(KID_BADGES):
            got = b["key"] in earned
            r, c = divmod(i, 2)
            card = tk.Frame(grid, bg=KID_THEME["input"] if got else "#eeeae4")
            card.grid(row=r, column=c, padx=S(6), pady=S(6), sticky="nsew")
            grid.grid_columnconfigure(c, weight=1)
            tk.Label(card, text=b["icon"] if got else "🔒",
                     bg=card.cget("bg"), font=("Segoe UI Emoji", FS(22))).pack(
                         pady=(S(8), 0))
            tk.Label(card, text=b["name"], bg=card.cget("bg"),
                     fg=self.k_ink() if got else "#a49a90",
                     font=self.k_font(FS(11), "bold")).pack()
            tk.Label(card, text="Earned!" if got else b["how"],
                     bg=card.cget("bg"),
                     fg=KID_THEME["cheer"] if got else "#a49a90",
                     font=self.k_font(FS(9)), wraplength=S(230),
                     justify="center").pack(pady=(S(2), S(8)))

        self._color_button(
            body, "← Back to designs", self._open_learn_gallery,
            ACCENT_GREEN, width=W-52, height=S(44),
            font_size=FS(12)).pack(side="bottom", pady=(S(10), 0))

    # ── PROGRESS & IMPACT DASHBOARD ──────────────────────────────────────────
    # One screen answering the question a judge always asks: how do you know
    # it works? Everything here is read back off the learner profile that
    # Learn Mode has been writing all along — nothing is invented for display.
    #
    # The split between AI-scored attempts and practice-only ones runs through
    # the whole screen. A fallback verdict is a hardcoded 9/10 shown when there
    # was no photo or no API key; those rows are real practice and are counted
    # as practice, but they are never allowed to look like evidence of skill.

    LESSON_LABELS = (("full", "Whole design"),
                     ("symmetry", "Symmetry challenge"),
                     ("oneline", "One continuous line"))

    def _progress_stats(self):
        """Every number the dashboard shows, worked out in one place.

        Deliberately free of widgets: the figures can be checked without a
        screen, and every panel is guaranteed to quote the same ones."""
        def norm(s):
            try:
                out_of = float(s.get("out_of") or 10) or 10.0
                return float(s.get("score", 0)) * 10.0 / out_of
            except (TypeError, ValueError):
                return None

        sessions = list(self._learn_sessions)
        ai_rows = [s for s in sessions if s.get("scored_by") == "ai"]
        practice_only = len(sessions) - len(ai_rows)

        trend = []
        for s in ai_rows:
            v = norm(s)
            if v is None:
                continue
            ts = str(s.get("timestamp") or "")
            trend.append({
                "score":  v,
                "when":   ts[:10],
                "clock":  ts[11:16],
                "design": s.get("design") or "Rangoli",
                "lesson": s.get("lesson") or "full",
                "level":  s.get("level"),
            })

        lessons = {}
        for key, _label in self.LESSON_LABELS:
            rows = [s for s in sessions if (s.get("lesson") or "full") == key]
            scored = [norm(s) for s in rows if s.get("scored_by") == "ai"]
            scored = [v for v in scored if v is not None]
            lessons[key] = {
                "attempts": len(rows),
                "scored":   len(scored),
                "avg":      (sum(scored) / len(scored)) if scored else None,
                "best":     max(scored) if scored else None,
            }

        # What it would take to move up, by the same rule the level itself
        # uses — a consecutive run of AI-scored attempts at this level.
        if self._learn_level >= LEARN_MAX_LEVEL:
            next_step = ("Pulli Mode — the top level. The robot lays only the "
                         "dots; every line is the learner's.")
        else:
            run = 0
            for v in reversed(self._learn_scored_attempts(level=self._learn_level)):
                if v < LEARN_PROMOTE_SCORE:
                    break
                run += 1
            need = max(1, LEARN_LEVEL_WINDOW - run)
            next_step = (
                f"{need} more AI-scored rangoli at {int(LEARN_PROMOTE_SCORE)}+ "
                f"to reach Level {self._learn_level + 1} · "
                f"{self._learn_level_meta(self._learn_level + 1)['title']}."
                + (f" ({run} in a row so far.)" if run else ""))

        # Notebook pages digitised — the kolam books that have been rescued
        # off paper, counted straight from the gallery.
        books = {}
        try:
            for _name, _full, data in self._load_saved_designs():
                nb = data.get("notebook")
                if isinstance(nb, dict):
                    books.setdefault(str(nb.get("book") or "Notebook"),
                                     set()).add(self._nb_page_no(data))
        except Exception:
            books = {}

        return {
            "level":         self._learn_level,
            "level_label":   self._learn_level_label(),
            "level_blurb":   self._learn_level_meta().get("blurb", ""),
            "next_step":     next_step,
            "total":         len(sessions),
            "ai_count":      len(ai_rows),
            "practice_only": practice_only,
            "trend":         trend,
            "avg":           (sum(t["score"] for t in trend) / len(trend)
                              if trend else None),
            "best":          max((t["score"] for t in trend), default=None),
            "designs":       len({s.get("design") for s in sessions
                                  if s.get("design")}),
            "days":          len(self._kid_practice_days()),
            "streak":        self._kid_streak(),
            "badges":        self._kid_earned_badges(),
            "lessons":       lessons,
            "books":         {b: len(p) for b, p in books.items()},
            "pages":         sum(len(p) for p in books.values()),
            "recent":        list(reversed(sessions))[:6],
        }

    def _open_progress_dashboard(self):
        self._close_progress_dashboard()
        self.root.update_idletasks()
        st = self._progress_stats()

        W, H = S(900), S(700)
        sx = self.root.winfo_screenwidth()  // 2 - W // 2
        sy = max(S(10), self.root.winfo_screenheight() // 2 - H // 2)

        card_bg = self.k_card()
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._progress_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK,
                          highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=S(24),
                                fill=card_bg, outline=ACCENT_BLUE, width=2)
        glass.create_text(S(28), S(30),
                          text=self.tr("Progress & Impact"), anchor="w",
                          fill=self.k_ink(),
                          font=self.k_font(FS(16), "bold"))
        glass.create_text(
            S(28), S(54),
            text=self.tr("Everything below is read back from the learner's own "
                         "saved sessions. Scores only count when the vision "
                         "model judged a real photo."),
            anchor="w", fill=self.k_dim(), font=self.k_font(FS(9)),
            width=W - S(80))

        close_lbl = tk.Label(popup, text="✕", bg=card_bg, fg=self.k_dim(),
                             font=self.k_font(FS(14), "bold"), cursor="hand2")
        close_lbl.place(x=W-S(44), y=S(20))
        close_lbl.bind("<Button-1>", lambda e: self._close_progress_dashboard())
        # No grab: this one is meant to be glanced at with the app still live,
        # so Escape has to work as well as the ✕.
        popup.bind("<Escape>", lambda e: self._close_progress_dashboard())

        # Scrolling body — the dashboard is deliberately longer than one screen
        # rather than shrinking every panel to fit.
        outer = tk.Frame(popup, bg=card_bg)
        outer.place(x=S(26), y=S(80), width=W-S(52), height=H-S(104))
        scroll_cv = tk.Canvas(outer, bg=card_bg, highlightthickness=0)
        scroll_sb = tk.Scrollbar(outer, orient="vertical",
                                 command=scroll_cv.yview)
        body = tk.Frame(scroll_cv, bg=card_bg)
        scroll_cv.configure(yscrollcommand=scroll_sb.set)
        scroll_sb.pack(side="right", fill="y")
        scroll_cv.pack(side="left", fill="both", expand=True)
        inner_w = W - S(52) - S(20)
        # Pin the scrolled frame to the visible width. Without this it takes
        # its natural width instead, and any row wider than the popup — a grid
        # of sticker chips, say — quietly runs off the right-hand edge where
        # there is nothing to scroll it back into view.
        scroll_cv.create_window((0, 0), window=body, anchor="nw", width=inner_w)
        body.bind("<Configure>", lambda e: scroll_cv.configure(
            scrollregion=scroll_cv.bbox("all")))
        popup.bind_all("<MouseWheel>", lambda e: scroll_cv.yview_scroll(
            -1 if e.delta > 0 else 1, "units"))

        def section(text, note=None):
            tk.Label(body, text=text, bg=card_bg, fg=self.k_ink(),
                     font=self.k_font(FS(12), "bold")).pack(
                anchor="w", pady=(S(14), S(2)))
            if note:
                tk.Label(body, text=note, bg=card_bg, fg=self.k_dim(),
                         font=self.k_font(FS(9)), wraplength=inner_w,
                         justify="left").pack(anchor="w", pady=(0, S(6)))

        # ── headline tiles ──────────────────────────────────────────────
        tiles = tk.Frame(body, bg=card_bg)
        tiles.pack(fill="x", pady=(S(4), S(2)))
        tile_specs = [
            (f"{st['level']}", "of 5", "Level", ACCENT_CYAN),
            (f"{st['ai_count']}", "AI-scored", "Rangoli judged", ACCENT_GREEN),
            (f"{st['total']}", "sessions", "Rangoli drawn", ACCENT_BLUE),
            (f"{st['days']}", "days", "Days practised", ACCENT_PURP),
            (f"{st['streak']}", "in a row", "Current streak", ACCENT_AMBER),
            (f"{len(st['badges'])}", f"of {len(KID_BADGES)}", "Stickers",
             ACCENT_PINK),
        ]
        for value, unit, label, accent in tile_specs:
            t = tk.Frame(tiles, bg=BG_INPUT if not self.kid_mode else card_bg,
                         highlightbackground=accent, highlightthickness=1)
            t.pack(side="left", expand=True, fill="both", padx=(0, S(6)))
            tk.Label(t, text=value, bg=t.cget("bg"), fg=accent,
                     font=self.k_font(FS(20), "bold")).pack(pady=(S(8), 0))
            tk.Label(t, text=unit, bg=t.cget("bg"), fg=self.k_dim(),
                     font=self.k_font(FS(8))).pack()
            tk.Label(t, text=label, bg=t.cget("bg"), fg=self.k_ink(),
                     font=self.k_font(FS(9), "bold")).pack(pady=(S(2), S(8)))

        # ── level + what moves it ───────────────────────────────────────
        section(f"⭐ {st['level_label']}", st['level_blurb'])
        ladder = tk.Frame(body, bg=card_bg)
        ladder.pack(fill="x", pady=(0, S(4)))
        for lvl in range(1, LEARN_MAX_LEVEL + 1):
            here = (lvl == st['level'])
            done = (lvl < st['level'])
            seg = tk.Frame(ladder,
                           bg=ACCENT_CYAN if here else
                              (ACCENT_GREEN if done else BG_INPUT),
                           height=S(8))
            seg.pack(side="left", expand=True, fill="x", padx=(0, S(4)))
        tk.Label(body, text=st['next_step'], bg=card_bg, fg=ACCENT_CYAN,
                 font=self.k_font(FS(9), "bold"), wraplength=inner_w,
                 justify="left").pack(anchor="w", pady=(S(6), 0))

        # ── score trend ─────────────────────────────────────────────────
        section("📈 Scores over time",
                "Every rangoli the vision model scored, oldest first. "
                "Hover a point to see which design it was.")
        self._progress_draw_trend(body, st, inner_w, card_bg)

        # ── evidence split ──────────────────────────────────────────────
        section("🔍 What the scores rest on",
                "Sessions with no photo or no API key fall back to a fixed "
                "sample verdict. They are kept as practice, and kept out of "
                "the level, the chart and the stickers.")
        ev = tk.Frame(body, bg=card_bg)
        ev.pack(fill="x")
        total = max(1, st['total'])
        for label, count, accent in (
                ("AI-scored — counts as evidence", st['ai_count'], ACCENT_GREEN),
                ("Practice only — not scored", st['practice_only'], TEXT_DIM)):
            row = tk.Frame(ev, bg=card_bg)
            row.pack(fill="x", pady=S(2))
            tk.Label(row, text=f"{count}", bg=card_bg, fg=accent, width=4,
                     anchor="w", font=self.k_font(FS(11), "bold")).pack(side="left")
            tk.Label(row, text=label, bg=card_bg, fg=self.k_dim(), width=30,
                     anchor="w", font=self.k_font(FS(9))).pack(side="left")
            bar = tk.Frame(row, bg=BG_INPUT, height=S(12))
            bar.pack_propagate(False)
            bar.pack(side="left", fill="x", expand=True, padx=(S(8), 0))
            fill = tk.Frame(bar, bg=accent, height=S(12))
            fill.place(x=0, y=0, relwidth=count / total, relheight=1.0)

        # ── lesson breakdown ────────────────────────────────────────────
        section("🎯 By lesson",
                "Symmetry and the one-line trick are separate skills, so they "
                "are tracked separately.")
        for key, label in self.LESSON_LABELS:
            d = st['lessons'][key]
            row = tk.Frame(body, bg=card_bg)
            row.pack(fill="x", pady=S(2))
            tk.Label(row, text=label, bg=card_bg, fg=self.k_ink(), width=20,
                     anchor="w", font=self.k_font(FS(10), "bold")).pack(side="left")
            bar = tk.Frame(row, bg=BG_INPUT, height=S(14), width=S(200))
            bar.pack_propagate(False)
            bar.pack(side="left")
            if d['avg'] is not None:
                tk.Frame(bar, bg=ACCENT_CYAN, height=S(14)).place(
                    x=0, y=0, relwidth=max(0.02, min(1.0, d['avg'] / 10.0)),
                    relheight=1.0)
            detail = (f"avg {d['avg']:.1f}/10 over {d['scored']} scored"
                      if d['avg'] is not None else "no scored attempts yet")
            tk.Label(row, text=f"{d['attempts']} attempt"
                              f"{'' if d['attempts'] == 1 else 's'}  ·  {detail}",
                     bg=card_bg, fg=self.k_dim(),
                     font=self.k_font(FS(9))).pack(side="left", padx=(S(10), 0))

        # ── stickers ────────────────────────────────────────────────────
        section(f"🏅 Stickers — {len(st['badges'])} of {len(KID_BADGES)}")
        badges = tk.Frame(body, bg=card_bg)
        badges.pack(fill="x")
        for i, b in enumerate(KID_BADGES):
            got = b["key"] in st['badges']
            chip = tk.Frame(badges, bg=BG_INPUT if not self.kid_mode else card_bg,
                            highlightbackground=ACCENT_AMBER if got else GLASS_EDGE,
                            highlightthickness=1)
            chip.grid(row=i // 3, column=i % 3, sticky="ew",
                      padx=(0, S(6)), pady=S(3))
            badges.grid_columnconfigure(i % 3, weight=1)
            tk.Label(chip, text=f"{b['icon']}  {b['name']}", bg=chip.cget("bg"),
                     fg=self.k_ink() if got else self.k_dim(),
                     font=self.k_font(FS(10), "bold")).pack(
                anchor="w", padx=S(8), pady=(S(5), 0))
            # Wraps inside a third of the panel — the longest "how" line is
            # what sets the column width.
            tk.Label(chip, text="Earned" if got else b["how"],
                     bg=chip.cget("bg"),
                     fg=ACCENT_AMBER if got else self.k_dim(),
                     font=self.k_font(FS(8)), wraplength=S(225),
                     justify="left").pack(anchor="w", padx=S(8), pady=(0, S(5)))

        # ── notebook pages ──────────────────────────────────────────────
        if st['pages']:
            section(f"📖 Kolam notebooks — {st['pages']} page"
                    f"{'' if st['pages'] == 1 else 's'} digitised",
                    "Hand-drawn pages photographed and turned into designs the "
                    "robot can draw.")
            for book, n in sorted(st['books'].items()):
                tk.Label(body, text=f"   • {book} — {n} page"
                                    f"{'' if n == 1 else 's'}",
                         bg=card_bg, fg=self.k_dim(),
                         font=self.k_font(FS(9))).pack(anchor="w")

        # ── recent sessions (the chart's table view) ────────────────────
        section("🕘 Recent sessions")
        if not st['recent']:
            tk.Label(body, text="No sessions recorded yet — finish a rangoli "
                                "in Learn Mode and it will appear here.",
                     bg=card_bg, fg=self.k_dim(),
                     font=self.k_font(FS(9))).pack(anchor="w")
        for s in st['recent']:
            ai = s.get("scored_by") == "ai"
            row = tk.Frame(body, bg=card_bg)
            row.pack(fill="x", pady=S(1))
            tk.Label(row, text=str(s.get("timestamp") or "")[:10].replace("-", "/"),
                     bg=card_bg, fg=self.k_dim(), width=11, anchor="w",
                     font=self.k_font(FS(9))).pack(side="left")
            tk.Label(row, text=(s.get("design") or "Rangoli")[:34],
                     bg=card_bg, fg=self.k_ink(), width=30, anchor="w",
                     font=self.k_font(FS(9))).pack(side="left")
            tk.Label(row, text=f"{s.get('score')}/{s.get('out_of')}",
                     bg=card_bg, fg=ACCENT_GREEN if ai else self.k_dim(),
                     width=7, anchor="w",
                     font=self.k_font(FS(9), "bold")).pack(side="left")
            tk.Label(row, text="AI-scored" if ai else "practice (sample score)",
                     bg=card_bg, fg=ACCENT_GREEN if ai else TEXT_DIM,
                     font=self.k_font(FS(8))).pack(side="left")

        tk.Frame(body, bg=card_bg, height=S(10)).pack()

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()

    def _progress_draw_trend(self, parent, st, width, card_bg):
        """The score-trend plot: one series, so no legend — the heading names
        it. Grid and axes stay recessive, the promote/demote thresholds are
        drawn as labelled reference lines, and only the latest point carries a
        number. A readout under the plot does the job a tooltip would."""
        trend = st['trend']
        h = S(190)
        if len(trend) < 2:
            tk.Label(parent,
                     text=("Not enough AI-scored rangoli to plot a trend yet — "
                           "two are needed. " +
                           (f"{st['ai_count']} scored so far."
                            if st['ai_count'] else
                            "Score one by finishing a rangoli in Learn Mode "
                            "with the camera on.")),
                     bg=card_bg, fg=self.k_dim(), font=self.k_font(FS(9)),
                     wraplength=width, justify="left").pack(anchor="w")
            return

        cv = tk.Canvas(parent, width=width, height=h, bg=card_bg,
                       highlightthickness=0)
        cv.pack(anchor="w")
        readout = tk.Label(parent, text="", bg=card_bg, fg=self.k_dim(),
                           font=self.k_font(FS(9)), anchor="w")
        readout.pack(anchor="w", pady=(S(2), 0))

        pad_l, pad_r, pad_t, pad_b = S(34), S(14), S(12), S(24)
        plot_w = max(S(60), width - pad_l - pad_r)
        plot_h = max(S(60), h - pad_t - pad_b)

        def px(i):
            return pad_l + (plot_w * i / max(1, len(trend) - 1))

        def py(score):
            return pad_t + plot_h * (1.0 - max(0.0, min(10.0, score)) / 10.0)

        # Recessive grid + axis labels.
        for val in (0, 5, 10):
            y = py(val)
            cv.create_line(pad_l, y, pad_l + plot_w, y,
                           fill=GLASS_EDGE, width=1)
            cv.create_text(pad_l - S(6), y, text=str(val), anchor="e",
                           fill=self.k_dim(), font=self.k_font(FS(8)))

        # Status thresholds: the rules that actually move the level, labelled
        # rather than left as bare colour.
        for val, colour, label in (
                (LEARN_PROMOTE_SCORE, ACCENT_GREEN, "level up"),
                (LEARN_DEMOTE_SCORE, ORIGIN_RED, "level down")):
            y = py(val)
            cv.create_line(pad_l, y, pad_l + plot_w, y, fill=colour,
                           width=1, dash=(3, 4))
            cv.create_text(pad_l + plot_w, y - S(7), text=label, anchor="e",
                           fill=colour, font=self.k_font(FS(8)))

        pts = [(px(i), py(t["score"])) for i, t in enumerate(trend)]
        cv.create_line([c for p in pts for c in p], fill=ACCENT_CYAN, width=2,
                       smooth=False)
        for x, y in pts:
            r = S(4)
            # A 2px surface ring keeps overlapping markers separable.
            cv.create_oval(x - r - 2, y - r - 2, x + r + 2, y + r + 2,
                           fill=card_bg, outline="")
            cv.create_oval(x - r, y - r, x + r, y + r,
                           fill=ACCENT_CYAN, outline="")

        last = trend[-1]
        cv.create_text(pts[-1][0], pts[-1][1] - S(14),
                       text=f"{last['score']:.1f}", anchor="e",
                       fill=self.k_ink(), font=self.k_font(FS(9), "bold"))
        cv.create_text(pad_l, h - S(8), text=trend[0]["when"], anchor="w",
                       fill=self.k_dim(), font=self.k_font(FS(8)))
        cv.create_text(pad_l + plot_w, h - S(8), text=last["when"], anchor="e",
                       fill=self.k_dim(), font=self.k_font(FS(8)))

        summary = (f"Average {st['avg']:.1f}/10 · best {st['best']:.1f} · "
                   f"{len(trend)} scored")
        readout.config(text=summary)

        marker = {"id": None}

        def on_move(e):
            i = min(range(len(pts)), key=lambda k: abs(pts[k][0] - e.x))
            if abs(pts[i][0] - e.x) > S(30):
                return on_leave(e)
            t = trend[i]
            if marker["id"] is not None:
                cv.delete(marker["id"])
            marker["id"] = cv.create_line(pts[i][0], pad_t, pts[i][0],
                                          pad_t + plot_h, fill=GLASS_EDGE,
                                          width=1)
            cv.tag_lower(marker["id"])
            lesson = dict(self.LESSON_LABELS).get(t["lesson"], t["lesson"])
            readout.config(
                text=f"{t['when']} {t['clock']} · {t['design']} · {lesson} · "
                     f"scored {t['score']:.1f}/10"
                     + (f" at level {t['level']}" if t.get("level") else ""))

        def on_leave(_e):
            if marker["id"] is not None:
                cv.delete(marker["id"])
                marker["id"] = None
            readout.config(text=summary)

        cv.bind("<Motion>", on_move)
        cv.bind("<Leave>", on_leave)

    def _close_progress_dashboard(self):
        popup = getattr(self, "_progress_popup", None)
        if popup is None:
            return
        try: self.root.unbind_all("<MouseWheel>")
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self._progress_popup = None

    # ── Learn Mode: the persisted learner profile ────────────────────────────
    # Difficulty used to be a fixed student/robot alternation, identical on the
    # child's first session and their fiftieth. It is now driven by this file:
    # the attempts the vision model actually scored, and the level derived from
    # them. Same read/write pattern as the camera config — a small JSON file
    # next to the app, loaded in __init__, never allowed to break Learn Mode.

    def _load_learner_profile(self):
        """Restore the learner's level and scored history from disk.

        Runs from __init__, before the console exists, so problems stay silent
        — a missing or corrupt profile just means "a brand new learner", which
        must never stop Learn Mode from opening.
        """
        try:
            with open(LEARNER_PROFILE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            lvl = data.get("level")
            if isinstance(lvl, int) and 1 <= lvl <= LEARN_MAX_LEVEL:
                self._learn_level = lvl
            sessions = data.get("sessions")
            if isinstance(sessions, list):
                self._learn_sessions = [s for s in sessions
                                        if isinstance(s, dict)]
            solved = data.get("oneline_solved")
            if isinstance(solved, list):
                self._kid_oneline_solved = [str(d) for d in solved if d]
        except Exception:
            self._learn_level    = 1
            self._learn_sessions = []
            self._kid_oneline_solved = []

    def _save_learner_profile(self):
        try:
            with open(LEARNER_PROFILE_FILE, "w", encoding="utf-8") as f:
                json.dump({"version":  1,
                           "level":    self._learn_level,
                           "sessions": self._learn_sessions,
                           # evidence for the "One-Line Wizard" sticker
                           "oneline_solved": self._kid_oneline_solved},
                          f, indent=2)
            return True
        except OSError as e:
            self.log_to_console(
                f"Learn Mode: couldn't save the learner profile — {e}", "err")
            return False

    def _learn_level_meta(self, level=None):
        lvl = self._learn_level if level is None else level
        return LEARN_LEVELS.get(max(1, min(LEARN_MAX_LEVEL, lvl)),
                                LEARN_LEVELS[1])

    def _learn_is_pulli(self, level=None):
        """True at the graduation level, where the robot only lays dots."""
        return bool(self._learn_level_meta(level).get("pulli"))

    def _learn_level_label(self, level=None):
        lvl = self._learn_level if level is None else level
        return f"Level {lvl} · {self._learn_level_meta(lvl)['title']}"

    def _learn_scored_attempts(self, level=None):
        """AI-scored attempts, oldest first, normalised to a 0-10 score.

        Fallback verdicts are excluded on purpose. ``_learn_fallback_verdict``
        is a hardcoded 9/10 shown when there is no photo or no API key, and
        counting it would promote a child straight to Pulli Mode without them
        ever having drawn anything the model looked at.
        """
        out = []
        for s in self._learn_sessions:
            if s.get("scored_by") != "ai":
                continue
            if level is not None and s.get("level") != level:
                continue
            try:
                out_of = float(s.get("out_of") or 10) or 10.0
                out.append(float(s.get("score", 0)) * 10.0 / out_of)
            except (TypeError, ValueError):
                continue
        return out

    def _learn_recompute_level(self):
        """Move the learner up or down on the strength of their real scores.

        Promotion needs LEARN_LEVEL_WINDOW consecutive AI-scored attempts at
        the current level, all at or above LEARN_PROMOTE_SCORE; a demotion
        needs the same window all below LEARN_DEMOTE_SCORE. Anything in
        between holds the level, so one shaky rangoli never costs a child the
        progress they earned. Returns the new level.
        """
        old = self._learn_level
        window = self._learn_scored_attempts(level=old)[-LEARN_LEVEL_WINDOW:]
        if len(window) < LEARN_LEVEL_WINDOW:
            return old
        if old < LEARN_MAX_LEVEL and all(s >= LEARN_PROMOTE_SCORE for s in window):
            self._learn_level = old + 1
        elif old > 1 and all(s < LEARN_DEMOTE_SCORE for s in window):
            self._learn_level = old - 1
        return self._learn_level

    def _record_learn_session(self, verdict, scored_by):
        """Append one finished attempt to the profile and re-derive the level.

        ``scored_by`` is "ai" when the vision model judged a real photo and
        "sample" when the fallback verdict was displayed instead. Sample rows
        are still stored so the practice is visible, but they never feed the
        level (see _learn_scored_attempts).
        """
        photo = self._learn_photo_path
        if photo:
            try:
                photo = os.path.relpath(photo, _APP_DIR)
            except ValueError:          # different drive on Windows
                pass
        before = self._learn_level
        self._learn_sessions.append({
            "design":     verdict.get("name", "Rangoli"),
            "complexity": verdict.get("complexity", ""),
            "score":      verdict.get("score", 0),
            "out_of":     verdict.get("out_of", 10),
            "scored_by":  scored_by,
            "lesson":     self._learn_lesson,
            "level":      before,
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
            "photo":      photo or "",
            "improvements": list(verdict.get("improvements", []))[:3],
        })
        after = self._learn_recompute_level()
        self._save_learner_profile()

        if scored_by != "ai":
            self._learn_level_note = (
                f"Practice saved. {self._learn_level_label()} unchanged — only "
                f"AI-scored photos move the level.")
        elif after > before:
            self._learn_level_note = (
                "\U0001f389 Moved up to " + self._learn_level_label() +
                (" — the robot now lays only the dots!"
                 if self._learn_is_pulli() else
                 " — the robot will draw less next time."))
        elif after < before:
            self._learn_level_note = (
                "Back to " + self._learn_level_label() +
                " — the robot will draw more next time.")
        elif after >= LEARN_MAX_LEVEL:
            self._learn_level_note = (
                "You're in Pulli Mode — the last level. The robot lays the "
                "dots, you draw the rangoli.")
        else:
            # Promotion needs a consecutive run, so report the run still to go.
            run = 0
            for s in reversed(self._learn_scored_attempts(level=after)):
                if s < LEARN_PROMOTE_SCORE:
                    break
                run += 1
            need = max(1, LEARN_LEVEL_WINDOW - run)
            self._learn_level_note = (
                f"Still on {self._learn_level_label()} — {need} more rangoli "
                f"scored {int(LEARN_PROMOTE_SCORE)}+ to move up.")
        self.log_to_console(
            f"Learn Mode: recorded {verdict.get('score')}/"
            f"{verdict.get('out_of')} on '{verdict.get('name')}' "
            f"({scored_by}) at level {before} → now level {after}.", "info")

    # ── Learn Mode: who draws which part ─────────────────────────────────────

    @staticmethod
    def _learn_spread(count, start, length):
        """``count`` indices spaced evenly inside ``[start, start + length)``.

        Each index sits at the centre of its own equal block, so the picks are
        interleaved with the gaps between them instead of hugging either end.
        With count <= length the step is >= 1, which makes the results
        distinct.
        """
        if count <= 0 or length <= 0:
            return set()
        count = min(count, length)
        return {start + int((i + 0.5) * length / count) for i in range(count)}

    def _learn_build_plan(self):
        """Assign every part to the robot or the student, from the level.

        The robot's share comes straight from LEARN_LEVELS: ~70% at Level 1
        down to nothing at Level 5. Two properties matter beyond the raw split:

        * The robot always takes part 0 whenever it draws at all, so the child
          sees one worked example before being asked to copy anything.
        * Whichever of the two has *fewer* parts is the one spread out over the
          design. Spreading the majority is what bunches the minority into a
          block at the far end — at Level 1 that would mean the child watches
          the robot draw seven parts in a row and only then starts drawing.
        * The child always keeps at least one part. A one-part design at 70%
          rounds to "the robot draws all of it", which would walk the child
          straight to the camera step to be scored on the robot's own work.
          Where there is only one part it goes to the child and the robot
          demonstrates nothing — no demo is better than no drawing.
        """
        n = len(self._learn_parts)
        share = self._learn_level_meta().get("robot_share", 0.0)
        k = max(0, min(n, int(round(n * share))))
        if n >= 1:
            k = min(k, n - 1)
        if k <= 0:
            picks = set()
        elif k >= n:
            picks = set(range(n))
        else:
            picks = {0}                     # the demonstration part
            robot_rest, student_rest = k - 1, n - k
            if robot_rest <= student_rest:
                picks |= self._learn_spread(robot_rest, 1, n - 1)
            else:
                student = self._learn_spread(student_rest, 1, n - 1)
                picks |= {i for i in range(1, n) if i not in student}
        self._learn_owner = {i: ("robot" if i in picks else "student")
                             for i in range(n)}
        return picks

    def _learn_take_next(self):
        """Claim the next unstarted part; return ``(idx, owner)``.

        ``(None, None)`` once the design is exhausted. Ownership is read off
        the level's plan instead of alternating, which is what makes the
        difficulty progressive — see _learn_build_plan.
        """
        if self._learn_next_free >= len(self._learn_parts):
            return None, None
        idx = self._learn_next_free
        self._learn_next_free += 1
        return idx, self._learn_owner.get(idx, "student")

    def _learn_advance(self, hint=None):
        """Hand the next part to whoever the level's plan gives it to.

        Single entry point for every turn change, so the student and the robot
        can never both hold a part and the lesson always ends on the camera
        step. ``hint`` is only surfaced when the next part is the student's.
        """
        idx, owner = self._learn_take_next()
        if idx is None:
            self._open_learn_camera_step()
            return
        if owner == "robot":
            self._learn_student_idx = None
            self._learn_start_robot(idx)
            self._open_learn_robot_turn()
            self._kid_set_mood("watch")
        else:
            self._learn_robot_idx = None
            self._learn_student_idx = idx
            if hint:
                self.show_hint_popup(hint)
            self._open_learn_step()

    @staticmethod
    def _learn_part_color(idx):
        """(name, hex) colour assigned to part ``idx`` — cycles the palette."""
        return LEARN_PART_COLORS[idx % len(LEARN_PART_COLORS)]

    def _learn_tf_for(self, paths, cx, cy, size, flip=None):
        """Transform that fits ``paths`` into a ``size``-radius preview box.

        ``flip`` inverts the y axis. The preset generators hand back paths that
        have already been flipped into canvas space by _translate, and the
        previews have always flipped them back — harmless for the built-in
        designs, which are all vertically symmetric. A digitized notebook page
        is not symmetric, so pages coming out of the notebook or from a
        relative's app must be drawn the way they were photographed. Defaults
        to whatever the current lesson set (see ``_learn_flip_y``).
        """
        pts = [pt for p in paths for pt in p]
        if not pts:
            return None
        if flip is None:
            flip = getattr(self, "_learn_flip_y", True)
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        return (cx, cy, (size * 2) / span,
                (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                -1.0 if flip else 1.0)

    @staticmethod
    def _learn_map(tf, x, y):
        cx, cy, scale, mid_x, mid_y, ysign = tf
        return cx + (x - mid_x) * scale, cy + ysign * (y - mid_y) * scale

    def _learn_draw_preview(self, canvas, paths, cx, cy, size, flip=None):
        """Gallery thumbnail — every part in its own colour."""
        tf = self._learn_tf_for(paths, cx, cy, size, flip=flip)
        if tf is None:
            return
        for idx, path in enumerate(paths):
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in self._learn_map(tf, *pt)]
            canvas.create_line(flat, fill=self._learn_part_color(idx)[1],
                               width=2, smooth=True)

    def _learn_render_preview(self):
        """Redraw the lesson preview from the current state: your part bold and
        highlighted, finished parts solid, the rest ghosted grey."""
        prev, tf = self._learn_prev, self._learn_tf
        if prev is None or tf is None:
            return
        try:
            if not prev.winfo_exists():
                return
        except tk.TclError:
            return
        prev.delete("base")
        for idx, path in enumerate(self._learn_parts):
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in self._learn_map(tf, *pt)]
            hexc = self._learn_part_color(idx)[1]
            if idx == self._learn_student_idx:
                # Your part: a pale halo under a bold line so it pops out.
                prev.create_line(flat, fill=self._lighten(hexc, 70), width=11,
                                 smooth=True, capstyle="round", tags="base")
                prev.create_line(flat, fill=hexc, width=4, smooth=True,
                                 capstyle="round", tags="base")
            elif idx == self._learn_robot_idx:
                prev.create_line(flat, fill="#e5e7eb", width=2, smooth=True,
                                 tags="base")
            elif idx in self._learn_done_parts:
                prev.create_line(flat, fill=hexc, width=2, smooth=True,
                                 tags="base")
            else:
                prev.create_line(flat, fill="#d1d5db", width=1, smooth=True,
                                 tags="base")
        # Pulli Mode: once the scaffold is down it stays visible under every
        # step, because the dots are the only thing the child is working from.
        if self._learn_dots_laid:
            for dx, dy in self._learn_dots:
                x, y = self._learn_map(tf, dx, dy)
                prev.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ACCENT_AMBER,
                                 outline="#ffffff", width=1, tags="base")
        for tag in ("learn_dots", "learn_robot"):
            try: prev.tag_raise(tag)
            except tk.TclError: pass

    def _learn_status_text(self):
        if self._learn_robot_idx is not None:
            name = self._learn_part_color(self._learn_robot_idx)[0]
            return (f"\U0001f916 Robot is drawing part "
                    f"{self._learn_robot_idx + 1} ({name})… hold on, your next "
                    f"part comes up the moment it finishes.")
        # In Pulli Mode the robot owns no parts at all, so "waiting its turn"
        # would be a lie — its whole job is the dots. Gated on there actually
        # being a scaffold: a symmetry lesson can also run at Level 5.
        if self._learn_is_pulli() and self._learn_dots:
            if not self._learn_dots_laid:
                return (f"\U0001f916 Robot is laying the "
                        f"{len(self._learn_dots)} pulli… the lines are yours "
                        f"once the dots are down.")
            return ("\U0001f916 Dots are down — the robot is finished. Every "
                    "line from here is yours.")
        remaining = sum(1 for i in range(self._learn_next_free,
                                         len(self._learn_parts))
                        if self._learn_owner.get(i) == "robot")
        if remaining:
            return (f"\U0001f916 Robot is waiting its turn — {remaining} more "
                    f"part(s) to go. Press Done when your part is finished.")
        return "\U0001f916 Robot has finished all of its parts."

    def _learn_update_status(self):
        lbl = self._learn_status
        if lbl is None:
            return
        try:
            if lbl.winfo_exists():
                lbl.config(text=self._learn_status_text())
        except tk.TclError:
            pass

    def _open_learn_step(self):
        """The student's screen: the part they must draw right now."""
        idx = self._learn_student_idx
        if idx is None:
            if self._learn_robot_idx is not None:
                self._open_learn_robot_turn()
            else:
                self._open_learn_camera_step()
            return

        total = len(self._learn_parts)
        col_name, col_hex = self._learn_part_color(idx)
        W, H = S(640), S(590)
        nxt = self._learn_owner.get(self._learn_next_free)
        after = ("then press Done and the robot takes the next one"
                 if nxt == "robot" else
                 "then press Done and the next part is yours too"
                 if nxt else
                 "then press Done — that's the last part")
        popup, body = self._learn_shell(
            W, H,
            self.kid_pick(f"Your turn! Bit {idx + 1} of {total} 🎨",
                          f"Your turn — draw this part  ·  "
                          f"{self._learn_level_label()}"),
            self.kid_pick(
                f"Copy the bright {col_name.lower()} bit with your magic "
                f"bottle. Take your time — then tap the big green button!",
                f"Part {idx + 1} of {total} of '{self._learn_design}' — draw "
                f"the highlighted {col_name.lower()} part with your bottle, "
                f"{after}."),
            outline=KID_THEME["outline"] if self.kid_mode else ACCENT_GREEN,
            mascot="idle")

        upper = tk.Frame(body, bg=BG_CARD)
        upper.pack(fill="both", expand=True)

        prev = tk.Canvas(upper, width=S(210), height=S(210), bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(side="left", anchor="n", padx=(S(0), S(16)), pady=(S(2), S(0)))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 105, 105, 92)
        self._learn_render_preview()

        right = tk.Frame(upper, bg=BG_CARD)
        right.pack(side="left", fill="both", expand=True)

        # Colour chip — which powder to load in the bottle for this part.
        chip = tk.Frame(right, bg=BG_CARD)
        chip.pack(fill="x", anchor="w", pady=(S(0), S(8)))
        sw = tk.Canvas(chip, width=S(18), height=S(18), bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(S(0), S(8)))
        sw.create_oval(2, 2, 16, 16, fill=col_hex, outline="#ffffff", width=1)
        tk.Label(chip, text=f"Your part: {col_name} powder", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", FS(11), "bold")).pack(side="left")

        tk.Label(right, text=self.kid_pick("How to do it:", "How to draw it:"),
                 bg=BG_CARD, fg=ACCENT_GREEN,
                 font=("Segoe UI", FS(11), "bold")).pack(anchor="w", pady=(S(0), S(6)))
        for i, line in enumerate(self._learn_step_instructions(idx), 1):
            r = tk.Frame(right, bg=BG_CARD)
            r.pack(fill="x", anchor="w", pady=(S(0), S(5)))
            tk.Label(r, text=str(i), bg=ACCENT_GREEN, fg="#06281c",
                     font=("Segoe UI", FS(9), "bold"), width=S(2)).pack(
                side="left", anchor="n", padx=(S(0), S(8)))
            tk.Label(r, text=line, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(10)), justify="left",
                     wraplength=W-330).pack(side="left", anchor="w")

        footer = tk.Frame(body, bg=BG_CARD)
        footer.pack(fill="x", side="bottom", pady=(S(8), S(0)))
        pick = tk.Label(footer, text="← Pick another design", bg=BG_CARD,
                        fg=TEXT_DIM, font=("Segoe UI", FS(9), "bold"), cursor="hand2")
        pick.pack(side="left")
        pick.bind("<Button-1>", lambda e: self._open_learn_gallery())
        basics = tk.Label(footer, text="Re-read the 10 basics", bg=BG_CARD,
                          fg=TEXT_DIM, font=("Segoe UI", FS(9), "bold"), cursor="hand2")
        basics.pack(side="right")
        basics.bind("<Button-1>", lambda e: self._show_learn_intro())

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom", pady=(S(10), S(0)))
        self._color_button(
            btns, self.kid_pick("✓ I did it!", "✓ Done drawing my part"),
            self._learn_done_my_part, ACCENT_GREEN,
            width=S(250), height=S(46), font_size=FS(13)).pack(side="left")

        self._learn_status = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_PURP,
            font=("Segoe UI", FS(10), "bold"), anchor="w", justify="left",
            wraplength=W-70)
        self._learn_status.pack(fill="x", side="bottom", pady=(S(10), S(0)))
        self._learn_update_status()

    def _open_learn_robot_turn(self):
        """The robot's turn — the student watches until it finishes."""
        idx = self._learn_robot_idx
        if idx is None:
            self._open_learn_camera_step()
            return
        total = len(self._learn_parts)
        col_name, col_hex = self._learn_part_color(idx)
        W, H = S(520), S(470)
        _, body = self._learn_shell(
            W, H, "Robot's turn — watch",
            f"Part {idx + 1} of {total} — the robot is drawing its "
            f"{col_name.lower()} part. Watch how the line flows; your next "
            f"part appears as soon as it finishes.", outline=ACCENT_PURP)

        prev = tk.Canvas(body, width=S(210), height=S(210), bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(pady=(S(2), S(12)))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 105, 105, 92)
        self._learn_render_preview()

        chip = tk.Frame(body, bg=BG_CARD)
        chip.pack(pady=(S(0), S(8)))
        sw = tk.Canvas(chip, width=S(18), height=S(18), bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(S(0), S(8)))
        sw.create_oval(2, 2, 16, 16, fill=col_hex, outline="#ffffff", width=1)
        tk.Label(chip, text=f"Robot's part: {col_name}", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", FS(11), "bold")).pack(side="left")

        self._learn_status = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_PURP,
            font=("Segoe UI", FS(10), "bold"), wraplength=W-70, justify="center")
        self._learn_status.pack()
        self._learn_update_status()

        self._learn_animate_robot(idx)

    def _learn_step_instructions(self, step):
        sets = LEARN_STEP_SETS_KID if self.kid_mode else LEARN_STEP_SETS
        return sets[step % len(sets)]

    def _learn_done_my_part(self):
        """Student finished their part — move on to whoever owns the next one.

        Whether that is the robot or another part for the student is decided by
        the level's plan, not by alternation: at Level 1 the robot takes most of
        the follow-ups, and in Pulli Mode the student simply carries straight on
        to their next part.
        """
        idx = self._learn_student_idx
        if idx is None:
            return
        self._learn_done_parts.add(idx)
        self._learn_student_idx = None
        self.log_to_console(f"Learn Mode: you finished part {idx + 1}.", "recv")
        done, total = len(self._learn_done_parts), len(self._learn_parts)
        # Advance first, then celebrate. _learn_advance destroys this popup to
        # build the next one, and confetti drawn on a destroyed canvas is gone
        # before anybody sees it — so the burst has to land on the new screen.
        self._learn_advance()
        self._kid_celebrate(
            "That's the whole rangoli — amazing!" if done >= total else
            f"Part {done} of {total} done! ⭐")

    def _learn_start_robot(self, idx):
        """Robot begins drawing part ``idx`` — for real if a port is connected,
        and always as an animated trace in the preview."""
        self._learn_robot_idx = idx
        name = self._learn_part_color(idx)[0]
        self._learn_streaming = False
        if self.port_var.get() and not self.is_sending:
            part = self._learn_parts[idx]
            _SPEED_MAP = {"Aqua Low": 50, "Super Low": 100, "Low (default)": 150, "Medium": 200, "High": 250}
            f = _SPEED_MAP.get(self.feed_rate.get(), 150)
            lines = ["$X", "G21", "G90", f"F{f}"]
            lines += self._paths_gcode_lines([part], f)
            lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0"]
            self._pending_raw_gcode = lines
            self._on_send_complete = self._learn_robot_finished
            self._learn_streaming = True
            self.log_to_console(
                f"Learn Mode: robot starting part {idx + 1} ({name}).", "info")
            self.start_gcode_streaming()
        else:
            why = ("robot is busy with another job"
                   if self.port_var.get() else "no robot connected")
            self.log_to_console(
                f"Learn Mode: {why} — tracing part {idx + 1} ({name}) on "
                f"screen instead.", "info")

    def _learn_animate_robot(self, idx):
        """Trace the robot's current part into the preview while it draws."""
        if self._learn_anim_id is not None:
            try: self.root.after_cancel(self._learn_anim_id)
            except Exception: pass
            self._learn_anim_id = None
        if self._learn_prev is None or self._learn_tf is None:
            return
        self._learn_prev.delete("learn_robot")
        self._learn_cur_pts = [self._learn_map(self._learn_tf, x, y)
                               for x, y in self._learn_parts[idx]]
        self._learn_cur_col = self._learn_part_color(idx)[1]
        self._learn_anim_step(1, max(1, len(self._learn_cur_pts) // 45))

    def _learn_anim_step(self, j, step):
        prev = self._learn_prev
        try:
            alive = prev is not None and prev.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._learn_anim_id = None
            return
        pts = self._learn_cur_pts
        prev.delete("learn_robot")
        flat = [c for pt in pts[:j + 1] for c in pt]
        if len(flat) >= 4:
            prev.create_line(flat, fill=self._learn_cur_col, width=3,
                             smooth=True, capstyle="round", tags="learn_robot")
        if j < len(pts) - 1:
            nxt = min(j + step, len(pts) - 1)
            self._learn_anim_id = self.root.after(
                16, lambda: self._learn_anim_step(nxt, step))
        else:
            self._learn_anim_id = None
            # With no robot attached the on-screen trace IS the robot's turn.
            if not self._learn_streaming:
                self._learn_robot_finished()

    def _learn_robot_finished(self, ok=True):
        """Called when the robot's part ends. ``ok`` is the verdict read off the
        GRBL log — the student's next part is only revealed on a clean finish.
        """
        idx = self._learn_robot_idx
        if idx is None:
            return
        self._learn_streaming = False
        if not ok:
            self.log_to_console(
                f"Learn Mode: part {idx + 1} did NOT complete — see the GRBL "
                f"log. Holding the lesson here.", "err")
            self._open_learn_robot_error(idx)
            return

        self._learn_done_parts.add(idx)
        self._learn_robot_idx = None
        self.log_to_console(f"Learn Mode: robot finished part {idx + 1}.", "recv")
        self._learn_advance(hint="Robot's done — your turn!")

    def _open_learn_robot_error(self, idx):
        """The robot's part did not complete — never advance the student on a
        failed print; let them retry it or take the part over by hand."""
        col_name = self._learn_part_color(idx)[0]
        W, H = S(520), S(340)
        _, body = self._learn_shell(
            W, H, "Robot didn't finish that part",
            f"GRBL reported a problem part-way through part {idx + 1} "
            f"({col_name.lower()}). Check the Log for the exact error, make "
            f"sure the robot is connected and unclogged, then retry.",
            outline=ACCENT_PINK)

        def _retry():
            self._learn_start_robot(idx)
            self._open_learn_robot_turn()

        def _take_over():
            # Student draws the robot's part themselves and carries on.
            self._learn_robot_idx = None
            self._learn_student_idx = idx
            self.log_to_console(
                f"Learn Mode: you're taking over part {idx + 1}.", "info")
            self._open_learn_step()

        self._color_button(
            body, "↻ Try this part again", _retry,
            ACCENT_PURP, width=W-52, height=S(44), font_size=FS(12)).pack(pady=(S(10), S(8)))
        self._color_button(
            body, "I'll draw this part myself", _take_over,
            ACCENT_GREEN, width=W-52, height=S(44), font_size=FS(12)).pack()

    # ── Learn Mode: one-continuous-line visualisation ─────────────────────────
    # A sikku (kambi) kolam is the claim that a whole design can be drawn as one
    # unbroken line without lifting the bottle. That is a graph-theory claim, and
    # this is it on screen: treat the design as a multigraph — stroke endpoints
    # welded together are the vertices, the strokes themselves are the edges —
    # then run the Euler test and animate the route it produces.
    #
    # The counter reports what the route ACTUALLY does. If a design needs four
    # lines and three lifts, it says four lines and three lifts, and the panel
    # says why (disconnected rings, or too many odd-degree vertices). A hardcoded
    # "one line · 0 lifts" would be a fabricated result, which is worthless as
    # evidence — the honest number and the reason for it is the real
    # demonstration, and it is the same maths either way.

    def _sikku_analyse(self, parts):
        """Build the multigraph for ``parts`` and run the Euler test.

        Returns a dict, or None when there is nothing to analyse. Vertices are
        stroke endpoints welded within a tolerance scaled to the design, because
        two strokes that meet at a petal tip are one junction even though their
        float coordinates differ in the last few decimals.
        """
        strokes = [[(float(x), float(y)) for x, y in p]
                   for p in parts if len(p) >= 2]
        if not strokes:
            return None

        pts = [pt for s in strokes for pt in s]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        tol = max(1.0, span * SIKKU_WELD_FRAC)

        verts = []
        def vid(pt):
            for i, v in enumerate(verts):
                if math.dist(pt, v) <= tol:
                    return i
            verts.append(pt)
            return len(verts) - 1

        # edges[i] = (vertex_a, vertex_b, stroke_index)
        edges = [(vid(s[0]), vid(s[-1]), i) for i, s in enumerate(strokes)]

        deg = [0] * len(verts)
        adj = {i: [] for i in range(len(verts))}
        for ei, (a, b, _) in enumerate(edges):
            deg[a] += 1
            deg[b] += 1          # a == b (a closed stroke) correctly scores 2
            adj[a].append((b, ei))
            if a != b:
                adj[b].append((a, ei))
            else:
                # A self-loop is incident twice, so it must be walkable from
                # either "side" or Hierholzer can miss it.
                adj[a].append((b, ei))

        # connected components over vertices that actually carry an edge
        seen, comps = set(), []
        for v in range(len(verts)):
            if v in seen or deg[v] == 0:
                continue
            stack, comp = [v], []
            seen.add(v)
            while stack:
                u = stack.pop()
                comp.append(u)
                for (o, _) in adj[u]:
                    if o not in seen:
                        seen.add(o)
                        stack.append(o)
            comps.append(comp)

        # Euler: a connected component with 2k odd-degree vertices splits into
        # max(1, k) open trails; with none it closes into a single circuit.
        odd_total = sum(1 for d in deg if d % 2)
        min_trails = 0
        for comp in comps:
            odd = sum(1 for v in comp if deg[v] % 2)
            min_trails += max(1, odd // 2)

        if not comps:
            verdict, why = "none", "This design has no strokes to trace."
        elif len(comps) == 1 and odd_total == 0:
            verdict = "circuit"
            why = ("Every junction has an even number of lines meeting it, and "
                   "the whole design is connected — so it closes into one "
                   "unbroken loop. This is a true sikku kolam.")
        elif len(comps) == 1 and odd_total == 2:
            verdict = "path"
            why = ("Exactly two junctions have an odd number of lines meeting "
                   "them, so one unbroken line works — it just starts at one "
                   "of those two and ends at the other.")
        elif len(comps) == 1:
            verdict = "multi"
            why = (f"{odd_total} junctions have an odd number of lines meeting "
                   f"them. Euler's rule allows at most 2, so this design needs "
                   f"{min_trails} separate lines — one for each pair.")
        else:
            verdict = "split"
            why = (f"The design falls into {len(comps)} pieces that never touch "
                   f"each other, so the bottle has to be lifted between them. "
                   f"{min_trails} lines in total.")

        return {"strokes": strokes, "edges": edges, "adj": adj, "deg": deg,
                "verts": verts, "components": len(comps), "odd": odd_total,
                "min_trails": min_trails, "verdict": verdict, "why": why,
                "tol": tol}

    @staticmethod
    def _sikku_route(g):
        """Walk the graph into as few continuous trails as possible.

        Hierholzer's algorithm, generalised: take a maximal walk, then splice
        any leftover closed loops into it at the first vertex that still has
        unused edges. Starting from an odd-degree vertex whenever one is left is
        what keeps the number of trails down to Euler's minimum — start in the
        middle of an odd-degree component and you strand edges and pay an extra
        lift for them.

        Returns [[(edge_index, from_vertex), …], …] — one list per trail.
        """
        edges, adj = g["edges"], g["adj"]
        used = [False] * len(edges)

        def live_deg(v):
            return sum(1 for (_, ei) in adj[v] if not used[ei])

        def walk_from(v):
            """Maximal walk of unused edges; returns (vertices, edge steps)."""
            path, steps = [v], []
            while True:
                nxt = next(((o, ei) for (o, ei) in adj[v] if not used[ei]), None)
                if nxt is None:
                    return path, steps
                o, ei = nxt
                used[ei] = True
                steps.append((ei, v))
                v = o
                path.append(v)

        trails = []
        while not all(used):
            starts = [v for v in adj if live_deg(v) > 0]
            if not starts:
                break
            odd = [v for v in starts if live_deg(v) % 2]
            start = odd[0] if odd else starts[0]

            path, steps = walk_from(start)
            # splice leftover loops in, so one trail absorbs as much as it can
            i = 0
            while i < len(path):
                u = path[i]
                if live_deg(u) > 0:
                    sub_path, sub_steps = walk_from(u)
                    if sub_path[-1] == u:                 # a closed loop: splice
                        path = path[:i + 1] + sub_path[1:] + path[i + 1:]
                        steps = steps[:i] + sub_steps + steps[i:]
                        continue                          # re-check this vertex
                    # Not a loop — it would break the trail's continuity. Give
                    # the edges back and let them become their own trail.
                    for ei, _ in sub_steps:
                        used[ei] = False
                i += 1
            trails.append(steps)
        return trails

    def _sikku_trail_points(self, g, trail):
        """Concatenate a trail's strokes into one point list, in walk order."""
        out = []
        for ei, from_v in trail:
            a, b, si = g["edges"][ei]
            stroke = g["strokes"][si]
            pts = stroke if from_v == a else list(reversed(stroke))
            if out and math.dist(out[-1], pts[0]) <= g["tol"]:
                pts = pts[1:]          # don't repeat the shared junction
            out.extend(pts)
        return out

    def _sikku_dot_hit_radius(self, dots_px):
        """How close the pen must pass for a dot to count as covered.

        Derived from the actual dot spacing on screen so it means the same thing
        on a dense grid and a sparse one.
        """
        if len(dots_px) < 2:
            return 6.0
        near = []
        for i, a in enumerate(dots_px):
            d = min((math.dist(a, b) for j, b in enumerate(dots_px) if j != i),
                    default=0.0)
            if d > 0:
                near.append(d)
        if not near:
            return 6.0
        near.sort()
        return max(4.0, near[len(near) // 2] * SIKKU_DOT_HIT_FRAC / 2.0)

    def _sikku_counter_text(self):
        """The live counter — "one line · 214 dots · 0 lifts".

        Every number is read off the route being animated, not asserted.
        """
        started = min(self._sikku_lifts + 1, max(1, len(self._sikku_trails)))
        lines = ("one line" if started == 1 else f"{started} lines")
        hit, total = len(self._sikku_dots_hit), len(self._sikku_dots)
        dots = (f"{total} dots" if hit >= total and total
                else f"{hit} of {total} dots")
        lifts = f"{self._sikku_lifts} lift" + ("" if self._sikku_lifts == 1 else "s")
        return f"{lines}  ·  {dots}  ·  {lifts}"

    def _sikku_update_counter(self):
        lbl = self._sikku_counter
        if lbl is None:
            return
        try:
            if lbl.winfo_exists():
                lbl.configure(text=self._sikku_counter_text())
        except tk.TclError:
            self._sikku_counter = None

    def _open_learn_oneline(self):
        """Animate the design as the fewest continuous lines Euler allows,
        with a live counter and the graph reasoning beside it."""
        g = self._sikku_analyse(self._learn_parts)
        if g is None:
            self.show_hint_popup("Nothing in that design to trace")
            self._open_learn_gallery()
            return
        trails = self._sikku_route(g)
        self._sikku_g, self._sikku_trails = g, trails

        one = len(trails) == 1
        # "One-Line Wizard" is earned by actually finding a design that draws in
        # one line, so record the evidence the sticker rule reads.
        if one and self._learn_design and \
                self._learn_design not in self._kid_oneline_solved:
            self._kid_oneline_solved.append(self._learn_design)
            self._save_learner_profile()
            self._kid_sound("badge")

        W, H = S(720), S(600)
        _, body = self._learn_shell(
            W, H,
            self.kid_pick("The magic one-line trick", "One continuous line"),
            self.kid_pick(
                f"Can '{self._learn_design}' be drawn without lifting your "
                f"magic bottle? Let's find out! Watch the little bottle walk "
                f"the line.",
                f"'{self._learn_design}' traced as the fewest unbroken lines "
                f"that are mathematically possible. Watch where the line has "
                f"to stop."),
            outline=ACCENT_GREEN if one else ACCENT_AMBER,
            mascot="cheer" if one else "watch")
        if self.kid_mode:
            self._kid_set_mood(
                "cheer" if one else "watch",
                "One line, no lifting — that's the magic trick! 🪄" if one else
                f"This one needs {len(trails)} lines. Watch where it has to "
                f"stop!")

        upper = tk.Frame(body, bg=BG_CARD)
        upper.pack(fill="both", expand=True)

        PV = S(300)
        prev = tk.Canvas(upper, width=PV, height=PV, bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(side="left", anchor="n", padx=(S(0), S(16)))
        self._sikku_prev = prev
        half = PV / 2.0
        tf = self._learn_tf_for(self._learn_parts, half, half, half * 0.88)
        self._sikku_tf = tf

        # ghost of the finished design, so the highlight reads as progress
        for path in self._learn_parts:
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in self._learn_map(tf, *pt)]
            prev.create_line(flat, fill="#dcdce6", width=2, smooth=True)

        # the pulli, same scaffold Pulli Mode lays
        self._learn_dots = self._learn_pulli_dots()
        self._sikku_dots = [self._learn_map(tf, x, y) for x, y in self._learn_dots]
        self._sikku_dot_r = self._sikku_dot_hit_radius(self._sikku_dots)
        self._sikku_dots_hit = set()
        for dx, dy in self._sikku_dots:
            prev.create_oval(dx - 2.5, dy - 2.5, dx + 2.5, dy + 2.5,
                             fill="#b9b9c8", outline="", tags="sikku_dot")

        # ── the graph panel: the maths, in words ──────────────────────────────
        right = tk.Frame(upper, bg=BG_CARD)
        right.pack(side="left", fill="both", expand=True)

        verdict_txt = ("✓ One unbroken line — a true sikku kolam"
                       if one else
                       f"✗ Needs {len(trails)} lines, {len(trails) - 1} lifts")
        tk.Label(right, text=verdict_txt, bg=BG_CARD,
                 fg=ACCENT_GREEN if one else ACCENT_AMBER,
                 font=("Segoe UI", FS(12), "bold"), justify="left",
                 wraplength=W-PV-90).pack(anchor="w", pady=(S(0), S(6)))
        tk.Label(right, text=g["why"], bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", FS(9)), justify="left",
                 wraplength=W-PV-90).pack(anchor="w", pady=(S(0), S(10)))

        tk.Label(right, text="The graph", bg=BG_CARD, fg=ACCENT_CYAN,
                 font=("Segoe UI", FS(10), "bold")).pack(anchor="w")
        rows = [
            ("Junctions (vertices)", str(len(g["verts"]))),
            ("Strokes (edges)",      str(len(g["edges"]))),
            ("Connected pieces",     str(g["components"])),
            ("Odd-degree junctions", str(g["odd"])),
            ("Euler's minimum",      f"{g['min_trails']} line(s)"),
            ("This route walks",     f"{len(trails)} line(s)"),
        ]
        for k, v in rows:
            r = tk.Frame(right, bg=BG_CARD)
            r.pack(fill="x", anchor="w")
            tk.Label(r, text=k, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", FS(9))).pack(side="left")
            tk.Label(r, text=v, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(9), "bold")).pack(side="right")

        tk.Label(right,
                 text="Euler's rule: a shape can be drawn without lifting only "
                      "if it is all one piece and at most two junctions have an "
                      "odd number of lines meeting them.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(8)),
                 justify="left", wraplength=W-PV-90).pack(anchor="w",
                                                          pady=(S(10), 0))

        # ── the live counter ─────────────────────────────────────────────────
        self._sikku_lifts = 0
        self._sikku_counter = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_GREEN,
            font=("Consolas", FS(15), "bold"), anchor="w")
        self._sikku_counter.pack(fill="x", side="bottom", pady=(S(8), S(0)))

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom", pady=(S(10), S(0)))
        self._sikku_play_btn = self._color_button(
            btns, "⏸ Pause", self._sikku_toggle_play, ACCENT_PURP,
            width=S(150), height=S(40), font_size=FS(11))
        self._sikku_play_btn.pack(side="left")
        self._color_button(
            btns, "↻ Replay", self._sikku_replay, ACCENT_CYAN,
            width=S(140), height=S(40), font_size=FS(11)).pack(side="left",
                                                               padx=S(8))
        back = tk.Label(btns, text="← Pick another design", bg=BG_CARD,
                        fg=TEXT_DIM, font=("Segoe UI", FS(9), "bold"),
                        cursor="hand2")
        back.pack(side="right")
        back.bind("<Button-1>", lambda e: self._open_learn_gallery())

        self.log_to_console(
            f"One-line view: '{self._learn_design}' — V={len(g['verts'])}, "
            f"E={len(g['edges'])}, pieces={g['components']}, "
            f"odd={g['odd']} → {len(trails)} line(s), {len(trails) - 1} lift(s) "
            f"[{g['verdict']}].", "info")
        self._sikku_start()

    def _sikku_start(self):
        """Build the frame stream in preview space and begin animating."""
        g, trails, tf = self._sikku_g, self._sikku_trails, self._sikku_tf
        if not (g and trails and tf):
            return
        # Map to preview coordinates first, then densify, so the pen moves at an
        # even speed on screen rather than in design millimetres.
        strokes = [([self._learn_map(tf, x, y)
                     for x, y in self._sikku_trail_points(g, t)], True)
                   for t in trails]
        self._sikku_frames = self._frames_from_strokes(strokes, max_frames=2600)
        self._sikku_i = 0
        self._sikku_last = None
        self._sikku_lifts = 0
        self._sikku_dots_hit = set()
        self._sikku_running = True
        self._kid_clear_sparkles()
        try:
            self._sikku_prev.delete("sikku_trail")
            self._sikku_prev.delete("sikku_pen")
            self._sikku_prev.delete("sikku_spark")
        except tk.TclError:
            pass
        self._sikku_update_counter()
        self._sikku_tick()

    def _sikku_stop(self):
        self._sikku_running = False
        aid = self._sikku_after
        if aid is not None:
            try: self.root.after_cancel(aid)
            except Exception: pass
            self._sikku_after = None

    def _sikku_toggle_play(self):
        if self._sikku_running:
            self._sikku_stop()
            label = "▶ Play"
        else:
            if self._sikku_i >= len(self._sikku_frames):
                self._sikku_start()
            else:
                self._sikku_running = True
                self._sikku_tick()
            label = "⏸ Pause"
        try:
            self._sikku_play_btn.configure(text=label)
        except (tk.TclError, AttributeError):
            pass

    def _sikku_replay(self):
        self._sikku_stop()
        self._sikku_start()
        try:
            self._sikku_play_btn.configure(text="⏸ Pause")
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def _sikku_ramp(t):
        """Cyan → purple → pink → amber along the route, so the *order* the line
        is drawn in is visible and not just its shape."""
        stops = ["#22d3ee", "#a78bfa", "#f472b6", "#f97316"]
        t = max(0.0, min(0.999999, t)) * (len(stops) - 1)
        i = int(t)
        f = t - i
        a = stops[i].lstrip("#")
        b = stops[i + 1].lstrip("#")
        return "#%02x%02x%02x" % tuple(
            int(int(a[k:k+2], 16) + (int(b[k:k+2], 16) - int(a[k:k+2], 16)) * f)
            for k in (0, 2, 4))

    def _sikku_tick(self):
        """One frame of the line animation, counter included."""
        if not self._sikku_running:
            return
        prev = self._sikku_prev
        try:
            alive = prev is not None and prev.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._sikku_stop()
            return

        frames = self._sikku_frames
        i = self._sikku_i
        if i >= len(frames):
            self._sikku_running = False
            self._sikku_after = None
            try:
                prev.delete("sikku_pen")
            except tk.TclError:
                pass
            self._sikku_update_counter()
            try:
                self._sikku_play_btn.configure(text="↻ Replay")
            except (tk.TclError, AttributeError):
                pass
            n = len(self._sikku_trails)
            self.log_to_console(
                f"One-line view: finished — {self._sikku_counter_text()}",
                "recv" if n == 1 else "info")
            return

        pt = frames[i]
        self._sikku_i = i + 1

        if pt is None:
            # A pen lift: the line genuinely stops here. Count it and mark the
            # break on screen, because the break is the point of the lesson.
            self._sikku_lifts += 1
            if self._sikku_last is not None:
                lx, ly = self._sikku_last
                prev.create_oval(lx - 4, ly - 4, lx + 4, ly + 4,
                                 outline=ACCENT_PINK, width=2, fill="",
                                 tags="sikku_trail")
            self._sikku_last = None
            self._sikku_update_counter()
            self._sikku_after = self.root.after(140, self._sikku_tick)
            return

        x, y = pt
        if self._sikku_last is not None:
            prev.create_line(self._sikku_last[0], self._sikku_last[1], x, y,
                             fill=self._sikku_ramp(i / max(1, len(frames))),
                             width=4, capstyle="round", tags="sikku_trail")
        self._sikku_last = (x, y)

        # light up any pulli the line has now reached
        r = self._sikku_dot_r
        for di, (dx, dy) in enumerate(self._sikku_dots):
            if di not in self._sikku_dots_hit and math.dist((x, y), (dx, dy)) <= r:
                self._sikku_dots_hit.add(di)
                prev.create_oval(dx - 4, dy - 4, dx + 4, dy + 4,
                                 fill=ACCENT_AMBER, outline="#ffffff", width=1,
                                 tags="sikku_dot")

        try:
            if self.kid_mode:
                if i % 4 == 0:
                    self._kid_sparkle(prev, x, y, tag="sikku_spark")
                self._kid_draw_buddy(prev, x, y, i, tag="sikku_pen", scale=0.8)
            else:
                prev.delete("sikku_pen")
                prev.create_oval(x - 6, y - 6, x + 6, y + 6, fill=ACCENT_GREEN,
                                 outline="#ffffff", width=2, tags="sikku_pen")
            prev.tag_raise("sikku_dot")
            prev.tag_raise("sikku_pen")
        except tk.TclError:
            pass

        self._sikku_update_counter()
        self._sikku_after = self.root.after(8, self._sikku_tick)

    # ── Learn Mode: symmetry challenges ───────────────────────────────────────
    # See LEARN_SYMMETRY_BY_LEVEL for why this lesson type exists. The robot
    # draws one fundamental domain of a part; the child draws its reflections,
    # with the mirror lines and the target outlines both on screen.

    def _learn_symmetry_mode(self):
        """Mirror mode for this learner — more axes as the level rises."""
        return LEARN_SYMMETRY_BY_LEVEL.get(self._learn_level, "2-way")

    @staticmethod
    def _polyline_len(path):
        return sum(math.dist(path[i], path[i + 1])
                   for i in range(len(path) - 1))

    @classmethod
    def _clip_polyline(cls, path, inside, min_len):
        """The contiguous runs of ``path`` that lie inside a domain.

        A part can cross the mirror line several times (a ring crosses twice, a
        petal burst many more), so this returns a *list* of runs rather than one
        path. Runs shorter than ``min_len`` are dropped — clipping almost always
        leaves a stray point or two right on the axis, and they would otherwise
        become their own unreachable "draw this" target.
        """
        runs, cur = [], []
        for pt in path:
            if inside(*pt):
                cur.append(pt)
            else:
                if len(cur) >= 2 and cls._polyline_len(cur) >= min_len:
                    runs.append(cur)
                cur = []
        if len(cur) >= 2 and cls._polyline_len(cur) >= min_len:
            runs.append(cur)
        return runs

    def _learn_symmetry_pairs(self, mode=None):
        """Build the challenge: [(robot_half, mirrored_targets), …].

        For each part of the design, the robot's half is the piece of it inside
        the mode's fundamental domain, and the child's targets are that piece
        reflected by every transform of the mode. On a symmetric design the
        reflections land on the part's own other halves, so the child is
        completing the real rangoli and not an invented shape.

        Parts that don't reach into the domain are skipped: at 8-way a petal on
        the far side of the design has nothing in the octant, and asking the
        robot to draw "nothing" is not a lesson.
        """
        mode = mode or self._learn_symmetry_mode()
        cx, cy = self._learn_center
        inside = self._mirror_axis_test(mode, cx, cy)
        tfs = self._mirror_transforms(mode=mode, cx=cx, cy=cy)

        pts = [pt for p in self._learn_parts for pt in p]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        min_len = span * LEARN_SYM_MIN_LEN_FRAC

        pairs = []
        for idx, part in enumerate(self._learn_parts):
            runs = self._clip_polyline(part, inside, min_len)
            if not runs:
                continue
            pairs.append({
                "part":   idx,
                "robot":  runs,
                "target": [[f(x, y) for x, y in run]
                           for f in tfs for run in runs],
            })
            if len(pairs) >= LEARN_SYM_MAX_PAIRS:
                break
        return pairs

    def _learn_start_symmetry(self, name):
        """Begin a symmetry challenge on the chosen design."""
        mode = self._learn_symmetry_mode()
        pairs = self._learn_symmetry_pairs(mode)
        if not pairs and mode != "2-way":
            # An octant can miss a design's geometry entirely. One mirror line
            # always has something to work with, so fall back rather than
            # dead-end the child on an empty lesson.
            self.log_to_console(
                f"Learn Mode: '{name}' has nothing inside the {mode} wedge — "
                f"falling back to one mirror line.", "info")
            mode = "2-way"
            pairs = self._learn_symmetry_pairs(mode)
        if not pairs:
            self.show_hint_popup("That design has no symmetry to practise")
            self._open_learn_gallery()
            return

        self._learn_sym_mode  = mode
        self._learn_sym_pairs = pairs
        self._learn_sym_idx   = 0
        self.log_to_console(
            f"Learn Mode: symmetry challenge on '{name}' — {mode} "
            f"({LEARN_SYMMETRY_LABELS[mode]}), {len(pairs)} half/halves to "
            f"complete.", "info")
        self._open_learn_symmetry_robot_turn()

    def _learn_sym_pair(self):
        if 0 <= self._learn_sym_idx < len(self._learn_sym_pairs):
            return self._learn_sym_pairs[self._learn_sym_idx]
        return None

    def _learn_render_symmetry(self, show_target):
        """Draw the symmetry preview: the whole design ghosted, the mirror lines
        dashed through it, the robot's half bold, and — once the robot has
        finished — the child's target halves outlined so they can see the shape
        they are aiming for."""
        prev, tf = self._learn_prev, self._learn_tf
        pair = self._learn_sym_pair()
        if prev is None or tf is None or pair is None:
            return
        try:
            if not prev.winfo_exists():
                return
        except tk.TclError:
            return
        prev.delete("base")

        # the rest of the design, faint, for context
        for idx, path in enumerate(self._learn_parts):
            if len(path) < 2 or idx == pair["part"]:
                continue
            flat = [c for pt in path for c in self._learn_map(tf, *pt)]
            prev.create_line(flat, fill="#e8e8ef", width=1, smooth=True,
                             tags="base")

        # the mirror lines — the thing the lesson is actually about
        cx, cy = self._learn_center
        pts = [pt for p in self._learn_parts for pt in p]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        reach = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * 0.62
        for (a, b) in self._mirror_axis_lines(self._learn_sym_mode, cx, cy, reach):
            ax, ay = self._learn_map(tf, *a)
            bx, by = self._learn_map(tf, *b)
            prev.create_line(ax, ay, bx, by, fill=ACCENT_CYAN, width=1,
                             dash=(3, 3), tags="base")

        hexc = self._learn_part_color(pair["part"])[1]
        # the child's targets, outlined
        if show_target:
            for run in pair["target"]:
                flat = [c for pt in run for c in self._learn_map(tf, *pt)]
                prev.create_line(flat, fill=self._lighten(hexc, 45), width=3,
                                 smooth=True, capstyle="round", dash=(5, 4),
                                 tags="base")
        # the robot's half, solid
        for run in pair["robot"]:
            flat = [c for pt in run for c in self._learn_map(tf, *pt)]
            prev.create_line(flat, fill=hexc, width=4, smooth=True,
                             capstyle="round", tags="base")
        try: prev.tag_raise("learn_robot")
        except tk.TclError: pass

    def _open_learn_symmetry_robot_turn(self):
        """The robot draws its half; the child watches the mirror line."""
        pair = self._learn_sym_pair()
        if pair is None:
            self._open_learn_camera_step()
            return
        n, total = self._learn_sym_idx + 1, len(self._learn_sym_pairs)
        mode = self._learn_sym_mode
        W, H = S(560), S(520)
        _, body = self._learn_shell(
            W, H, f"Symmetry {n} of {total} — watch the robot's half",
            f"The dashed cyan line is the mirror. The robot is drawing the "
            f"{self._learn_part_color(pair['part'])[0].lower()} shape on one "
            f"side only — you'll draw its reflection across "
            f"{LEARN_SYMMETRY_LABELS[mode]}.", outline=ACCENT_PURP)

        prev = tk.Canvas(body, width=S(230), height=S(230), bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(pady=(S(2), S(10)))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 115, 115, 100)
        self._learn_render_symmetry(show_target=False)

        self._learn_status = tk.Label(
            body, text="\U0001f916 Robot is drawing its half…", bg=BG_CARD,
            fg=ACCENT_PURP, font=("Segoe UI", FS(10), "bold"),
            wraplength=W-70, justify="center")
        self._learn_status.pack(side="bottom")

        self._learn_start_symmetry_robot(pair)
        self._learn_animate_symmetry(pair)

    def _learn_start_symmetry_robot(self, pair):
        """Stream the robot's half to the machine, if one is connected."""
        self._learn_streaming = False
        if self.port_var.get() and not self.is_sending:
            _SPEED_MAP = {"Aqua Low": 50, "Super Low": 100,
                          "Low (default)": 150, "Medium": 200, "High": 250}
            f = _SPEED_MAP.get(self.feed_rate.get(), 150)
            lines = ["$X", "G21", "G90", f"F{f}"]
            lines += self._paths_gcode_lines(pair["robot"], f)
            lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0"]
            self._pending_raw_gcode = lines
            self._on_send_complete = self._learn_symmetry_robot_finished
            self._learn_streaming = True
            self.log_to_console(
                f"Learn Mode: robot drawing the symmetry half of part "
                f"{pair['part'] + 1}.", "info")
            self.start_gcode_streaming()
        else:
            why = ("robot is busy with another job"
                   if self.port_var.get() else "no robot connected")
            self.log_to_console(
                f"Learn Mode: {why} — tracing the symmetry half on screen "
                f"instead.", "info")

    def _learn_animate_symmetry(self, pair):
        """Trace the robot's half into the preview, run by run.

        Clipping a part to the mirror domain can leave several separate runs, so
        this cannot reuse _learn_anim_step: that draws one polyline through
        every point and would stitch the runs together with connector lines the
        robot never draws. It also completes into _learn_robot_finished, which
        is the wrong end of the lesson.
        """
        if self._learn_anim_id is not None:
            try: self.root.after_cancel(self._learn_anim_id)
            except Exception: pass
            self._learn_anim_id = None
        if self._learn_prev is None or self._learn_tf is None:
            return
        self._learn_prev.delete("learn_robot")
        self._learn_sym_runs = [
            [self._learn_map(self._learn_tf, x, y) for x, y in run]
            for run in pair["robot"] if len(run) >= 2]
        self._learn_cur_col = self._learn_part_color(pair["part"])[1]
        if not self._learn_sym_runs:
            if not self._learn_streaming:
                self._learn_symmetry_robot_finished()
            return
        total = sum(len(r) for r in self._learn_sym_runs)
        self._learn_sym_anim_step(0, 1, max(1, total // 45))

    def _learn_sym_anim_step(self, ri, j, step):
        prev = self._learn_prev
        try:
            alive = prev is not None and prev.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._learn_anim_id = None
            return
        runs = self._learn_sym_runs
        if ri >= len(runs):
            self._learn_anim_id = None
            return
        prev.delete("learn_robot")
        for done_run in runs[:ri]:               # runs already traced
            flat = [c for pt in done_run for c in pt]
            if len(flat) >= 4:
                prev.create_line(flat, fill=self._learn_cur_col, width=4,
                                 smooth=True, capstyle="round",
                                 tags="learn_robot")
        flat = [c for pt in runs[ri][:j + 1] for c in pt]
        if len(flat) >= 4:
            prev.create_line(flat, fill=self._learn_cur_col, width=4,
                             smooth=True, capstyle="round", tags="learn_robot")

        if j < len(runs[ri]) - 1:
            nxt = min(j + step, len(runs[ri]) - 1)
            self._learn_anim_id = self.root.after(
                16, lambda: self._learn_sym_anim_step(ri, nxt, step))
        elif ri + 1 < len(runs):
            self._learn_anim_id = self.root.after(
                120, lambda: self._learn_sym_anim_step(ri + 1, 1, step))
        else:
            self._learn_anim_id = None
            # With no robot attached the on-screen trace IS the robot's half.
            if not self._learn_streaming:
                self._learn_symmetry_robot_finished()

    def _learn_symmetry_robot_finished(self, ok=True):
        """Robot's half is down — now the child draws the reflection."""
        self._learn_streaming = False
        if not ok:
            self.log_to_console(
                "Learn Mode: the robot's symmetry half did NOT complete — see "
                "the GRBL log. Holding the lesson here.", "err")
            self._open_learn_symmetry_error()
            return
        self._open_learn_symmetry_step()

    def _open_learn_symmetry_error(self):
        """The robot's half failed — retry it or take it over by hand."""
        W, H = S(520), S(330)
        _, body = self._learn_shell(
            W, H, "The robot didn't finish its half",
            "GRBL reported a problem part-way through the robot's half of this "
            "symmetry challenge. Check the Log, make sure the robot is "
            "connected and unclogged, then retry.", outline=ACCENT_PINK)
        self._color_button(
            body, "↻ Draw that half again", self._open_learn_symmetry_robot_turn,
            ACCENT_PURP, width=W-52, height=S(44), font_size=FS(12)).pack(
                pady=(S(10), S(8)))
        self._color_button(
            body, "Carry on — I'll draw both halves", self._open_learn_symmetry_step,
            ACCENT_GREEN, width=W-52, height=S(44), font_size=FS(12)).pack()

    def _open_learn_symmetry_step(self):
        """The child's turn: draw the mirrored half/halves."""
        pair = self._learn_sym_pair()
        if pair is None:
            self._open_learn_camera_step()
            return
        n, total = self._learn_sym_idx + 1, len(self._learn_sym_pairs)
        mode = self._learn_sym_mode
        col_name, col_hex = self._learn_part_color(pair["part"])
        copies = len(pair["target"])
        W, H = S(650), S(600)
        _, body = self._learn_shell(
            W, H,
            self.kid_pick(f"Mirror time! {n} of {total} 🦋",
                          f"Your turn — mirror it  ·  "
                          f"{self._learn_level_label()}"),
            self.kid_pick(
                f"The robot drew the solid {col_name.lower()} bit. Draw the "
                f"same thing on the other side of the dotted mirror line — "
                f"{'there is 1 to do' if copies == 1 else f'there are {copies} to do'}!",
                f"Symmetry {n} of {total}. The robot's {col_name.lower()} half "
                f"is solid; the dashed outline is where your "
                f"{'reflection goes' if copies == 1 else f'{copies} reflections go'}"
                f". Draw across the cyan mirror line."),
            outline=KID_THEME["outline"] if self.kid_mode else ACCENT_CYAN,
            mascot="idle")

        upper = tk.Frame(body, bg=BG_CARD)
        upper.pack(fill="both", expand=True)

        prev = tk.Canvas(upper, width=S(240), height=S(240), bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(side="left", anchor="n", padx=(S(0), S(16)))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 120, 120, 104)
        self._learn_render_symmetry(show_target=True)

        right = tk.Frame(upper, bg=BG_CARD)
        right.pack(side="left", fill="both", expand=True)

        chip = tk.Frame(right, bg=BG_CARD)
        chip.pack(fill="x", anchor="w", pady=(S(0), S(8)))
        sw = tk.Canvas(chip, width=S(18), height=S(18), bg=BG_CARD,
                       highlightthickness=0)
        sw.pack(side="left", padx=(S(0), S(8)))
        sw.create_oval(2, 2, 16, 16, fill=col_hex, outline="#ffffff", width=1)
        tk.Label(chip, text=f"{col_name} powder · {mode}", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", FS(11), "bold")).pack(side="left")

        tk.Label(right,
                 text=self.kid_pick("How to do the mirror:", "How to mirror it:"),
                 bg=BG_CARD, fg=ACCENT_CYAN,
                 font=("Segoe UI", FS(11), "bold")).pack(anchor="w",
                                                        pady=(S(0), S(6)))
        for i, line in enumerate(self._learn_symmetry_instructions(mode), 1):
            r = tk.Frame(right, bg=BG_CARD)
            r.pack(fill="x", anchor="w", pady=(S(0), S(5)))
            tk.Label(r, text=str(i), bg=ACCENT_CYAN, fg="#06281c",
                     font=("Segoe UI", FS(9), "bold"), width=S(2)).pack(
                side="left", anchor="n", padx=(S(0), S(8)))
            tk.Label(r, text=line, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(10)), justify="left",
                     wraplength=W-340).pack(side="left", anchor="w")

        footer = tk.Frame(body, bg=BG_CARD)
        footer.pack(fill="x", side="bottom", pady=(S(8), S(0)))
        pick = tk.Label(footer, text="← Pick another design", bg=BG_CARD,
                        fg=TEXT_DIM, font=("Segoe UI", FS(9), "bold"),
                        cursor="hand2")
        pick.pack(side="left")
        pick.bind("<Button-1>", lambda e: self._open_learn_gallery())

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom", pady=(S(10), S(0)))
        last = self._learn_sym_idx >= len(self._learn_sym_pairs) - 1
        self._color_button(
            btns,
            self.kid_pick("✓ Check my mirror!", "✓ Done — check my symmetry")
            if last else
            self.kid_pick("✓ Done — next one!", "✓ Done — next half"),
            self._learn_done_symmetry_half, ACCENT_GREEN,
            width=S(260), height=S(46), font_size=FS(13)).pack(side="left")

    def _learn_symmetry_instructions(self, mode):
        """Guidance aimed at *producing* a mirror line, not recognising one."""
        if self.kid_mode:
            steps = [
                "Find the dotted blue line — that's your magic mirror. Lay a "
                "thread on it so you can really see it.",
                "Pick one easy spot on the robot's shape: a pointy tip or the "
                "fattest bit.",
                "Count how far that spot is from the mirror line, then count "
                "the SAME distance on your side and put a dot there.",
                "Do two or three more dots the same way. Dots first — lines "
                "after!",
                "Join your dots up, but bend your curve the OTHER way. Left "
                "becomes right in a mirror!",
            ]
            if mode != "2-way":
                steps.append(
                    "Finished one? Spin round to the next mirror line and do "
                    "it all again!")
            return steps
        steps = [
            "Find the cyan mirror line on the floor first — line it up with a "
            "tile edge or lay a thread down so you can see it.",
            "Pick one clear landmark on the robot's half: a tip, a corner, the "
            "widest point.",
            "Measure that landmark's distance from the mirror line, then mark "
            "the same distance on your side. That dot is where it belongs.",
            "Mark two or three more landmarks the same way before you draw any "
            "line — dots first, curves after.",
            "Now join your dots, curving the opposite way to the robot's half: "
            "a curve bending left mirrors to one bending right.",
        ]
        if mode != "2-way":
            steps.append(
                "Finish one reflection completely, then turn the same way "
                "round the centre and repeat it for the next mirror line.")
        return steps

    def _learn_done_symmetry_half(self):
        """Child finished this reflection — next challenge, or go get scored."""
        pair = self._learn_sym_pair()
        if pair is None:
            self._open_learn_camera_step()
            return
        self.log_to_console(
            f"Learn Mode: learner completed the mirrored half of part "
            f"{pair['part'] + 1}.", "recv")
        self._learn_sym_idx += 1
        n, total = self._learn_sym_idx, len(self._learn_sym_pairs)
        if self._learn_sym_idx >= len(self._learn_sym_pairs):
            self._open_learn_camera_step()
            self._kid_celebrate("Both sides match — you're a mirror master! 🦋")
            return
        self._open_learn_symmetry_robot_turn()
        self._kid_celebrate(f"Mirror {n} of {total} done! ⭐")

    # ── Learn Mode: Pulli Mode (the graduation level) ─────────────────────────
    # A pulli kolam is drawn around a scaffold of dots — the pulli. An
    # experienced hand doesn't want the lines demonstrated, only the dots put
    # down, and that is exactly what the robot is reduced to at Level 5. There
    # is no separate "you graduated" screen: reaching Level 5 *is* the
    # graduation, because the child is now using the robot the same way
    # grandma would.

    def _learn_pulli_dots(self):
        """The dot scaffold for the design currently being learned.

        Dots are sampled evenly along each part of the chosen rangoli rather
        than laid out as a generic lattice, so the child is connecting dots
        that actually belong to their own design. Near-coincident dots (where
        parts of the design overlap) are merged, and the whole set is capped so
        a many-part design doesn't turn into a dot storm.
        Returns canvas-space [(x, y), …].
        """
        parts = [p for p in self._learn_parts if len(p) >= 2]
        if not parts:
            return []

        pts = [pt for p in parts for pt in p]
        xs, ys = [x for x, _ in pts], [y for _, y in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        min_gap = span * 0.06          # scale-free: 6% of the design's extent

        per = max(2, min(PULLI_DOTS_PER_PART,
                         max(2, PULLI_MAX_DOTS // len(parts))))
        dots = []
        for path in parts:
            n = len(path)
            for j in range(per):
                x, y = path[round(j * (n - 1) / (per - 1))]
                if all((x - dx) ** 2 + (y - dy) ** 2 >= min_gap ** 2
                       for dx, dy in dots):
                    dots.append((x, y))
                if len(dots) >= PULLI_MAX_DOTS:
                    return dots
        return dots

    def _learn_dot_gcode_lines(self, dots, f):
        """G-code that dabs one dot of powder at each point.

        Same nozzle convention as _paths_gcode_lines — travel with the nozzle
        shut, open it on the spot, shut it again — so each pulli comes out as a
        dab rather than the start of a stroke.
        """
        lines = []
        for px, py in dots:
            mx, my = self.to_machine(px, py)
            lines += [
                f"G1 Z{NOZZLE_CLOSED_Z:.2f} F{f}",
                f"G1 X{round(mx, 4):.4f} F{f}",
                f"G1 Y{round(my, 4):.4f} F{f}",
                "M3",
                f"G1 Z{NOZZLE_OPEN_Z:.2f} F{f}",
                f"G1 Z{NOZZLE_CLOSED_Z:.2f} F{f}",
                "M5",
            ]
        return lines

    def _learn_start_pulli(self):
        """Lay the whole dot scaffold — for real if a port is connected, and
        always as an animated reveal in the preview."""
        self._learn_dots_laid = False
        self._learn_streaming = False
        n = len(self._learn_dots)
        if self.port_var.get() and not self.is_sending:
            _SPEED_MAP = {"Aqua Low": 50, "Super Low": 100,
                          "Low (default)": 150, "Medium": 200, "High": 250}
            f = _SPEED_MAP.get(self.feed_rate.get(), 150)
            lines = ["$X", "G21", "G90", f"F{f}"]
            lines += self._learn_dot_gcode_lines(self._learn_dots, f)
            lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0"]
            self._pending_raw_gcode = lines
            self._on_send_complete = self._learn_pulli_done
            self._learn_streaming = True
            self.log_to_console(
                f"Learn Mode: Pulli Mode — robot laying {n} dots.", "info")
            self.start_gcode_streaming()
        else:
            why = ("robot is busy with another job"
                   if self.port_var.get() else "no robot connected")
            self.log_to_console(
                f"Learn Mode: {why} — showing the {n} pulli on screen instead.",
                "info")

    def _open_learn_pulli_step(self):
        """Pulli Mode's opening screen: the robot puts the dots down, once."""
        W, H = S(540), S(500)
        _, body = self._learn_shell(
            W, H,
            self.kid_pick("Dot magic! ✨", "Pulli Mode — the robot lays the dots"),
            self.kid_pick(
                "You're on the top level! The robot only puts the dots down "
                "now — connect the dots without lifting your magic bottle, "
                "just like grandma does!",
                f"You've reached {self._learn_level_label()}. The robot places "
                f"the pulli for '{self._learn_design}' and nothing else — every "
                f"line of the rangoli is yours to draw, the way grandma does "
                f"it."),
            outline=KID_THEME["outline"] if self.kid_mode else ACCENT_AMBER,
            mascot="watch")

        prev = tk.Canvas(body, width=S(220), height=S(220), bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(pady=(S(2), S(10)))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 110, 110, 96)
        self._learn_render_preview()

        tk.Label(body,
                 text=f"{len(self._learn_dots)} dots. The faint grey lines are "
                      f"only a reference — join the dots in your own hand.",
                 bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", FS(10)),
                 justify="center", wraplength=W-70).pack(pady=(S(0), S(8)))

        self._learn_status = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_AMBER,
            font=("Segoe UI", FS(10), "bold"), wraplength=W-70, justify="center")
        self._learn_status.pack(side="bottom")
        self._learn_update_status()

        self._learn_start_pulli()
        self._learn_animate_dots()

    def _learn_animate_dots(self):
        """Reveal the pulli one dot at a time while the robot lays them."""
        if self._learn_anim_id is not None:
            try: self.root.after_cancel(self._learn_anim_id)
            except Exception: pass
            self._learn_anim_id = None
        if self._learn_prev is None or self._learn_tf is None:
            return
        self._learn_prev.delete("learn_dots")
        self._learn_dot_step(0)

    def _learn_dot_step(self, i):
        prev = self._learn_prev
        try:
            alive = prev is not None and prev.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            self._learn_anim_id = None
            return
        if i < len(self._learn_dots):
            x, y = self._learn_map(self._learn_tf, *self._learn_dots[i])
            r = 3
            prev.create_oval(x - r, y - r, x + r, y + r, fill=ACCENT_AMBER,
                             outline="#ffffff", width=1, tags="learn_dots")
            self._learn_anim_id = self.root.after(
                70, lambda: self._learn_dot_step(i + 1))
            return
        self._learn_anim_id = None
        # With no robot attached the on-screen reveal IS the robot laying dots.
        if not self._learn_streaming:
            self._learn_pulli_done()

    def _learn_pulli_done(self, ok=True):
        """Dots are down (or the stream failed) — hand the rangoli to the child."""
        self._learn_streaming = False
        if not ok:
            self.log_to_console(
                "Learn Mode: the dots did NOT finish — see the GRBL log. "
                "Holding the lesson here.", "err")
            self._open_learn_pulli_error()
            return
        self._learn_dots_laid = True
        n = len(self._learn_dots)
        self.log_to_console(
            f"Learn Mode: {n} pulli laid — every line is "
            f"the learner's from here.", "recv")
        self._learn_advance(hint=self.kid_pick(
            "Dots are down — go join them up! ✨",
            "Dots are down — the rangoli is all yours!"))
        self._kid_set_mood(
            "cheer", f"{n} magic dots! Now connect them without lifting your "
                     f"bottle! ✨")

    def _open_learn_pulli_error(self):
        """The dot pass didn't complete — retry it or place the dots by hand."""
        W, H = S(520), S(330)
        _, body = self._learn_shell(
            W, H, "The dots didn't finish",
            "GRBL reported a problem while the robot was laying the pulli. "
            "Check the Log for the exact error, make sure the robot is "
            "connected and unclogged, then retry.", outline=ACCENT_PINK)

        def _retry():
            self._open_learn_pulli_step()

        def _by_hand():
            self._learn_dots_laid = True
            self.log_to_console(
                "Learn Mode: learner is placing the pulli by hand.", "info")
            self._learn_advance()

        self._color_button(
            body, "↻ Lay the dots again", _retry,
            ACCENT_PURP, width=W-52, height=S(44), font_size=FS(12)).pack(
                pady=(S(10), S(8)))
        self._color_button(
            body, "I'll place the dots myself", _by_hand,
            ACCENT_GREEN, width=W-52, height=S(44), font_size=FS(12)).pack()

    # ── Learn Mode: USB camera install ────────────────────────────────────────
    # The finished rangoli is meant to be photographed by a USB camera clamped
    # over the mat, and that photo is what the evaluation screen will grade.
    # No camera is fitted to the rig yet, so what is built here is the install
    # step: scan the USB video devices, let the user pick and test one, and
    # remember it between runs. The capture below reads that saved choice.

    def _load_camera_config(self):
        """Restore the installed camera from disk.

        Runs from __init__, before the console exists, so problems stay silent
        — the camera screen simply reports "no camera installed".
        """
        try:
            with open(CAMERA_CONFIG_FILE, "r") as f:
                data = json.load(f)
            idx = data.get("index")
            if isinstance(idx, int) and idx >= 0:
                self._camera_index = idx
                self._camera_name  = data.get("name") or f"Camera {idx}"
        except Exception:
            self._camera_index = None
            self._camera_name  = None

    def _save_camera_config(self):
        try:
            with open(CAMERA_CONFIG_FILE, "w") as f:
                json.dump({"index": self._camera_index,
                           "name":  self._camera_name}, f, indent=2)
        except OSError as e:
            self.log_to_console(f"Camera: couldn't save the choice — {e}", "err")

    @staticmethod
    def _camera_backend():
        """Backend that actually enumerates USB webcams on this platform."""
        import cv2
        if sys.platform == "darwin":
            return cv2.CAP_AVFOUNDATION
        if sys.platform.startswith("win"):
            return cv2.CAP_DSHOW          # MSMF is slow to open by index
        return cv2.CAP_V4L2

    def _open_camera_device(self, index, warmup=CAMERA_WARMUP_FRAMES):
        """Open one USB camera and prove it delivers frames.

        Returns (cap, frame) with the capture still open, or (None, None).
        The caller owns the release. Never raises — a missing OpenCV, a busy
        device and an unplugged one all look the same to the UI.
        """
        try:
            import cv2
        except ImportError:
            return None, None
        cap = None
        try:
            cap = cv2.VideoCapture(index, self._camera_backend())
            if not cap.isOpened():
                cap.release()
                return None, None
            frame = None
            for _ in range(max(1, warmup)):
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
            if frame is None:
                cap.release()
                return None, None
            return cap, frame
        except Exception:
            if cap is not None:
                try: cap.release()
                except Exception: pass
            return None, None

    def _scan_usb_cameras(self):
        """Probe device indices 0…N-1 and return the ones that hand back a
        frame, as [(index, "Camera 0 — 1280×720"), …].

        Called on a worker thread: opening a camera blocks for a second or two
        per index and would otherwise freeze the popup.
        """
        found = []
        for idx in range(CAMERA_PROBE_COUNT):
            cap, frame = self._open_camera_device(idx, warmup=2)
            if cap is None:
                continue
            try:
                h, w = frame.shape[:2]
                found.append((idx, f"Camera {idx} — {w}×{h}"))
            finally:
                try: cap.release()
                except Exception: pass
        return found

    def _camera_installed_text(self):
        if self._camera_index is None:
            return "No camera installed yet."
        return f"Installed: {self._camera_name} (device {self._camera_index})"

    def _set_camera_status(self, text, colour=TEXT_DIM):
        lbl = self._camera_status
        if lbl is None:
            return
        try:
            if lbl.winfo_exists():
                lbl.configure(text=text, fg=colour)
        except tk.TclError:
            self._camera_status = None

    def _open_learn_camera_step(self):
        """Final Learn-Mode step: install the USB camera that photographs the
        rangoli. Reachable with or without a camera plugged in — the course
        can always be finished without one."""
        W, H = S(520), S(400)
        sub = ("A USB camera mounted over the mat photographs your finished "
               "rangoli. Plug it in and install it here — the choice is "
               "remembered for next time.")
        if self._learn_lesson == "symmetry":
            sub = ("Photograph the whole exercise — the robot's half AND your "
                   "mirrored half, with the mirror line running through the "
                   "middle of the frame. The AI scores the symmetry between "
                   "them, so both sides have to be in shot.")
        if self.kid_mode:
            sub = ("Time to show off your rangoli! Point the camera at it and "
                   "take a photo — then I'll give you stars. ⭐" +
                   ("  Get BOTH halves in the picture!"
                    if self._learn_lesson == "symmetry" else ""))
        _, body = self._learn_shell(
            W, H,
            self.kid_pick("Photo time! 📸", "Set up the rangoli camera"), sub,
            outline=KID_THEME["outline"] if self.kid_mode else ACCENT_CYAN,
            mascot="cheer")

        self._camera_status = tk.Label(
            body, text=self._camera_installed_text(),
            bg=BG_CARD, fg=ACCENT_GREEN if self._camera_index is not None
            else TEXT_DIM, font=("Segoe UI", FS(10), "bold"),
            justify="left", wraplength=W-70)
        self._camera_status.pack(anchor="w", pady=(S(0), S(6)))

        self._camera_list = tk.Frame(body, bg=BG_CARD)
        self._camera_list.pack(fill="both", expand=True)
        self._render_camera_devices()

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom")
        self._color_button(
            btns, "\U0001f50d Scan for USB cameras", self._start_camera_scan,
            ACCENT_CYAN, width=W-52, height=S(40), font_size=FS(12)).pack(pady=(S(8), S(6)))
        row = tk.Frame(btns, bg=BG_CARD)
        row.pack(fill="x")
        self._color_button(
            row, "\U0001f4f7 Take the photo", self._learn_take_photo,
            ACCENT_PURP, width=(W-72)//3, height=S(38),
            font_size=FS(11)).pack(side="left")
        self._color_button(
            row, "\U0001f4c1 Upload image", self._learn_upload_photo,
            ACCENT_CYAN, width=(W-72)//3, height=S(38),
            font_size=FS(11)).pack(side="left", padx=S(6))
        self._color_button(
            row, "Continue →", self._show_learn_evaluation,
            ACCENT_GREEN, width=(W-72)//3, height=S(38),
            font_size=FS(11)).pack(side="right")

        self.log_to_console("Learn Mode: camera setup — " +
                            self._camera_installed_text().lower(), "info")

    def _render_camera_devices(self):
        """Redraw the scan-results list inside the camera popup."""
        holder = self._camera_list
        if holder is None:
            return
        try:
            if not holder.winfo_exists():
                self._camera_list = None
                return
        except tk.TclError:
            self._camera_list = None
            return
        for w in holder.winfo_children():
            w.destroy()

        if self._camera_scanning:
            tk.Label(holder, text="Scanning the USB ports…",
                     bg=BG_CARD, fg=ACCENT_AMBER,
                     font=("Segoe UI", FS(10))).pack(anchor="w", pady=(S(4), S(0)))
            return
        if not self._camera_devices:
            tk.Label(holder,
                     text="Scan to see the cameras connected over USB. "
                          "Nothing listed yet.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(9)),
                     justify="left", wraplength=S(430)).pack(anchor="w",
                                                          pady=(S(4), S(0)))
            return

        for idx, label in self._camera_devices:
            chosen = (idx == self._camera_index)
            row = tk.Frame(holder, bg=BG_INPUT, highlightthickness=1,
                           highlightbackground=ACCENT_GREEN if chosen
                           else GLASS_EDGE, cursor="hand2")
            row.pack(fill="x", pady=S(3))
            tk.Label(row, text=("● " if chosen else "○ ") + label,
                     bg=BG_INPUT, fg=TEXT_PRIMARY if chosen else TEXT_DIM,
                     font=("Segoe UI", FS(10), "bold" if chosen else "normal"),
                     anchor="w").pack(side="left", padx=S(10), pady=S(7))
            tk.Label(row, text="installed" if chosen else "click to install",
                     bg=BG_INPUT, fg=ACCENT_GREEN if chosen else TEXT_DIM,
                     font=("Segoe UI", FS(8))).pack(side="right", padx=S(10))
            for w in (row, *row.winfo_children()):
                w.bind("<Button-1>",
                       lambda e, i=idx, n=label: self._install_camera(i, n))

    def _start_camera_scan(self):
        """Kick off a USB scan on a worker thread and repaint when it lands."""
        if self._camera_scanning:
            return
        self._camera_scanning = True
        self._render_camera_devices()
        self._set_camera_status("Scanning USB devices…", ACCENT_AMBER)
        self.log_to_console("Camera: scanning USB video devices…", "info")

        def worker():
            try:
                found = self._scan_usb_cameras()
            except Exception:
                found = []
            self.root.after(0, lambda: self._camera_scan_done(found))

        threading.Thread(target=worker, daemon=True).start()

    def _camera_scan_done(self, found):
        self._camera_scanning = False
        self._camera_devices  = found
        if found:
            # A camera that disappeared between runs must not stay "installed".
            if self._camera_index is not None and \
                    self._camera_index not in [i for i, _ in found]:
                self._camera_index = None
                self._camera_name  = None
                self._save_camera_config()
            self.log_to_console(
                f"Camera: found {len(found)} USB camera(s).", "recv")
            self._set_camera_status(
                self._camera_installed_text() if self._camera_index is not None
                else "Pick the camera pointed at the mat.",
                ACCENT_GREEN if self._camera_index is not None else TEXT_DIM)
        else:
            self.log_to_console(
                "Camera: no USB camera responded. Check the cable, then scan "
                "again.", "err")
            self._set_camera_status(
                "No USB camera responded — check the cable and scan again.",
                ACCENT_PINK)
        self._render_camera_devices()

    # ── Live camera panel (during printing) ────────────────────────────────
    def _build_live_camera_panel(self, canvas_outer, canvas_h):
        """Big camera box to the left of the drawing canvas, matching the
        canvas's height. Hidden until a design is sent to the robot — it
        pops into view over the canvas once the live feed starts, and
        disappears again when the feed stops."""
        box_h = canvas_h
        box_w = max(S(320), int(canvas_h * 0.9))
        self._live_cam_frame_size = (box_w - S(24), box_h - S(90))

        cam_panel = tk.Frame(canvas_outer, bg=BG_CARD,
                              highlightbackground=GLASS_BORDER,
                              highlightthickness=1, bd=0,
                              width=box_w, height=box_h)
        cam_panel.pack_propagate(False)
        self._live_cam_panel = cam_panel

        cam_header = tk.Frame(cam_panel, bg=BG_CARD)
        cam_header.pack(fill="x", padx=S(10), pady=(S(10), S(4)))
        self._live_cam_status_lbl = tk.Label(
            cam_header, text="○ Idle", bg=BG_CARD, fg=TEXT_DIM,
            font=("Segoe UI", FS(11), "bold"))
        self._live_cam_status_lbl.pack(side="left")
        self._color_button(
            cam_header, "Camera Off", self._stop_live_camera, "#334155",
            width=S(100), height=S(28), font_size=FS(10), corner_radius=S(6),
        ).pack(side="right")

        cam_pick_row = tk.Frame(cam_panel, bg=BG_CARD)
        cam_pick_row.pack(fill="x", padx=S(10), pady=(S(0), S(6)))
        self.live_cam_device_var = tk.StringVar(
            value=self._camera_name or "Select camera")
        self._live_cam_combo = ctk.CTkComboBox(
            cam_pick_row, variable=self.live_cam_device_var, values=[],
            state="readonly", width=S(180), height=S(26), font=("Segoe UI", FS(9)),
            fg_color=BG_INPUT, border_color=GLASS_BORDER,
            button_color=BG_INPUT, button_hover_color=BG_INPUT,
            text_color=TEXT_PRIMARY, dropdown_fg_color="#ffffff",
            dropdown_text_color="#0f172a",
            command=self._on_live_cam_device_select)
        self._live_cam_combo.pack(side="left", fill="x", expand=True,
                                   padx=(S(0), S(6)))
        self._color_button(
            cam_pick_row, "⟳", self._scan_live_cam_devices, "#334155",
            width=S(30), height=S(26), font_size=FS(11), corner_radius=S(6),
        ).pack(side="right")

        self._live_cam_label = tk.Label(cam_panel, bg="#000000")
        self._live_cam_label.pack(fill="both", expand=True, padx=S(10),
                                   pady=(S(0), S(10)))
        self._live_cam_label.configure(image=self._camera_idle_image())

        # Not placed yet — stays unmapped until _show_live_cam_panel() pops
        # it open (see start_gcode_streaming / _start_live_camera).
        # Populate the device list without blocking startup.
        self.root.after(400, self._scan_live_cam_devices)

    def _show_live_cam_panel(self):
        panel = self._live_cam_panel
        if panel is None:
            return
        panel.place(relx=0.0, rely=0.5, anchor="w", x=20)
        panel.lift()

    def _hide_live_cam_panel(self):
        panel = self._live_cam_panel
        if panel is None:
            return
        panel.place_forget()

    def _camera_idle_image(self):
        """Solid placeholder frame shown while the live view isn't running,
        so the panel keeps its size instead of collapsing to nothing."""
        from PIL import Image, ImageDraw, ImageTk
        w, h = getattr(self, "_live_cam_frame_size", (240, 160))
        img = Image.new("RGB", (w, h), (17, 17, 24))
        draw = ImageDraw.Draw(img)
        text = "Camera idle"
        try:
            bbox = draw.textbbox((0, 0), text)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            tw, th = 70, 10
        draw.text(((w - tw) / 2, (h - th) / 2), text, fill=(148, 163, 184))
        self._live_cam_idle_photo = ImageTk.PhotoImage(img)
        return self._live_cam_idle_photo

    def _scan_live_cam_devices(self):
        """Refresh the camera picker's dropdown from a USB scan.

        Shares the same scan/state as the Learn Mode camera popup — there is
        only one "installed camera" concept in the app, this just gives a
        second place to pick it from.
        """
        if self._camera_scanning:
            return
        self._camera_scanning = True
        self._live_cam_combo.configure(values=["Scanning…"])
        self.live_cam_device_var.set("Scanning…")

        def worker():
            try:
                found = self._scan_usb_cameras()
            except Exception:
                found = []
            self.root.after(0, lambda: self._live_cam_scan_done(found))

        threading.Thread(target=worker, daemon=True).start()

    def _live_cam_scan_done(self, found):
        self._camera_scanning = False
        self._camera_devices  = found
        if self._camera_index is not None and \
                self._camera_index not in [i for i, _ in found]:
            self._camera_index = None
            self._camera_name  = None
            self._save_camera_config()
        self._refresh_live_cam_dropdown()
        # Also repaint the Learn Mode popup's list, if it's open.
        self._render_camera_devices()

    def _refresh_live_cam_dropdown(self):
        labels = [label for _, label in self._camera_devices]
        self._live_cam_combo.configure(values=labels or ["No camera found"])
        current = next((label for idx, label in self._camera_devices
                         if idx == self._camera_index), None)
        self.live_cam_device_var.set(
            current or self._camera_name or (labels[0] if labels
                                              else "No camera found"))

    def _on_live_cam_device_select(self, label):
        match = next((idx for idx, l in self._camera_devices if l == label),
                     None)
        if match is None:
            return
        self._install_camera(match, label)

    def _start_live_camera(self):
        """Open the selected camera and start streaming it into the panel.
        Silently does nothing if no camera is installed or it can't be
        opened — the print still runs either way.
        """
        if self._live_cam_active:
            return
        if self._camera_index is None:
            self.log_to_console(
                "Camera: no camera selected — live view skipped.", "info")
            return
        try:
            import cv2
        except ImportError:
            self.log_to_console(
                "Camera: OpenCV not available — live view skipped.", "err")
            return
        cap = cv2.VideoCapture(self._camera_index, self._camera_backend())
        if not cap.isOpened():
            cap.release()
            self.log_to_console(
                "Camera: couldn't open the selected camera for live view.",
                "err")
            return
        self._live_cam_cap    = cap
        self._live_cam_active = True
        self._live_cam_status_lbl.configure(text="● LIVE", fg=ACCENT_PINK)
        self._show_live_cam_panel()
        self.log_to_console("Camera: live view on.", "recv")
        self._update_live_camera_frame()

    def _update_live_camera_frame(self):
        if not self._live_cam_active or self._live_cam_cap is None:
            return
        import cv2
        from PIL import Image, ImageTk
        ok, frame = self._live_cam_cap.read()
        if ok and frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            size = getattr(self, "_live_cam_frame_size", (240, 160))
            pil_img = Image.fromarray(rgb).resize(size)
            self._live_cam_photo = ImageTk.PhotoImage(pil_img)
            self._live_cam_label.configure(image=self._live_cam_photo)
        self._live_cam_after = self.root.after(66, self._update_live_camera_frame)

    def _stop_live_camera(self):
        """Turn the live view off — called by the Camera Off button, when a
        print finishes, and on app shutdown. Pops the panel back out of
        view along with it."""
        self._live_cam_active = False
        if self._live_cam_after is not None:
            try: self.root.after_cancel(self._live_cam_after)
            except Exception: pass
            self._live_cam_after = None
        if self._live_cam_cap is not None:
            try: self._live_cam_cap.release()
            except Exception: pass
            self._live_cam_cap = None
        if self._live_cam_label is not None:
            self._live_cam_label.configure(image=self._camera_idle_image())
        if self._live_cam_status_lbl is not None:
            self._live_cam_status_lbl.configure(text="○ Idle", fg=TEXT_DIM)
        self._hide_live_cam_panel()

    def _install_camera(self, index, label):
        """Remember this device, then prove it still delivers a frame."""
        self._camera_index = index
        self._camera_name  = label
        self._save_camera_config()
        self._render_camera_devices()
        self.log_to_console(f"Camera: installed {label}.", "recv")

        cap, frame = self._open_camera_device(index)
        if cap is None:
            self._set_camera_status(
                f"{label} saved, but it isn't sending frames right now.",
                ACCENT_AMBER)
            return
        try:
            h, w = frame.shape[:2]
        finally:
            try: cap.release()
            except Exception: pass
        self._set_camera_status(f"✓ {label} installed and responding "
                                f"({w}×{h}).", ACCENT_GREEN)

    def _learn_take_photo(self):
        """Photograph the physical rangoli with the installed USB camera.

        Untested against real hardware — no camera is mounted on the rig yet,
        so this runs off whatever the install step above found.
        """
        if self._camera_index is None:
            self._set_camera_status(
                "No camera installed — scan and pick one first.", ACCENT_PINK)
            self.log_to_console(
                "Camera: nothing to photograph with — install a camera first.",
                "err")
            return

        self._set_camera_status("Taking the photo…", ACCENT_AMBER)
        cap, frame = self._open_camera_device(self._camera_index)
        if cap is None:
            self._set_camera_status(
                "The installed camera didn't respond — check the USB cable.",
                ACCENT_PINK)
            self.log_to_console(
                f"Camera: device {self._camera_index} didn't respond.", "err")
            return
        try:
            import cv2
            os.makedirs(LEARN_PHOTO_DIR, exist_ok=True)
            design = (self._learn_design or "rangoli").replace(os.sep, "_")
            name = f"{design}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            path = os.path.join(LEARN_PHOTO_DIR, name)
            if not cv2.imwrite(path, frame):
                raise OSError(f"could not write {path}")
        except Exception as e:
            self._set_camera_status(f"Photo failed — {e}", ACCENT_PINK)
            self.log_to_console(f"Camera: photo failed — {e}", "err")
            return
        finally:
            try: cap.release()
            except Exception: pass

        self._learn_photo_path = path
        self._set_camera_status(f"✓ Photo saved to {name}", ACCENT_GREEN)
        self.log_to_console(f"Camera: photo saved — {path}", "recv")

    def _learn_upload_photo(self):
        """Let the user pick an existing image instead of using the camera.
        Used as-is for this Learn-Mode session only — nothing is copied
        into LEARN_PHOTO_DIR, so it leaves no permanent trace."""
        path = filedialog.askopenfilename(
            title="Upload a rangoli photo",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.webp"),
                       ("All files", "*.*")])
        if not path:
            return
        self._learn_photo_path = path
        name = os.path.basename(path)
        self._set_camera_status(f"✓ Using uploaded image {name}", ACCENT_GREEN)
        self.log_to_console(f"Camera: using uploaded image — {path}", "recv")

    # ── Learn-Mode result: known facts, AI photo scoring, fallback ────────────
    def _learn_known_facts(self):
        """Facts the app already knows — no AI needed for these."""
        design     = self._learn_design or "Your Rangoli"
        if self._learn_lesson == "symmetry":
            # The "complexity" row carries the mirror mode for a symmetry
            # challenge — that IS the difficulty of this lesson.
            return {"name": f"{design} — symmetry", "out_of": 10,
                    "complexity": f"{self._learn_sym_mode} "
                                  f"({LEARN_SYMMETRY_LABELS[self._learn_sym_mode]})"}
        complexity = PRESET_DESIGNS.get(self._learn_design, {}).get(
            "difficulty", "Medium")
        return {"name": design, "complexity": complexity, "out_of": 10}

    def _learn_fallback_verdict(self):
        """Used when there's no photo / no API key / the AI call fails."""
        if self._learn_lesson == "symmetry":
            return {"score": 9, "improvements": [
                "Check both halves sit the same distance from the mirror line.",
                "Match the curve direction on the reflected side.",
                "Keep the mirrored tips level with the robot's tips.",
            ]}
        return {"score": 9, "improvements": [
            "Make the white outlines more uniform.",
            "Improve the smoothness of a few curved edges.",
            "Ensure slightly more consistent spacing in the inner "
            "decorative patterns.",
        ]}

    def _score_learn_symmetry_ai(self, api_key, photo_path, design, mode):
        """Score the photo on mirror symmetry alone.

        Deliberately a different prompt from _score_learn_photo_ai: that one
        judges the rangoli as a whole (neatness, spacing, how well it matches
        the design), which is exactly the blend that lets a lopsided-but-tidy
        attempt score well. This lesson exists to isolate symmetry, so the
        judgement has to isolate it too — the model is told which mirror lines
        to check and told to ignore overall prettiness.
        """
        import base64
        with open(photo_path, "rb") as fh:
            img_b64 = base64.b64encode(fh.read()).decode("ascii")
        ext   = os.path.splitext(photo_path)[1].lower()
        media = "image/png" if ext == ".png" else "image/jpeg"

        axes = {"2-way": "a single vertical mirror line through the centre",
                "4-way": "a vertical AND a horizontal mirror line through the "
                         "centre",
                "8-way": "vertical, horizontal and both diagonal mirror lines "
                         "through the centre"}[mode]
        prompt = (
            f"This is a photo of a hand-drawn rangoli exercise based on "
            f"'{design}'. Part of it was drawn by a machine and the rest was "
            f"drawn BY HAND by a child trying to mirror it.\n\n"
            f"Judge ONE THING ONLY: mirror symmetry about {axes}. For each "
            f"mirror line, compare the two sides — are matching features the "
            f"same distance from the line, at the same angle, the same size, "
            f"with curves bending the opposite way as a true reflection "
            f"should?\n\n"
            f"IGNORE neatness, powder texture, colour, line thickness and "
            f"overall beauty unless they actually break the symmetry. A "
            f"beautiful but lopsided rangoli must score LOW. A rough but "
            f"accurately mirrored one must score HIGH.\n\n"
            f"Reply with ONLY compact JSON, no markdown, no commentary, in "
            f"EXACTLY this shape (the values show the FORMAT, not the "
            f"answer):\n"
            f'{{"score": 6, "improvements": ["tip", "tip", "tip"]}}\n'
            f"Rules: score is an integer 0-10 rating symmetry accuracy ONLY. "
            f"Give EXACTLY 3 short, specific, encouraging tips, each under 12 "
            f"words, each about making the reflection more accurate."
        )
        body = {
            "model": "gpt-5.4",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media};base64,{img_b64}"}},
                ],
            }],
            "max_completion_tokens": 300,
            "temperature": 0.4,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"].strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)

        score = max(0, min(10, int(round(float(data.get("score", 0))))))
        tips  = [str(t).strip() for t in data.get("improvements", [])
                 if str(t).strip()]
        if not tips:
            raise ValueError("AI reply had no improvement tips.")
        return {"score": score, "improvements": tips[:3]}

    def _score_learn_photo_ai(self, api_key, photo_path, design, complexity):
        """Send the camera photo to the vision model; return {score, improvements}.

        Mirrors _ask_ai_for_fx_coordinates: same endpoint, same JSON-only
        contract. The model judges the photo only — the design name and
        complexity are already known, so they are NOT asked for here. Raises on
        any network/parse error (the worker turns that into a graceful fallback).
        """
        import base64
        with open(photo_path, "rb") as fh:
            img_b64 = base64.b64encode(fh.read()).decode("ascii")
        ext   = os.path.splitext(photo_path)[1].lower()
        media = "image/png" if ext == ".png" else "image/jpeg"

        prompt = (
            f"This is a photo of a rangoli drawn BY HAND, attempting a "
            f"traditional design called '{design}' ({complexity} difficulty). "
            f"Judge ONLY what you can actually see in the photo: how well it "
            f"matches that kind of design, and its neatness, symmetry, line "
            f"smoothness and spacing.\n\n"
            f"Reply with ONLY compact JSON, no markdown, no commentary, in "
            f"EXACTLY this shape (the values are an example of the FORMAT, not "
            f"the answer):\n"
            f'{{"score": 8, "improvements": ["tip", "tip", "tip"]}}\n'
            f"Rules: score is an integer from 0 to 10. Give EXACTLY 3 short, "
            f"specific, encouraging improvement tips, each under 12 words."
        )
        body = {
            "model": "gpt-5.4",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media};base64,{img_b64}"}},
                ],
            }],
            "max_completion_tokens": 300,
            "temperature": 0.4,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        text = result["choices"][0]["message"]["content"].strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)

        score = max(0, min(10, int(round(float(data.get("score", 0))))))
        tips  = [str(t).strip() for t in data.get("improvements", [])
                 if str(t).strip()]
        if not tips:
            raise ValueError("AI reply had no improvement tips.")
        return {"score": score, "improvements": tips[:3]}

    def _learn_eval_worker(self, api_key, photo_path, popup):
        """Background thread: score the photo, then render on the UI thread."""
        facts = self._learn_known_facts()
        # "ai" only when the model really judged the photo — the level is
        # derived from these rows, so a fallback must never be labelled as one.
        scored_by = "sample"
        symmetry  = self._learn_lesson == "symmetry"
        try:
            if symmetry:
                ai = self._score_learn_symmetry_ai(
                    api_key, photo_path, self._learn_design or "a rangoli",
                    self._learn_sym_mode)
            else:
                ai = self._score_learn_photo_ai(
                    api_key, photo_path, facts["name"], facts["complexity"])
            verdict, note = {**facts, **ai}, None
            scored_by = "ai"
            self.log_to_console(
                f"Learn Mode: AI scored "
                f"{'symmetry ' if symmetry else ''}{ai['score']}/10.", "recv")
        except urllib.error.HTTPError as e:
            detail = ""
            try: detail = e.read().decode("utf-8", errors="ignore")[:150]
            except Exception: pass
            self.log_to_console(
                f"Learn Mode: AI scoring failed (HTTP {e.code}). {detail}", "err")
            verdict = {**facts, **self._learn_fallback_verdict()}
            note = "Couldn't reach the AI — showing sample values."
        except Exception as e:
            self.log_to_console(f"Learn Mode: AI scoring failed ({e}).", "err")
            verdict = {**facts, **self._learn_fallback_verdict()}
            note = "Couldn't score the photo — showing sample values."
        self.root.after(0, lambda: self._render_learn_result(
            popup, verdict, note, scored_by))

    def _show_learn_evaluation(self):
        """Neon 'RANGOLI RESULT' card. Shows an 'Evaluating…' state, sends the
        camera photo to the AI on a background thread, then fills in the real
        score. Falls back to sample values when there's no photo or no key."""
        import math, random
        import tkinter.font as _tkfont

        # ── geometry (fixed up front so the window never needs resizing) ──────
        W, pad, ib = S(540), S(22), S(56)
        tl = pad + S(14) + ib + S(18)
        ROW_H, GAP, IMPR_H, BTN_H = S(74), S(12), S(152), S(44)
        y = S(150)
        name_y  = y; y += ROW_H + GAP
        comp_y  = y; y += ROW_H + GAP
        score_y = y; y += ROW_H + GAP
        impr_y  = y
        level_y = impr_y + IMPR_H + S(14)   # "moved up to Level 3" line
        note_y  = level_y + S(18)           # fallback-reason line, if any
        btn_y   = note_y + S(16)
        # A lesson taken from a page somebody sent gets a third button, so the
        # finished rangoli can go straight back to them.
        n_btns  = 3 if self._learn_share_src else 2
        H = btn_y + BTN_H * n_btns + S(12) * (n_btns - 1) + S(18)
        self._eval_layout = dict(
            W=W, H=H, pad=pad, ib=ib, tl=tl, ROW_H=ROW_H, IMPR_H=IMPR_H,
            BTN_H=BTN_H, name_y=name_y, comp_y=comp_y, score_y=score_y,
            impr_y=impr_y, level_y=level_y, note_y=note_y, btn_y=btn_y)

        CARD_BG = "#0b0b12"
        NEON = ["#f472b6", "#a78bfa", "#22d3ee", "#10b981", "#f97316", "#f472b6"]
        def _h2r(h):
            h = h.lstrip("#"); return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        def _r2h(r):
            return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in r)
        def _lerp(a, b, t):
            ra, rb = _h2r(a), _h2r(b)
            return _r2h(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))
        def _grad(t):
            n = len(NEON) - 1
            t = max(0.0, min(0.999999, t)) * n
            i = int(t); return _lerp(NEON[i], NEON[i + 1], t - i)
        def _blend(fg, t):
            return _lerp(CARD_BG, fg, t)
        self._eval_blend = _blend            # reused by _render_learn_result

        # ── window shell ─────────────────────────────────────────────────────
        self._close_learn_popup()
        self.root.update_idletasks()
        sx = self.root.winfo_screenwidth() // 2 - W // 2
        sy = max(20, self.root.winfo_screenheight() // 2 - H // 2)

        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try: popup.attributes("-alpha", 0.0)
        except tk.TclError: pass
        popup.geometry(f"{W}x{H}+{sx}+{sy}")
        popup.configure(bg=BG_DARK)
        popup.transient(self.root)
        self._learn_popup = popup

        cv = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        self._eval_canvas = cv
        self._draw_rounded_rect(cv, 8, 8, W - 8, H - 8, radius=S(26), fill=CARD_BG)

        # neon gradient border + halo
        def _perimeter(x1, y1, x2, y2, r):
            pts, seg = [], [
                ("edge", (x1 + r, y1), (x2 - r, y1)),
                ("arc",  (x2 - r, y1 + r), -90, 0),
                ("edge", (x2, y1 + r), (x2, y2 - r)),
                ("arc",  (x2 - r, y2 - r), 0, 90),
                ("edge", (x2 - r, y2), (x1 + r, y2)),
                ("arc",  (x1 + r, y2 - r), 90, 180),
                ("edge", (x1, y2 - r), (x1, y1 + r)),
                ("arc",  (x1 + r, y1 + r), 180, 270),
            ]
            for e in seg:
                if e[0] == "edge":
                    (ax, ay), (bx, by) = e[1], e[2]
                    for k in range(9):
                        t = k / 9.0
                        pts.append((ax + (bx - ax) * t, ay + (by - ay) * t))
                else:
                    ccx, ccy = e[1]; a0, a1 = e[2], e[3]
                    for k in range(9):
                        ang = math.radians(a0 + (a1 - a0) * (k / 9.0))
                        pts.append((ccx + r * math.cos(ang), ccy + r * math.sin(ang)))
            return pts
        def _grad_border(x1, y1, x2, y2, r, width, dim=1.0):
            pts = _perimeter(x1, y1, x2, y2, r); n = len(pts)
            for i in range(n):
                a, b = pts[i], pts[(i + 1) % n]
                col = _grad(i / n)
                if dim < 1.0: col = _blend(col, dim)
                cv.create_line(a[0], a[1], b[0], b[1],
                               fill=col, width=width, capstyle="round")
        _grad_border(6, 6, W - 6, H - 6, 28, width=5, dim=0.35)
        _grad_border(8, 8, W - 8, H - 8, 26, width=2)

        close_lbl = tk.Label(popup, text="✕", bg=CARD_BG, fg=TEXT_DIM,
                             font=("Segoe UI", FS(13), "bold"), cursor="hand2")
        close_lbl.place(x=W - 44, y=22)
        close_lbl.bind("<Button-1>", lambda e: self._exit_learn_mode())

        # header: check badge + confetti + gradient title
        hx = W // 2
        cv.create_oval(hx - 30, 24, hx + 30, 84, outline=ACCENT_GREEN, width=3)
        cv.create_oval(hx - 24, 30, hx + 24, 78,
                       outline=_blend(ACCENT_GREEN, 0.5), width=6)
        cv.create_line(hx - 13, 55, hx - 3, 66, hx + 16, 41, fill=ACCENT_GREEN,
                       width=4, capstyle="round", joinstyle="round")
        rng = random.Random(7)
        for _ in range(16):
            ang = rng.uniform(0, 6.2832); dist = rng.uniform(36, 62)
            px = hx + dist * math.cos(ang); py = 54 + dist * math.sin(ang) * 0.72
            cv.create_oval(px - 2, py - 2, px + 2, py + 2,
                           fill=NEON[rng.randrange(len(NEON))], outline="")
        tfont = _tkfont.Font(family="Segoe UI", size=FS(26), weight="bold")
        title = ("SYMMETRY RESULT" if self._learn_lesson == "symmetry"
                 else "RANGOLI RESULT")
        tx = hx - tfont.measure(title) // 2
        for i, ch in enumerate(title):
            col = _lerp("#f472b6", "#f97316", i / max(1, len(title) - 1))
            cv.create_text(tx, 116, text=ch, anchor="w", fill=col, font=tfont)
            tx += tfont.measure(ch)

        # ── decide: real AI scoring, or straight to fallback ─────────────────
        api_key  = self._get_openai_api_key()
        key_ok   = bool(api_key) and api_key != "ADD YOUR OPENAI API KEY HERE"
        photo    = self._learn_photo_path
        photo_ok = bool(photo) and os.path.exists(photo)

        if key_ok and photo_ok:
            loading = cv.create_text(
                W // 2, 330, text="Evaluating your rangoli", fill=TEXT_PRIMARY,
                font=("Segoe UI", FS(14), "bold"), tags="eval")
            cv.create_text(W // 2, 362, text="The AI is judging your photo…",
                           fill=TEXT_DIM, font=("Segoe UI", FS(10)), tags="eval")

            def _spin(i=0):
                if self._learn_popup is not popup:
                    return
                try:
                    cv.itemconfigure(loading,
                                     text="Evaluating your rangoli" + "." * (i % 4))
                except tk.TclError:
                    return
                popup.after(400, lambda: _spin(i + 1))
            _spin()

            self.log_to_console("Learn Mode: sending photo to AI for scoring…",
                                "info")
            threading.Thread(target=self._learn_eval_worker,
                             args=(api_key, photo, popup), daemon=True).start()
        else:
            why = "No photo was taken" if not photo_ok else "No OpenAI API key set"
            self._render_learn_result(
                popup, {**self._learn_known_facts(),
                        **self._learn_fallback_verdict()},
                note=f"{why} — showing sample values.", scored_by="sample")

        self._fade(popup, 0.0, 0.98, 0.08)
        popup.lift()
        popup.focus_force()

    def _render_learn_result(self, popup, verdict, note=None, scored_by="sample"):
        """Paint the real result rows + buttons onto the card. Safe to call from
        a background-thread callback: it no-ops if the popup has been closed.

        The attempt is written to the learner profile *before* the popup check,
        so a child who closes the card while the AI is still thinking doesn't
        silently lose the score that earned them their next level.
        """
        self._record_learn_session(verdict, scored_by)
        if self._learn_popup is not popup:
            return
        import math
        import tkinter.font as _tkfont
        cv = self._eval_canvas
        L  = self._eval_layout
        W, pad, ib, tl = L["W"], L["pad"], L["ib"], L["tl"]
        ROW_H, IMPR_H, BTN_H = L["ROW_H"], L["IMPR_H"], L["BTN_H"]
        name_y, comp_y, score_y = L["name_y"], L["comp_y"], L["score_y"]
        impr_y, note_y, btn_y   = L["impr_y"], L["note_y"], L["btn_y"]
        level_y = L["level_y"]
        _blend = self._eval_blend

        cv.delete("eval")                    # clear the loading text

        def _field(y0, h, accent, glow=False):
            self._draw_rounded_rect(
                cv, pad, y0, W - pad, y0 + h, radius=S(16), fill="#101018",
                outline=_blend(accent, 0.5 if glow else 0.3),
                width=2 if glow else 1, tags="eval")

        def _icon(x, y0, accent, kind):
            self._draw_rounded_rect(cv, x, y0, x + ib, y0 + ib, radius=S(12),
                                    fill=_blend(accent, 0.14), outline=accent,
                                    width=2, tags="eval")
            g = 15
            x1, y1, x2, y2 = x + g, y0 + g, x + ib - g, y0 + ib - g
            gcx, gcy, gw = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1)
            if kind == "flower":
                for a in range(6):
                    ang = math.radians(a * 60)
                    ox = gcx + gw * 0.26 * math.cos(ang)
                    oy = gcy + gw * 0.26 * math.sin(ang)
                    cv.create_oval(ox - gw*0.17, oy - gw*0.17, ox + gw*0.17,
                                   oy + gw*0.17, outline=accent, width=2,
                                   tags="eval")
                cv.create_oval(gcx - gw*0.12, gcy - gw*0.12, gcx + gw*0.12,
                               gcy + gw*0.12, outline=accent, width=2, tags="eval")
            elif kind == "bars":
                bw = gw / 4.4
                for i, hh in enumerate((0.42, 0.72, 1.0)):
                    bx = x1 + i * (bw + bw * 0.5)
                    self._draw_rounded_rect(cv, bx, y2 - (y2 - y1)*hh, bx + bw, y2,
                                            radius=S(3), outline=accent, width=2,
                                            tags="eval")
            elif kind == "star":
                pts = []
                for i in range(10):
                    rr = gw*0.5 if i % 2 == 0 else gw*0.22
                    ang = math.radians(-90 + i * 36)
                    pts += [gcx + rr*math.cos(ang), gcy + rr*math.sin(ang)]
                cv.create_polygon(pts, outline=accent, width=2, fill="", tags="eval")
            elif kind == "bulb":
                cv.create_oval(gcx - gw*0.3, y1, gcx + gw*0.3, y1 + gw*0.6,
                               outline=accent, width=2, tags="eval")
                cv.create_line(gcx - gw*0.16, y1 + gw*0.63, gcx + gw*0.16,
                               y1 + gw*0.63, fill=accent, width=2, tags="eval")
                cv.create_line(gcx - gw*0.12, y1 + gw*0.76, gcx + gw*0.12,
                               y1 + gw*0.76, fill=accent, width=2, tags="eval")

        # Rangoli Name
        _field(name_y, ROW_H, ACCENT_PURP)
        _icon(pad + 14, name_y + (ROW_H - ib)//2, ACCENT_PURP, "flower")
        cv.create_text(tl, name_y + 24, text="Rangoli Name", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", FS(11)), tags="eval")
        cv.create_text(tl, name_y + 49, text=verdict["name"], anchor="w",
                       fill=TEXT_PRIMARY, font=("Segoe UI", FS(17), "bold"), tags="eval")
        # Complexity
        _field(comp_y, ROW_H, ACCENT_CYAN)
        _icon(pad + 14, comp_y + (ROW_H - ib)//2, ACCENT_CYAN, "bars")
        cv.create_text(tl, comp_y + 24, text="Complexity", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", FS(11)), tags="eval")
        cv.create_text(tl, comp_y + 49, text=verdict["complexity"], anchor="w",
                       fill=TEXT_PRIMARY, font=("Segoe UI", FS(17), "bold"), tags="eval")
        # Total Score
        _field(score_y, ROW_H, ACCENT_GREEN, glow=True)
        _icon(pad + 14, score_y + (ROW_H - ib)//2, ACCENT_GREEN, "star")
        cv.create_text(tl, score_y + 22, text="Total Score", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", FS(11)), tags="eval")
        if self.kid_mode:
            # Kid Mode shows stars rather than a bare mark — but the real score
            # stays on the card in small text, because a judge or a parent still
            # needs to be able to read what the model actually said.
            stars = self._kid_star_text(verdict["score"], verdict["out_of"])
            sfont = _tkfont.Font(family="Segoe UI", size=FS(21), weight="bold")
            cv.create_text(tl, score_y + 50, text=stars, anchor="w",
                           fill="#fbbf24", font=sfont, tags="eval")
            cv.create_text(tl + sfont.measure(stars) + 10, score_y + 52,
                           text=f"({verdict['score']}/{verdict['out_of']})",
                           anchor="w", fill=TEXT_DIM,
                           font=("Segoe UI", FS(10)), tags="eval")
        else:
            sfont = _tkfont.Font(family="Segoe UI", size=FS(24), weight="bold")
            cv.create_text(tl, score_y + 50, text=str(verdict["score"]), anchor="w",
                           fill=ACCENT_GREEN, font=sfont, tags="eval")
            cv.create_text(tl + sfont.measure(str(verdict["score"])) + 6, score_y + 52,
                           text=f"/ {verdict['out_of']}", anchor="w",
                           fill=TEXT_PRIMARY, font=("Segoe UI", FS(14), "bold"),
                           tags="eval")
        # Improvements
        _field(impr_y, IMPR_H, ACCENT_AMBER, glow=True)
        _icon(pad + 14, impr_y + 16, ACCENT_AMBER, "bulb")
        cv.create_text(tl, impr_y + 26, text="Improvements", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", FS(11)), tags="eval")
        ty = impr_y + 52
        for tip in verdict["improvements"][:3]:
            cv.create_oval(tl, ty + 5, tl + 6, ty + 11, fill=ACCENT_AMBER,
                           outline="", tags="eval")
            item = cv.create_text(tl + 16, ty, text=tip, anchor="nw",
                                  fill=TEXT_PRIMARY, font=("Segoe UI", FS(11)),
                                  width=W - tl - pad - 20, tags="eval")
            bb = cv.bbox(item)
            ty = (bb[3] if bb else ty + 20) + 8

        # where this attempt left the learner's level
        if self._learn_level_note:
            moved = self._learn_level_note.startswith("\U0001f389")
            cv.create_text(W // 2, level_y, text=self._learn_level_note,
                           fill=ACCENT_GREEN if moved else TEXT_PRIMARY,
                           font=("Segoe UI", FS(10), "bold"),
                           width=W - 2*pad, tags="eval")

        # optional note (fallback reason)
        if note:
            cv.create_text(W // 2, note_y, text=note, fill=TEXT_DIM,
                           font=("Segoe UI", FS(9)), tags="eval")

        # buttons (original completion actions)
        y_btn = btn_y
        src = self._learn_share_src
        if src:
            # The return leg of Family Sharing: her page came here, the
            # rangoli it turned into goes back.
            who = src.get("sender") or "them"
            b0 = self._color_button(
                popup, f"📤  Send this back to {who}",
                lambda v=dict(verdict), sb=scored_by:
                    self._share_compose_reply(v, sb),
                ACCENT_PINK, width=W - 2*pad, height=BTN_H, font_size=FS(13),
                corner_radius=S(14))
            b0.place(x=pad, y=y_btn)
            y_btn += BTN_H + 12
        b1 = self._color_button(popup, "🏠  Learn another design",
                                self._open_learn_gallery, ACCENT_GREEN,
                                width=W - 2*pad, height=BTN_H, font_size=FS(13),
                                corner_radius=S(14))
        b1.place(x=pad, y=y_btn)
        b2 = self._color_button(popup, "Finish", self._exit_learn_mode,
                                ACCENT_PURP, width=W - 2*pad, height=BTN_H,
                                font_size=FS(13), corner_radius=S(14))
        b2.place(x=pad, y=y_btn + BTN_H + 12)

        if self.kid_mode:
            # The mascot reacts to the mark: cheer on a good one, encourage on a
            # wobbly one. Sample verdicts never trigger a cheer — praising a
            # hardcoded 9/10 would be praising nothing the child did.
            stars = self._kid_stars_for(verdict["score"], verdict["out_of"])
            if scored_by != "ai":
                self._kid_set_mood(
                    "idle", "Take a photo next time and I'll give you stars! 📸")
            elif stars >= 4:
                self._kid_celebrate(f"{stars} stars! That's brilliant! 🎉")
            elif stars >= 3:
                self._kid_set_mood("cheer", f"{stars} stars — really good! ⭐")
            else:
                self._kid_set_mood(
                    "oops", f"{stars} star{'' if stars == 1 else 's'} this time"
                            f" — every rangoli makes your hand steadier!")
    def _show_learn_complete(self):
        W, H = S(460), S(300)
        _, body = self._learn_shell(
            W, H, "Rangoli complete! \U0001f389",
            f"You drew every part of '{self._learn_design}' by hand. "
            f"Beautiful work!", outline=ACCENT_PINK)
        tk.Label(body,
                 text="Keep practising — each design gets easier as your hand "
                      "learns the strokes.",
                 bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", FS(10)),
                 justify="left", wraplength=W-70).pack(anchor="w", pady=(S(0), S(16)))
        self._color_button(
            body, "Learn another design", self._open_learn_gallery,
            ACCENT_GREEN, width=W-52, height=S(42), font_size=FS(12)).pack(pady=(S(0), S(8)))
        self._color_button(
            body, "Finish", self._exit_learn_mode,
            ACCENT_PURP, width=W-52, height=S(42), font_size=FS(12)).pack()

    def _close_learn_popup(self):
        # The intro video lives inside this popup — its frame loop must stop
        # before the widgets it draws into are destroyed.
        self._stop_learn_video()
        if self._learn_anim_id is not None:
            try: self.root.after_cancel(self._learn_anim_id)
            except Exception: pass
            self._learn_anim_id = None
        self._learn_prev = None
        self._learn_tf = None
        self._learn_status = None
        # The one-line visualisation runs its own frame loop on its own canvas,
        # so it needs cancelling here too or it keeps ticking into dead widgets.
        self._sikku_stop()
        # Same for the Kid Mode loops — mascot, confetti and sparkles all draw
        # into widgets that are about to be destroyed.
        self._kid_stop_mascot()
        self._kid_stop_confetti()
        self._kid_clear_sparkles()
        self._kid_glass = None
        self._sikku_prev = None
        self._sikku_tf = None
        self._sikku_counter = None
        self._sikku_play_btn = None
        # Camera widgets live in this popup too; a scan thread that finishes
        # afterwards must not touch them.
        self._camera_status = None
        self._camera_list   = None
        popup = self._learn_popup
        if popup is None:
            return
        try: self.root.unbind_all("<MouseWheel>")
        except Exception: pass
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self._learn_popup = None

    # ── Canvas interactions ───────────────────────────────────────────────────
    def on_canvas_click(self, event):
        self._band_start  = None
        self._band_active = False
        self._pending_hit = None
        if self.pen_mode_var.get():
            self._pen_points = [(event.x, event.y)]
            self._pulli_live_marker(event.x, event.y)
            return
        if self.is_moving:
            self.is_moving = False
            self._move_indices = None
            self.hide_hint_popup()
            return
        if self._edit_popup is not None:
            self._close_edit_popup()
            return
        # Stroke editor: clicking an existing stroke (when not placing a new
        # shape) opens the "Edit features" popup for that stroke.
        if not self.selected_preset.get() and self.shape_type.get() == "Select":
            hit = self._stroke_hit_test(event.x, event.y)
            if hit is not None:
                # Open the popup on RELEASE, not press: opening mid-click can
                # eat the release half of the click and make taps feel dead.
                self._pending_hit = (hit, event.x, event.y)
                return
            # Empty press: maybe a rubber-band selection — decided on drag.
            self._band_start = (event.x, event.y)
            return
        preset = self.selected_preset.get()
        colour = self.shape_colour_var.get() if self.multi_colour_var.get() else None
        # Line is the one two-click shape: first click sets the start point,
        # second sets the end. Everything else places on a single click.
        if not preset and self.shape_type.get() == "Line":
            self._place_line_point(event.x, event.y, colour)
            return
        if preset:
            self.hide_hint_popup()
            base = {'type': 'Preset', 'preset': preset,
                    'x': event.x, 'y': event.y,
                    'size': self.size_val.get(), 'colour': colour}
        else:
            self.hide_hint_popup()
            base = {'type': self.shape_type.get(),
                    'x': event.x, 'y': event.y,
                    'size': self.size_val.get(), 'colour': colour}
        self.shapes.append(base)
        for mx, my in self._mirror_centers(event.x, event.y):
            dup = dict(base)
            dup['x'], dup['y'] = mx, my
            self.shapes.append(dup)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()

    def _place_line_point(self, x, y, colour):
        """Two-click straight line: first click = start, second = end."""
        if self._line_start is None:
            self._line_start = (x, y, colour)
            self.canvas.delete("line_live")
            self.canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                                    outline=self._SHAPE_COLORS["Line"],
                                    width=2, tags="line_live")
            self.show_hint_popup("Now click the end point of the line")
            return

        x0, y0, start_colour = self._line_start
        self._line_start = None
        self.canvas.delete("line_live")
        self.hide_hint_popup()
        if math.hypot(x - x0, y - y0) < 3:
            self.log_to_console("Line too short — pick two separate points.",
                                 "err")
            self.show_hint_popup("Click the start point of the line")
            return

        # 'size' is unused for a line (the endpoints define it) but every other
        # shape carries it, so keep the key present for code that reads it.
        base = {'type': 'Line', 'x': x0, 'y': y0, 'x2': x, 'y2': y,
                'size': self.size_val.get(), 'colour': start_colour}
        self.shapes.append(base)
        for (mx, my), (mx2, my2) in zip(self._mirror_centers(x0, y0),
                                        self._mirror_centers(x, y)):
            dup = dict(base)
            dup['x'], dup['y'], dup['x2'], dup['y2'] = mx, my, mx2, my2
            self.shapes.append(dup)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()
        self.show_hint_popup("Click the start point of the next line")

    def _cancel_line_draw(self):
        """Drop a half-placed line (shape switched, canvas cleared, …)."""
        if self._line_start is None:
            return
        self._line_start = None
        try:
            self.canvas.delete("line_live")
        except Exception:
            pass

    def on_shift_click(self, event):
        """Shift-click toggles a stroke in/out of the multi-selection."""
        if (self.pen_mode_var.get() or self.selected_preset.get()
                or self.shape_type.get() != "Select"):
            return "break"
        self._close_edit_popup()
        hit = self._stroke_hit_test(event.x, event.y)
        if hit is not None:
            if hit in self._multi_sel:
                self._multi_sel.remove(hit)
            else:
                self._multi_sel.append(hit)
            self.redraw()
        return "break"

    def on_canvas_drag(self, event):
        if self._pen_points is not None:
            lx, ly = self._pen_points[-1]
            if math.hypot(event.x - lx, event.y - ly) >= 2.0:
                self._pen_points.append((event.x, event.y))
                flat = [c for pt in self._pen_points for c in pt]
                self.canvas.delete("pen_live")
                self.canvas.create_line(flat, fill="#0d9488", width=2,
                                        smooth=True, tags="pen_live")
                self._pulli_live_marker(event.x, event.y)
            return
        if self._band_start is not None:
            x0, y0 = self._band_start
            if not self._band_active and math.hypot(event.x - x0,
                                                    event.y - y0) > 6:
                self._band_active = True
            if self._band_active:
                self.canvas.delete("band")
                self.canvas.create_rectangle(
                    x0, y0, event.x, event.y, outline=ACCENT_CYAN,
                    dash=(4, 3), width=1, tags="band")

    def on_canvas_release(self, event):
        if self._pending_hit is not None:
            (si, pi), px, py = self._pending_hit
            self._pending_hit = None
            # Still counts as a click even with a little wobble.
            if math.hypot(event.x - px, event.y - py) <= 12:
                if self._multi_sel and (si, pi) in self._multi_sel:
                    self._open_edit_popup(si, pi, event.x_root,
                                          event.y_root, multi=True)
                else:
                    self._multi_sel = []
                    self.selected_shape_index = si
                    self.redraw()
                    self._open_edit_popup(si, pi, event.x_root, event.y_root)
            return
        if self._pen_points is not None:
            pts = self._pen_points
            self._pen_points = None
            self.canvas.delete("pen_live")
            self.canvas.delete("pulli_live")
            self._finish_pen_stroke(pts)
            return
        if self._band_start is not None:
            x0, y0 = self._band_start
            self._band_start = None
            self.canvas.delete("band")
            if self._band_active:
                self._band_active = False
                self._select_in_band(x0, y0, event.x, event.y)
            elif self._multi_sel:
                self._multi_sel = []
                self.redraw()
            elif not self.shapes:
                self.show_hint_popup(
                    "Choose a design, turn on the Pen, or pick a shape first")

    def _select_in_band(self, x0, y0, x1, y1):
        bx1, bx2 = sorted((x0, x1))
        by1, by2 = sorted((y0, y1))
        sel = []
        for si, pi, mnx, mny, mxx, mxy, _ in self._get_hit_cache():
            if mnx >= bx1 and mxx <= bx2 and mny >= by1 and mxy <= by2:
                sel.append((si, pi))
        self._multi_sel = sel
        self.redraw()
        if sel:
            self.log_to_console(f"Selected {len(sel)} stroke(s). Click one to "
                                 "edit them together.", "info")

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
        if self.is_moving and self.selected_shape_index is not None:
            prim = self.shapes[self.selected_shape_index]
            dx = event.x - prim['x']
            dy = event.y - prim['y']
            idxs = self._move_indices or [self.selected_shape_index]
            for i in idxs:
                if i >= len(self.shapes):
                    continue
                s = self.shapes[i]
                s['x'] += dx
                s['y'] += dy
                if s.get('type') == 'Line':
                    s['x2'] += dx
                    s['y2'] += dy
                if 'paths' in s:
                    s['paths'] = [[(cx + dx, cy + dy) for cx, cy in path]
                                  for path in s['paths']]
            self.redraw()
            return
        # Rubber-band preview from the line's start point to the cursor.
        if self._line_start is not None:
            x0, y0, _ = self._line_start
            self.canvas.delete("line_live")
            self.canvas.create_line(x0, y0, event.x, event.y,
                                    fill=self._SHAPE_COLORS["Line"],
                                    width=2, dash=(5, 3), tags="line_live")
            self.canvas.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4,
                                    outline=self._SHAPE_COLORS["Line"],
                                    width=2, tags="line_live")
            return
        # Hover highlight: light up the stroke a click would select.
        if (self.pen_mode_var.get() or self._band_active or self.is_sending
                or self._edit_popup is not None or self.selected_preset.get()
                or self.shape_type.get() != "Select"):
            self._set_hover(None)
            return
        self._set_hover(self._stroke_hit_test(event.x, event.y))

    def _set_hover(self, hit):
        if hit == self._hover_hit:
            return
        self._hover_hit = hit
        self.canvas.delete("hover")
        if hit is None:
            self.canvas.configure(cursor="")
            return
        entry = self._cache_entry(hit)
        if entry is not None:
            flat = [c for pt in entry[6] for c in pt]
            if len(flat) >= 4:
                self.canvas.create_line(flat, fill=ACCENT_CYAN, width=4,
                                        smooth=True, tags="hover")
        self.canvas.configure(cursor="hand2")

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

    # ── Stroke editor (click a stroke → "Edit features" popup) ───────────────
    @staticmethod
    def _point_seg_dist(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    def _build_hit_cache(self):
        """Cache (shape_idx, path_idx, bbox, points) for every stroke so
        hover/hit-testing on every mouse move stays cheap."""
        cache = []
        for i, s in enumerate(self.shapes):
            for pidx, path in enumerate(self._shape_paths(s)):
                if len(path) < 2:
                    continue
                xs = [p[0] for p in path]
                ys = [p[1] for p in path]
                cache.append((i, pidx, min(xs), min(ys), max(xs), max(ys),
                              path))
        self._hit_cache = cache
        return cache

    def _get_hit_cache(self):
        return self._hit_cache if self._hit_cache is not None \
            else self._build_hit_cache()

    def _cache_entry(self, hit):
        for e in self._get_hit_cache():
            if (e[0], e[1]) == hit:
                return e
        return None

    def _stroke_hit_test(self, x, y, tol=8.0):
        """Return (shape_index, path_index) of the stroke nearest to (x, y)
        within ``tol`` pixels, or None if nothing is close enough."""
        best, best_d = None, tol
        for i, pidx, mnx, mny, mxx, mxy, path in self._get_hit_cache():
            if not (mnx - tol <= x <= mxx + tol and mny - tol <= y <= mxy + tol):
                continue
            for (x1, y1), (x2, y2) in zip(path, path[1:]):
                d = self._point_seg_dist(x, y, x1, y1, x2, y2)
                if d < best_d:
                    best_d, best = d, (i, pidx)
        return best

    def _close_edit_popup(self):
        popup = self._edit_popup
        if popup is None:
            return
        try: popup.grab_release()
        except Exception: pass
        try: popup.destroy()
        except Exception: pass
        self._edit_popup = None

    def _open_edit_popup(self, shape_idx, path_idx, sx, sy, multi=False):
        self._close_edit_popup()
        W = 190
        popup = tk.Toplevel(self.root)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.configure(bg=GLASS_BORDER)
        popup.transient(self.root)
        self._edit_popup = popup

        outer = tk.Frame(popup, bg=BG_CARD, padx=S(10), pady=S(8))
        outer.pack(fill="both", expand=True, padx=S(1), pady=S(1))

        body = tk.Frame(outer, bg=BG_CARD)
        body.pack(fill="both", expand=True)

        def _clear_body():
            for w in body.winfo_children():
                w.destroy()

        def _header(text):
            head = tk.Frame(body, bg=BG_CARD)
            head.pack(fill="x", pady=(S(0), S(6)))
            tk.Label(head, text=text, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", FS(11), "bold")).pack(side="left")
            close = tk.Label(head, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", FS(11), "bold"), cursor="hand2")
            close.pack(side="right")
            close.bind("<Button-1>", lambda e: self._close_edit_popup())

        def _menu_btn(text, cmd, color):
            btn = self._color_button(body, text, cmd, color,
                                     width=W - 24, height=S(32), font_size=FS(11))
            btn.pack(fill="x", pady=(S(0), S(6)))
            return btn

        def show_main():
            _clear_body()
            if multi:
                _header(f"Edit selection ({len(self._multi_sel)})")
                _menu_btn("Delete all", show_confirm_delete, "#dc2626")
                _menu_btn("Change colour", show_colour_picker, ACCENT_PURP)
                _menu_btn("Move together", start_drag, ACCENT_BLUE)
                _menu_btn("Group", do_group, ACCENT_GREEN)
                _menu_btn(f"Mirror: {self.mirror_mode_var.get()}",
                          show_mirror, ACCENT_CYAN)
                _menu_btn("Save to gallery", do_save, "#0f766e")
                _menu_btn("Clear selection", clear_selection, "#4b5563")
            else:
                _header("Edit features")
                _menu_btn("Delete", show_confirm_delete, "#dc2626")
                _menu_btn("Change colour", show_colour_picker, ACCENT_PURP)
                _menu_btn("Move", start_drag, ACCENT_BLUE)
                _menu_btn("Duplicate", do_duplicate, "#0d9488")
                _menu_btn("Draw order", show_draw_order, ACCENT_AMBER)
                s = self.shapes[shape_idx] if shape_idx < len(self.shapes) else {}
                if len(s.get('paths', [])) > 1:
                    _menu_btn("Ungroup", do_ungroup, ACCENT_PINK)
                _menu_btn(f"Mirror: {self.mirror_mode_var.get()}",
                          show_mirror, ACCENT_CYAN)
                _menu_btn("Save to gallery", do_save, "#0f766e")
            _fit()

        def show_mirror():
            _clear_body()
            _header("Mirror mode")
            tk.Label(body, text="New shapes and pen strokes are mirrored "
                     "live about the canvas centre.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                     wraplength=W - 30, justify="left").pack(
                anchor="w", pady=(S(0), S(6)))
            current = self.mirror_mode_var.get()
            for mode in ("Off", "2-way", "4-way", "8-way"):
                _menu_btn(("● " if mode == current else "") + mode,
                          lambda m=mode: do_mirror(m),
                          ACCENT_CYAN if mode == current else "#4b5563")
            _menu_btn("Back", show_main, "#334155")
            _fit()

        def do_mirror(mode):
            self.mirror_mode_var.set(mode)
            self.log_to_console(f"Mirror mode: {mode}.", "info")
            show_main()

        def do_save():
            self._close_edit_popup()
            self._save_design_to_gallery()

        def show_confirm_delete():
            _clear_body()
            _header("Confirm delete")
            msg = (f"Delete {len(self._multi_sel)} strokes?"
                   if multi else "Delete this stroke?")
            tk.Label(body, text=msg, bg=BG_CARD,
                     fg=TEXT_DIM, font=("Segoe UI", FS(10))).pack(
                anchor="w", pady=(S(0), S(6)))
            _menu_btn("Yes, delete", do_delete, "#dc2626")
            _menu_btn("Cancel", show_main, "#4b5563")
            _fit()

        def show_draw_order():
            _clear_body()
            _header("Draw order")
            tk.Label(body, text="When should the bot draw this stroke?",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", FS(10)),
                     wraplength=W - 30, justify="left").pack(
                anchor="w", pady=(S(0), S(6)))
            _menu_btn("Draw first",
                      lambda: do_reorder(True), ACCENT_GREEN)
            _menu_btn("Draw last",
                      lambda: do_reorder(False), ACCENT_AMBER)
            _menu_btn("Back", show_main, "#4b5563")
            _fit()

        def show_colour_picker():
            _clear_body()
            _header("Choose colour")
            grid = tk.Frame(body, bg=BG_CARD)
            grid.pack(fill="x")
            for n, (name, hex_col) in enumerate(COLOUR_PALETTE.items()):
                btn = self._color_button(
                    grid, name, lambda nm=name: do_colour(nm), hex_col,
                    width=(W - 30) // 2, height=S(28), font_size=FS(10),
                    text_color="#0f172a" if name in ("White", "Yellow")
                    else "#ffffff")
                btn.grid(row=n // 2, column=n % 2, padx=S(2), pady=S(2), sticky="ew")
            _fit()

        def do_delete():
            self._close_edit_popup()
            hits = list(self._multi_sel) if multi else [(shape_idx, path_idx)]
            self._delete_strokes(hits)

        def do_colour(name):
            self._close_edit_popup()
            if multi:
                for si, pi in self._multi_sel:
                    self._apply_stroke_colour(si, pi, name, quiet=True)
                self.redraw()
                self.log_to_console(
                    f"{len(self._multi_sel)} stroke(s) recoloured "
                    f"to {name}.", "info")
            else:
                self._apply_stroke_colour(shape_idx, path_idx, name)

        def start_drag():
            self._close_edit_popup()
            self.selected_shape_index = shape_idx
            if multi:
                self._move_indices = sorted({si for si, _ in self._multi_sel})
            else:
                self._move_indices = None
            self.is_moving = True
            self.show_hint_popup("Move the mouse to drag • click to drop")

        def do_duplicate():
            self._close_edit_popup()
            self._duplicate_shape(shape_idx)

        def do_reorder(first):
            self._close_edit_popup()
            self._reorder_stroke(shape_idx, path_idx, first)

        def do_ungroup():
            self._close_edit_popup()
            self._ungroup_shape(shape_idx)

        def do_group():
            self._close_edit_popup()
            self._group_selection()

        def clear_selection():
            self._close_edit_popup()
            self._multi_sel = []
            self.redraw()

        def _fit():
            popup.update_idletasks()
            w = max(W, popup.winfo_reqwidth())
            h = popup.winfo_reqheight()
            x = min(max(0, sx + 8), self.root.winfo_screenwidth() - w - 4)
            y = min(max(0, sy + 8), self.root.winfo_screenheight() - h - 4)
            popup.geometry(f"{w}x{h}+{x}+{y}")

        show_main()
        popup.lift()
        # No focus_force: stealing focus from the main window mid-click can
        # swallow the next canvas click on macOS.

    def _delete_strokes(self, hits):
        """Delete a batch of (shape_idx, path_idx) strokes. Path-based shapes
        (Imported / Pen) lose just those paths; other shapes are removed
        whole. Indices are processed high-to-low so they stay valid."""
        by_shape = {}
        for si, pi in hits:
            if si < len(self.shapes):
                by_shape.setdefault(si, set()).add(pi)
        n = 0
        for si in sorted(by_shape, reverse=True):
            s = self.shapes[si]
            doomed = by_shape[si]
            if 'paths' in s and len(s['paths']) > len(doomed):
                removed = sorted(doomed)
                old = s.get('path_colours', {})
                for pi in sorted(doomed, reverse=True):
                    if pi < len(s['paths']):
                        del s['paths'][pi]
                s['path_colours'] = {
                    k - sum(1 for r in removed if r < k): v
                    for k, v in old.items() if k not in doomed}
                n += len(doomed)
            else:
                del self.shapes[si]
                n += 1
        self.selected_shape_index = None
        self._multi_sel = []
        self._refresh_part_combo_visibility()
        self.redraw()
        self.log_to_console(f"Deleted {n} stroke(s).", "info")

    def _delete_stroke(self, shape_idx, path_idx):
        self._delete_strokes([(shape_idx, path_idx)])

    def _apply_stroke_colour(self, shape_idx, path_idx, colour_name,
                             quiet=False):
        if shape_idx >= len(self.shapes):
            return
        s = self.shapes[shape_idx]
        if 'paths' in s or s['type'] == 'Complex Flower':
            s.setdefault('path_colours', {})[path_idx] = colour_name
        else:
            s['colour'] = colour_name
        if not quiet:
            self.redraw()
            self.log_to_console(f"Stroke colour changed to {colour_name}.",
                                "info")

    def _duplicate_shape(self, shape_idx):
        if shape_idx >= len(self.shapes):
            return
        d = copy.deepcopy(self.shapes[shape_idx])
        d['x'] += 24
        d['y'] += 24
        if 'paths' in d:
            d['paths'] = [[(x + 24, y + 24) for x, y in p] for p in d['paths']]
        self.shapes.append(d)
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()
        self.log_to_console("Stroke duplicated.", "info")

    def _reorder_stroke(self, shape_idx, path_idx, first):
        """'Draw first/last': reorder the plot sequence. Paths reorder inside
        their shape; single-entity shapes reorder in the shape list."""
        if shape_idx >= len(self.shapes):
            return
        s = self.shapes[shape_idx]
        if 'paths' in s and len(s['paths']) > 1:
            cols = [s.get('path_colours', {}).get(i)
                    for i in range(len(s['paths']))]
            path = s['paths'].pop(path_idx)
            col = cols.pop(path_idx)
            if first:
                s['paths'].insert(0, path)
                cols.insert(0, col)
            else:
                s['paths'].append(path)
                cols.append(col)
            s['path_colours'] = {i: v for i, v in enumerate(cols) if v}
        else:
            shape = self.shapes.pop(shape_idx)
            if first:
                self.shapes.insert(0, shape)
            else:
                self.shapes.append(shape)
        self.selected_shape_index = None
        self._multi_sel = []
        self.redraw()
        self.log_to_console(
            f"Stroke will be drawn {'first' if first else 'last'}.", "info")

    def _ungroup_shape(self, shape_idx):
        """Split a multi-path shape into independent single-stroke shapes."""
        if shape_idx >= len(self.shapes):
            return
        s = self.shapes[shape_idx]
        if len(s.get('paths', [])) < 2:
            return
        pcols = s.get('path_colours', {})
        del self.shapes[shape_idx]
        for i, path in enumerate(s['paths']):
            if len(path) < 2:
                continue
            self.shapes.append({'type': 'Pen', 'paths': [path],
                                'x': path[0][0], 'y': path[0][1], 'size': 0,
                                'colour': pcols.get(i) or s.get('colour')})
        self.selected_shape_index = None
        self._multi_sel = []
        self.redraw()
        self.log_to_console(
            f"Ungrouped into {len(s['paths'])} separate strokes.", "info")

    def _group_selection(self):
        """Merge the shapes touched by the multi-selection into one shape
        (parametric shapes are frozen into their drawn paths)."""
        idxs = sorted({si for si, _ in self._multi_sel if si < len(self.shapes)})
        if len(idxs) < 2:
            self.show_hint_popup("Select strokes from at least two shapes "
                                 "to group them")
            return
        paths, cols = [], {}
        for si in idxs:
            s = self.shapes[si]
            pc = s.get('path_colours', {})
            for pi, p in enumerate(self._shape_paths(s)):
                if len(p) < 2:
                    continue
                col = pc.get(pi) or s.get('colour')
                if col:
                    cols[len(paths)] = col
                paths.append([tuple(pt) for pt in p])
        for si in reversed(idxs):
            del self.shapes[si]
        shape = {'type': 'Pen', 'paths': paths,
                 'x': paths[0][0][0], 'y': paths[0][0][1],
                 'size': 0, 'colour': None}
        if cols:
            shape['path_colours'] = cols
        self.shapes.append(shape)
        self.selected_shape_index = len(self.shapes) - 1
        self._multi_sel = []
        self.redraw()
        self.log_to_console(
            f"Grouped {len(idxs)} shapes into one ({len(paths)} strokes).",
            "info")

    # ── Copy / paste ─────────────────────────────────────────────────────────
    def _copy_selection(self, event=None):
        if self._multi_sel:
            idxs = sorted({si for si, _ in self._multi_sel
                           if si < len(self.shapes)})
        elif self.selected_shape_index is not None \
                and self.selected_shape_index < len(self.shapes):
            idxs = [self.selected_shape_index]
        else:
            return
        self._clipboard = [copy.deepcopy(self.shapes[i]) for i in idxs]
        self.log_to_console(f"Copied {len(self._clipboard)} shape(s). "
                             "Cmd+V to paste.", "info")

    def _paste_clipboard(self, event=None):
        if not self._clipboard:
            return
        for s in self._clipboard:
            d = copy.deepcopy(s)
            d['x'] += 24
            d['y'] += 24
            if 'paths' in d:
                d['paths'] = [[(x + 24, y + 24) for x, y in p]
                              for p in d['paths']]
            self.shapes.append(d)
        self.selected_shape_index = len(self.shapes) - 1
        self._multi_sel = []
        self.redraw()
        self.log_to_console(f"Pasted {len(self._clipboard)} shape(s).", "info")

    # ── Pulli Mode: her dot grid on the canvas, her lines by hand ────────────
    # The digitizer already finds the pulli on a photographed notebook page
    # (_nb_choose_pulli / _nb_fit_grid). Pulli Mode puts *only* that dot layer
    # on the canvas as a guide and hands the page back to her: she connects the
    # dots with the freehand pen from a chair, and the robot lays her lines in
    # powder. Nothing here is ever traced by the robot — the dots are chrome.
    # Dark slate on the light canvas. The first attempt used a mid grey at the
    # dot's own photographed radius and the grid read as dust specks.
    PULLI_GUIDE_COLOUR = "#1e293b"
    PULLI_LIVE_COLOUR  = ACCENT_CYAN
    # How near a stroke has to pass a dot before it locks on. Relative to the
    # grid pitch when the digitizer found one, so a coarse page snaps loosely
    # and a fine page snaps tightly; the fallback suits a hand-held stylus.
    PULLI_SNAP_FRAC    = 0.42
    PULLI_SNAP_FALLBACK_PX = 18.0

    def _pulli_snap_radius(self):
        if self._pulli_pitch:
            return max(6.0, self._pulli_pitch * self.PULLI_SNAP_FRAC)
        return self.PULLI_SNAP_FALLBACK_PX

    def _set_pulli_guides(self, dots, pitch=None, label=""):
        """Show a dot grid on the canvas as drawing scaffold."""
        self._pulli_guides = [(float(x), float(y), float(r))
                              for x, y, r in dots]
        self._pulli_pitch  = float(pitch) if pitch else None
        self._pulli_label  = label or ""
        if self._pulli_guides:
            self.pulli_show_var.set(True)
        self.redraw()

    def _clear_pulli_guides(self):
        self._pulli_guides = []
        self._pulli_pitch  = None
        self._pulli_label  = ""
        self.canvas.delete("pulli_guide")
        self.canvas.delete("pulli_live")

    def _draw_pulli_guides(self):
        """Paint the dot layer. Tag is "pulli_guide" only — deliberately not
        "sim_path", so the simulator and the robot both ignore it."""
        self.canvas.delete("pulli_guide")
        if not (self._pulli_guides and self.pulli_show_var.get()):
            return
        for x, y, r in self._pulli_guides:
            # Big enough to aim a stroke at from a chair, small enough that
            # the dot stays a dot.
            rr = max(4.0, min(r, 7.0))
            self.canvas.create_oval(x - rr, y - rr, x + rr, y + rr,
                                    outline=self.PULLI_GUIDE_COLOUR,
                                    fill=self.PULLI_GUIDE_COLOUR,
                                    width=1, tags="pulli_guide")
        # Sit just above the grid layer, not at the very bottom: the grid's
        # first item is an opaque background rectangle, so tag_lower() here
        # buried every dot behind it — only the cyan live marker (drawn later,
        # unlowered) ever showed, which read as "dots appear near the cursor".
        if self.canvas.find_withtag("grid"):
            self.canvas.tag_raise("pulli_guide", "grid")
        else:
            self.canvas.tag_lower("pulli_guide")

    def _nearest_pulli(self, x, y, radius=None):
        """The guide dot within snapping distance of (x, y), or None."""
        if not (self._pulli_guides and self.pulli_snap_var.get()):
            return None
        r = self._pulli_snap_radius() if radius is None else radius
        best, best_d = None, r
        for dx, dy, _dr in self._pulli_guides:
            d = math.hypot(x - dx, y - dy)
            if d <= best_d:
                best, best_d = (dx, dy), d
        return best

    def _pulli_live_marker(self, x, y):
        """Highlight the dot a stroke would lock onto, so she can see the
        assistance happening rather than being surprised by it afterwards."""
        self.canvas.delete("pulli_live")
        hit = self._nearest_pulli(x, y)
        if hit is None:
            return
        hx, hy = hit
        self.canvas.create_oval(hx - 7, hy - 7, hx + 7, hy + 7,
                                outline=self.PULLI_LIVE_COLOUR, width=2,
                                tags="pulli_live")

    def _snap_stroke_to_pulli(self, pts):
        """Lock a wobbly freehand stroke onto the pulli grid.

        Two different jobs, so two different strengths. The ends of a stroke
        are where she *meant* to touch a dot, so they are moved onto it
        outright. The middle only passes dots on the way, so it is nudged in
        proportion to how close it already came — that keeps a curve that
        loops around a pulli from being pinched flat onto it, which is exactly
        the shape a kolam is made of.
        """
        if not (self._pulli_guides and self.pulli_snap_var.get()) or len(pts) < 2:
            return pts
        r = self._pulli_snap_radius()
        out = [list(p) for p in pts]

        for idx in (0, len(out) - 1):
            hit = self._nearest_pulli(out[idx][0], out[idx][1], radius=r * 1.5)
            if hit is not None:
                out[idx][0], out[idx][1] = hit

        for i in range(1, len(out) - 1):
            x, y = out[i]
            hit = self._nearest_pulli(x, y, radius=r)
            if hit is None:
                continue
            hx, hy = hit
            d = math.hypot(x - hx, y - hy)
            # Full pull at the dot, fading to nothing at the snap radius.
            w = 1.0 - (d / r if r else 1.0)
            out[i][0] = x + (hx - x) * w
            out[i][1] = y + (hy - y) * w

        # Smooth once so the pulled points don't leave kinks between snaps.
        smoothed = [tuple(out[0])]
        for i in range(1, len(out) - 1):
            px, py = out[i - 1]
            cx, cy = out[i]
            nx, ny = out[i + 1]
            smoothed.append(((px + 2 * cx + nx) / 4.0,
                             (py + 2 * cy + ny) / 4.0))
        smoothed.append(tuple(out[-1]))
        return smoothed

    def toggle_pulli_guides(self):
        self.pulli_show_var.set(not self.pulli_show_var.get())
        self.redraw()

    def toggle_pulli_snap(self):
        self.pulli_snap_var.set(not self.pulli_snap_var.get())
        self.log_to_console(
            f"Pulli snap {'on' if self.pulli_snap_var.get() else 'off'}.",
            "info")

    # ── Pen tool + mirror symmetry ───────────────────────────────────────────
    def _pen_btn_colour(self):
        return "#f43f5e" if self.pen_mode_var.get() else "#0d9488"

    def _pen_btn_label(self):
        return (f"✏ {self.tr('Pen off')}" if self.pen_mode_var.get()
                else f"✏ {self.tr('Pen')}")

    def _refresh_pen_btn(self):
        """Repaint the pen button — it is rebuilt with the Design Options
        popup, so it is often absent when the mode is toggled."""
        btn = self.pen_btn
        if btn is None:
            return
        try:
            btn.configure(text=self._pen_btn_label(),
                          fg_color=self._pen_btn_colour(),
                          hover_color=self._pen_btn_colour())
        except tk.TclError:
            self.pen_btn = None

    def toggle_pen_mode(self):
        on = not self.pen_mode_var.get()
        self.pen_mode_var.set(on)
        self._pen_points = None
        self._close_edit_popup()
        self._refresh_pen_btn()
        if on:
            self.selected_preset.set("")
            self.shape_type.set("Select")
            self.canvas.configure(cursor="crosshair")
            self.show_hint_popup("Pen on — click-drag on the canvas to draw")
        else:
            self.canvas.configure(cursor="")
            self.canvas.delete("pulli_live")
            self.hide_hint_popup()

    def _finish_pen_stroke(self, pts):
        if len(pts) == 1:
            # A plain click with the pen leaves a small dot.
            x, y = pts[0]
            pts = [(x + 2.5 * math.cos(i * math.pi / 4),
                    y + 2.5 * math.sin(i * math.pi / 4)) for i in range(9)]
        if len(pts) < 2:
            return
        pts = self._densify_polyline(pts, step_px=3.0)
        snapped = self._snap_stroke_to_pulli(pts)
        pulled = snapped is not pts
        pts = snapped
        paths = [pts] + self._mirror_paths(pts)
        colour = self.shape_colour_var.get() \
            if self.multi_colour_var.get() else None
        self.shapes.append({'type': 'Pen', 'paths': paths,
                            'x': pts[0][0], 'y': pts[0][1],
                            'size': 0, 'colour': colour})
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()
        mirrored = "" if len(paths) == 1 else f" (+{len(paths)-1} mirrored)"
        snap_note = " snapped to the pulli" if pulled else ""
        self.log_to_console(f"Pen stroke added{mirrored}{snap_note}.", "info")

    def _mirror_transforms(self, mode=None, cx=None, cy=None):
        """Reflection functions for a mirror mode, about a centre point:
        2-way = vertical axis; 4-way adds horizontal; 8-way adds both
        diagonals.

        Defaults to the stroke editor's own mirror setting about the canvas
        centre, which is what the pen tool wants. Learn Mode's symmetry
        challenges pass an explicit mode and the design's centre so the same
        reflection engine drives the lesson — see _learn_symmetry_pairs.
        """
        mode = self.mirror_mode_var.get() if mode is None else mode
        cx = MARGIN_L + GRAPH_W / 2.0 if cx is None else cx
        cy = MARGIN_T + GRAPH_H / 2.0 if cy is None else cy
        t = []
        if mode in ("2-way", "4-way", "8-way"):
            t.append(lambda x, y: (2 * cx - x, y))
        if mode in ("4-way", "8-way"):
            t.append(lambda x, y: (x, 2 * cy - y))
            t.append(lambda x, y: (2 * cx - x, 2 * cy - y))
        if mode == "8-way":
            t.append(lambda x, y: (cx + (y - cy), cy + (x - cx)))
            t.append(lambda x, y: (cx - (y - cy), cy + (x - cx)))
            t.append(lambda x, y: (cx + (y - cy), cy - (x - cx)))
            t.append(lambda x, y: (cx - (y - cy), cy - (x - cx)))
        return t

    def _mirror_paths(self, pts, mode=None, cx=None, cy=None):
        return [[f(x, y) for x, y in pts]
                for f in self._mirror_transforms(mode, cx, cy)]

    @staticmethod
    def _mirror_axis_test(mode, cx, cy):
        """Predicate for the fundamental domain of ``mode`` — the one region
        whose reflections under _mirror_transforms tile the whole design
        without overlapping.

        2-way is the left half-plane, 4-way the top-left quadrant, 8-way the
        octant of that quadrant nearer the vertical axis. Getting this right is
        what keeps the child's target halves from landing on top of each other:
        reflect a *half* under 4-way and three of the copies overlap what is
        already drawn.
        """
        if mode == "4-way":
            return lambda x, y: x <= cx and y <= cy
        if mode == "8-way":
            return lambda x, y: (x <= cx and y <= cy
                                 and (cx - x) >= (cy - y))
        return lambda x, y: x <= cx          # 2-way

    @staticmethod
    def _mirror_axis_lines(mode, cx, cy, reach):
        """The mirror lines themselves, as ((x1,y1),(x2,y2)) pairs, so a lesson
        can show the child the axis they are reflecting across."""
        lines = [((cx, cy - reach), (cx, cy + reach))]
        if mode in ("4-way", "8-way"):
            lines.append(((cx - reach, cy), (cx + reach, cy)))
        if mode == "8-way":
            d = reach * 0.7071
            lines.append(((cx - d, cy - d), (cx + d, cy + d)))
            lines.append(((cx - d, cy + d), (cx + d, cy - d)))
        return lines

    def _mirror_centers(self, x, y):
        seen = {(round(x, 1), round(y, 1))}
        out = []
        for f in self._mirror_transforms():
            nx, ny = f(x, y)
            key = (round(nx, 1), round(ny, 1))
            if key not in seen:
                seen.add(key)
                out.append((nx, ny))
        return out

    # ── Shape colours ─────────────────────────────────────────────────────────
    _SHAPE_COLORS = {
        "Square":         "#7c3aed",
        "Rectangle":      "#6d28d9",
        "Circle":         "#9333ea",
        "Triangle":       "#7c3aed",
        "Line":           "#0ea5e9",
        "Flower":         "#ec4899",
        "Complex Flower": "#ec4899",
        "Imported":       "#ec4899",
        "Pen":            "#0d9488",
        "Preset":         ACCENT_BLUE,
    }
    _SELECTED_COLOR = "#ec4899"

    def _stop_simulation(self):
        self._sim_running = False
        aid = getattr(self, "_sim_after_id", None)
        if aid is not None:
            try:
                self.canvas.after_cancel(aid)
            except Exception:
                try:
                    self.root.after_cancel(aid)
                except Exception:
                    pass
            self._sim_after_id = None
        self._sim_frames = []
        self._sim_index = 0
        self._sim_last = None
        try:
            self.canvas.delete("sim_dot")
            self.canvas.delete("sim_trail")
            self.canvas.delete("kid_buddy")     # Kid Mode's bottle character
        except tk.TclError:
            pass
        self._kid_clear_sparkles()
        try:
            self.simulate_btn.configure(
                text="\u25b6 Simulate",
                fg_color=ACCENT_AMBER, hover_color=ACCENT_AMBER,
                text_color="#ffffff")
        except tk.TclError:
            pass

    def _densify_polyline(self, pts, step_px=4.0):
        """Evenly sample along straight polyline segments (sharp corners)."""
        if len(pts) < 2:
            return list(pts)
        out = [pts[0]]
        for i in range(1, len(pts)):
            x0, y0 = out[-1]
            x1, y1 = pts[i]
            dist = math.hypot(x1 - x0, y1 - y0)
            if dist < 1e-9:
                continue
            n = max(1, int(round(dist / step_px)))
            for k in range(1, n + 1):
                t = k / n
                out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        return out

    @staticmethod
    def _tk_bezier_points(pts, closed, steps=16):
        """Reproduce Tk canvas' own smooth=True spline (TkMakeBezierCurve).

        Tk does NOT spline through the control points (that would be a
        Catmull-Rom curve) — for each interior vertex p1 with neighbours
        p0/p2 it draws a quadratic Bezier from midpoint(p0,p1) through p1
        to midpoint(p1,p2), i.e. the curve cuts the corner at every vertex
        except the very first/last of an open path. Matching that exactly
        is what makes the simulated pen trace line up with what's actually
        drawn on the canvas.
        """
        n = len(pts)
        if n < 3:
            return list(pts)

        result = []
        idxs = range(n) if closed else range(1, n - 1)
        if not closed:
            result.append(pts[0])
        for i in idxs:
            p0 = pts[(i - 1) % n]
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            cp1 = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
            cp2 = p1
            cp3 = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
            for s in range(1, steps + 1):
                t = s / steps
                mt = 1.0 - t
                x = mt * mt * cp1[0] + 2 * mt * t * cp2[0] + t * t * cp3[0]
                y = mt * mt * cp1[1] + 2 * mt * t * cp2[1] + t * t * cp3[1]
                result.append((x, y))
        if closed:
            result.append(result[0])
        else:
            result.append(pts[-1])
        return result

    def _densify_smooth(self, pts, step_px=3.5):
        """Sample the curve exactly as canvas smooth=True renders it.

        Designs like Mandala Star are drawn with create_line/create_polygon(
        ..., smooth=True). Tk's spline cuts corners at every vertex rather
        than passing through them, so we replicate Tk's own Bezier
        construction (see _tk_bezier_points) instead of a Catmull-Rom curve
        that (incorrectly) hits every control point — that mismatch is why
        the simulated pen path used to drift from the drawn shape.
        """
        if len(pts) < 2:
            return list(pts)
        if len(pts) == 2:
            return self._densify_polyline(pts, step_px=step_px)

        # Drop duplicate closing point for closed detection; re-close after sample.
        work = list(pts)
        closed = (
            math.hypot(work[0][0] - work[-1][0], work[0][1] - work[-1][1]) < 1.0
        )
        if closed and len(work) > 2:
            work = work[:-1]
        if len(work) < 3:
            return self._densify_polyline(pts, step_px=step_px)

        raw = self._tk_bezier_points(work, closed, steps=16)
        if not raw:
            return list(pts)

        # Re-sample by approximate arc length for even pen speed.
        return self._densify_polyline(raw, step_px=step_px)

    def _sample_oval_outline(self, x1, y1, x2, y2, n=72):
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        rx, ry = abs(x2 - x1) / 2.0, abs(y2 - y1) / 2.0
        return [
            (cx + rx * math.cos(2 * math.pi * i / n),
             cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n + 1)
        ]

    def _build_sim_frames(self):
        """Build pen frames from the exact same paths used to draw shapes.

        Returns a list of (x, y) canvas coords, with None markers for pen-up
        between separate strokes (petals, rings, etc.).

        Presets / flowers are densified with a smooth spline so the pen follows
        the curved outline (canvas smooth=True), not the sharp control polygon.
        """
        strokes = []   # list of (pts, smooth)
        for s in self.shapes:
            stype = s.get("type")
            try:
                if stype == "Circle":
                    x, y, sz = s["x"], s["y"], s["size"]
                    strokes.append((self._sample_oval_outline(
                        x - sz / 2, y - sz / 2, x + sz / 2, y + sz / 2, n=96),
                        False))
                    continue
                if stype == "Square":
                    x, y, sz = s["x"], s["y"], s["size"]
                    h = sz / 2
                    strokes.append(([
                        (x - h, y - h), (x + h, y - h),
                        (x + h, y + h), (x - h, y + h), (x - h, y - h),
                    ], False))
                    continue
                if stype == "Rectangle":
                    x, y, sz = s["x"], s["y"], s["size"]
                    strokes.append(([
                        (x - sz, y - sz / 2), (x + sz, y - sz / 2),
                        (x + sz, y + sz / 2), (x - sz, y + sz / 2),
                        (x - sz, y - sz / 2),
                    ], False))
                    continue
                if stype == "Triangle":
                    # Sharp corners — linear is correct.
                    for path in self._shape_paths(s):
                        pts = [(float(p[0]), float(p[1])) for p in path]
                        if len(pts) >= 2:
                            strokes.append((pts, False))
                    continue

                # Preset / Flower / Complex Flower / Imported: drawn smooth.
                use_smooth = stype in (
                    "Preset", "Flower", "Complex Flower", "Imported")
                for path in self._shape_paths(s):
                    pts = []
                    for pt in path:
                        try:
                            px, py = float(pt[0]), float(pt[1])
                        except (TypeError, ValueError, IndexError, KeyError):
                            continue
                        if math.isfinite(px) and math.isfinite(py):
                            pts.append((px, py))
                    if len(pts) >= 2:
                        strokes.append((pts, use_smooth))
            except Exception as e:
                self.log_to_console(f"Sim path skip ({stype}): {e}", "err")

        if not strokes:
            return []
        return self._frames_from_strokes(strokes)

    def _frames_from_strokes(self, strokes, max_frames=3500):
        """Densify ``[(pts, smooth), …]`` into pen frames.

        One flat list of (x, y) with a ``None`` between strokes to mark a pen
        lift — the format _sim_tick and the Learn-Mode line visualisation both
        animate. Shared so the two never drift apart in how they sample a path.
        """
        frames = []
        for i, (stroke, smooth) in enumerate(strokes):
            if i > 0:
                frames.append(None)
            if smooth:
                dense = self._densify_smooth(stroke, step_px=3.5)
            else:
                dense = self._densify_polyline(stroke, step_px=4.0)
            frames.extend(dense)

        # Cap length without scrambling stroke order.
        real = [p for p in frames if p is not None]
        if len(real) > max_frames:
            stride = max(1, len(real) // max_frames)
            out, count = [], 0
            for p in frames:
                if p is None:
                    out.append(None)
                else:
                    if count % stride == 0:
                        out.append(p)
                    count += 1
            frames = out

        return frames

    def simulate_pattern(self):
        """Animate a green pen tip along every design stroke on the canvas."""
        if getattr(self, "_sim_running", False):
            self._stop_simulation()
            self.log_to_console("Simulation stopped.", "info")
            return

        if not self.shapes:
            self.log_to_console(
                "Nothing to simulate — place or generate a design first.", "err")
            return

        frames = self._build_sim_frames()
        n_pts = sum(1 for p in frames if p is not None)
        if n_pts < 2:
            self.log_to_console(
                "Nothing to simulate — could not build a toolpath.", "err")
            return

        self._sim_frames = frames
        self._sim_index = 0
        self._sim_last = None
        self._sim_running = True

        try:
            self.simulate_btn.configure(
                text="\u25a0 Stop",
                fg_color="#ef4444", hover_color="#ef4444",
                text_color="#ffffff")
        except tk.TclError:
            pass
        try:
            self.canvas.delete("sim_dot")
            self.canvas.delete("sim_trail")
        except tk.TclError:
            pass

        self.log_to_console(f"Simulating toolpath ({n_pts} points)...", "info")
        # Use a method tick (no nested lambdas) so every frame is reliable.
        self._sim_tick()

    def _sim_tick(self):
        """One animation frame — scheduled via canvas.after, no closures."""
        if not getattr(self, "_sim_running", False):
            return

        frames = getattr(self, "_sim_frames", None) or []
        i = getattr(self, "_sim_index", 0)
        if i >= len(frames):
            self._stop_simulation()
            self.log_to_console("Simulation complete.", "recv")
            return

        pt = frames[i]
        self._sim_index = i + 1
        delay = 7

        if pt is None:
            # Pen-up between strokes: clear trail, don't draw a jump line.
            self._sim_last = None
            try:
                self.canvas.delete("sim_trail")
                self.canvas.delete("sim_dot")
            except tk.TclError:
                pass
            self._sim_after_id = self.canvas.after(delay, self._sim_tick)
            return

        x, y = pt
        last = self._sim_last
        if last is not None:
            try:
                self.canvas.create_line(
                    last[0], last[1], x, y,
                    fill="#4ade80", width=3, capstyle=tk.ROUND,
                    tags="sim_trail")
            except tk.TclError:
                pass

        try:
            if self.kid_mode:
                # Kid Mode: the pen tip is a powder-bottle character walking the
                # line, leaving sparkles behind it.
                if i % 3 == 0:
                    self._kid_sparkle(self.canvas, x, y, tag="sim_dot")
                self._kid_draw_buddy(self.canvas, x, y, i, tag="kid_buddy")
                self.canvas.tag_raise("sim_trail")
                self.canvas.tag_raise("sim_dot")
                self.canvas.tag_raise("kid_buddy")
            else:
                self.canvas.delete("sim_dot")
                r = 7
                self.canvas.create_oval(
                    x - r - 3, y - r - 3, x + r + 3, y + r + 3,
                    fill="#bbf7d0", outline="", tags="sim_dot")
                self.canvas.create_oval(
                    x - r, y - r, x + r, y + r,
                    fill=ACCENT_GREEN, outline="#ffffff", width=2, tags="sim_dot")
                self.canvas.tag_raise("sim_trail")
                self.canvas.tag_raise("sim_dot")
        except tk.TclError:
            pass

        self._sim_last = (x, y)
        self._sim_after_id = self.canvas.after(delay, self._sim_tick)

    # ── AI Suggestions (diya + twinkling flowers overlay) ─────────────────
    AI_FX_GRID_COLS = 20
    AI_FX_GRID_ROWS = 20

    def _grid_code_to_canvas_xy(self, code):
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
        cell_w = GRAPH_W / self.AI_FX_GRID_COLS
        cell_h = GRAPH_H / self.AI_FX_GRID_ROWS
        col_idx = max(0, min(self.AI_FX_GRID_COLS - 1, int(lx / cell_w)))
        row_idx = max(0, min(self.AI_FX_GRID_ROWS - 1, int(ly / cell_h)))
        return f"{chr(ord('A') + col_idx)}{row_idx + 1}"

    def _render_design_image(self):
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
        pts = []
        for s in self.shapes:
            for path in self._shape_paths(s):
                for x, y in path:
                    pts.append((x - MARGIN_L, y - MARGIN_T))
        return pts

    def _design_outline_radius_at_angle(self, ang, points, cx, cy):
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
        self.canvas.delete("sim_path")
        self.canvas.delete("hover")
        self._hover_hit = None
        self._draw_pulli_guides()
        multi = self.multi_colour_var.get()
        # "sim_path" marks real design geometry the simulator should follow.
        # Selection chrome uses "shape" only so it is never traced.
        PATH = ("shape", "sim_path")

        for i, s in enumerate(self.shapes):
            selected = (i == self.selected_shape_index)
            if s.get('colour'):
                col = COLOUR_PALETTE.get(s['colour'], "#7c3aed")
            else:
                col = self._SHAPE_COLORS.get(s['type'], "#7c3aed")
            lw  = 3 if selected else 1

            if s['type'] in ('Imported', 'Pen'):
                path_colours = s.get('path_colours', {})
                for pidx, path in enumerate(s['paths']):
                    if len(path) < 2:
                        continue
                    flat = [coord for pt in path for coord in pt]
                    pcol = COLOUR_PALETTE.get(path_colours.get(pidx), col) \
                        if path_colours.get(pidx) else col
                    self.canvas.create_line(flat, fill=pcol, width=lw,
                                            smooth=True, tags=PATH)
                if selected:
                    all_x = [cx for path in s['paths'] for cx, _ in path]
                    all_y = [cy for path in s['paths'] for _, cy in path]
                    if all_x:
                        self.canvas.create_rectangle(
                            min(all_x)-4, min(all_y)-4, max(all_x)+4, max(all_y)+4,
                            outline=self._SELECTED_COLOR, width=1, dash=(4, 4),
                            tags="shape")
                continue

            sz   = s['size']
            x, y = s['x'], s['y']

            if s['type'] == "Line":
                self.canvas.create_line(x, y, s['x2'], s['y2'], fill=col,
                                        width=max(lw, 2), tags=PATH)
            elif s['type'] == "Square":
                self.canvas.create_rectangle(x-sz/2, y-sz/2, x+sz/2, y+sz/2,
                                             outline=col, width=lw, tags=PATH)
            elif s['type'] == "Rectangle":
                self.canvas.create_rectangle(x-sz, y-sz/2, x+sz, y+sz/2,
                                             outline=col, width=lw, tags=PATH)
            elif s['type'] == "Circle":
                self.canvas.create_oval(x-sz/2, y-sz/2, x+sz/2, y+sz/2,
                                        outline=col, width=lw, tags=PATH)
            elif s['type'] == "Triangle":
                self.canvas.create_polygon([x, y-sz/2, x-sz/2, y+sz/2, x+sz/2, y+sz/2],
                                           outline=col, fill="", width=lw, tags=PATH)
            elif s['type'] == "Flower":
                coords = self.get_shape_coords(s)
                flat   = [c for pt in coords for c in pt]
                if len(flat) >= 4:
                    self.canvas.create_line(flat, fill=col, width=lw,
                                            tags=PATH, smooth=True)
            elif s['type'] == "Complex Flower":
                self._draw_complex_flower(s, lw, multi)
            elif s['type'] == "Preset":
                self._draw_preset_shape(s, lw, multi)

            if selected:
                self.canvas.create_oval(x-12, y-12, x+12, y+12,
                                        fill="", outline=self._SELECTED_COLOR,
                                        width=2, tags="shape")
                self.canvas.create_oval(x-8, y-8, x+8, y+8,
                                        fill=self._SELECTED_COLOR, outline="#ffffff",
                                        width=2, tags="shape")

        # Rebuild the hover/hit cache, prune stale selections, then draw the
        # multi-select highlight over the selected strokes.
        self._build_hit_cache()
        valid = {(e[0], e[1]) for e in self._hit_cache}
        self._multi_sel = [h for h in self._multi_sel if h in valid]
        for hit in self._multi_sel:
            entry = self._cache_entry(hit)
            if entry is None:
                continue
            flat = [c for pt in entry[6] for c in pt]
            if len(flat) >= 4:
                self.canvas.create_line(flat, fill=self._SELECTED_COLOR,
                                        width=3, dash=(6, 3), smooth=True,
                                        tags="shape")

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
        if s.get('colour'):
            base_col = COLOUR_PALETTE.get(s['colour'], "#ec4899")
        else:
            base_col = self._SHAPE_COLORS.get(s['type'], "#ec4899")

        selected_part = None
        if (self.selected_shape_index is not None
                and self.shapes[self.selected_shape_index] is s
                and self.part_select_var.get() != "Whole shape"):
            selected_part = self._part_key(self.part_select_var.get())

        PATH = ("shape", "sim_path")
        for idx, path in enumerate(paths):
            flat = [c for pt in path for c in pt]
            if len(flat) < 4:
                continue
            part_col = path_colours.get(idx)
            col = COLOUR_PALETTE.get(part_col, base_col) if part_col else base_col
            part_lw = lw + 2 if idx == selected_part else lw
            if idx < 8:
                self.canvas.create_polygon(flat, outline=col, fill="",
                                           width=part_lw, smooth=True, tags=PATH)
            else:
                self.canvas.create_line(flat, fill=col, width=part_lw,
                                        smooth=True, tags=PATH)

    def _draw_preset_shape(self, s, lw, multi):
        x, y, sz = s['x'], s['y'], s['size']
        paths = PRESET_DESIGNS[s['preset']]['generator'](x, y, sz)
        if s.get('colour'):
            col = COLOUR_PALETTE.get(s['colour'], self._SHAPE_COLORS.get('Preset'))
        else:
            col = self._SHAPE_COLORS.get('Preset', ACCENT_BLUE)
        PATH = ("shape", "sim_path")
        for path in paths:
            if len(path) < 2:
                continue
            flat = [c for pt in path for c in pt]
            self.canvas.create_line(flat, fill=col, width=lw,
                                    smooth=True, tags=PATH)

    # ── G-code helpers ────────────────────────────────────────────────────────
    def get_shape_coords(self, s):
        sz   = s['size']
        x, y = s['x'], s['y']
        res  = []
        if s['type'] == "Line":
            res = [(x, y), (s['x2'], s['y2'])]
        elif s['type'] == "Square":
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

    def _reset_canvas_state(self, keep_pulli=False):
        """Empty the canvas without touching any popup.

        Split out of clear_canvas because Pulli Mode has to wipe the canvas
        while a popup is already closing. Opening the design chooser only to
        destroy it a moment later left a grab behind and froze the window.
        """
        if getattr(self, "_sim_running", False):
            self._stop_simulation()
        self._cancel_line_draw()
        self.shapes = []
        self.selected_shape_index = None
        self._multi_sel = []
        self._pen_points = None
        self._hit_cache = None
        self._hover_hit = None
        self._move_indices = None
        self.canvas.delete("shape")
        self.canvas.delete("hover")
        self.canvas.delete("pen_live")
        if not keep_pulli:
            # The dot grid is scaffolding for the drawing that was just
            # cleared, so it goes with it.
            self._clear_pulli_guides()
        if self._ai_fx_running:
            self.toggle_ai_effects()

    def clear_canvas(self):
        self._close_edit_popup()
        self._reset_canvas_state()
        self.log_to_console("Canvas cleared.", "info")
        self._open_design_options_popup()

    def _shape_paths(self, s):
        if s['type'] in ('Imported', 'Pen'):
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
            mpts = [self.to_machine(px, py) for px, py in path]
            # Every coordinate in this block uses 4-decimal precision, not
            # just the arcs. GRBL derives an arc's radius twice — once from
            # its current position, once from the I/J target — and alarms
            # (error:33) if they disagree by more than a few microns. If a
            # preceding G1 line were rounded to .2f while the arc math uses
            # .4f, the "current position" GRBL lands on after that G1 is
            # already ~0.005mm off from what the arc was fitted against,
            # which is exactly big enough to trip that check. Keeping one
            # precision for the whole path keeps `cur` (what GRBL actually
            # thinks its position is) consistent with the arc fit.
            start_x, start_y = round(mpts[0][0], 4), round(mpts[0][1], 4)
            lines += [
                f"G1 Z0.00 F{f}",
                f"G1 X{start_x:.4f} F{f}",
                f"G1 Y{start_y:.4f} F{f}",
                "M3",
                f"G1 Z0.05 F{f}",
            ]
            cur = (start_x, start_y)
            for seg in _fit_arcs(mpts):
                if seg[0] == 'line':
                    ex, ey = round(seg[1][0], 4), round(seg[1][1], 4)
                    lines.append(f"G1 X{ex:.4f} Y{ey:.4f} F{f}")
                    cur = (ex, ey)
                else:
                    _, end, cx, cy, code = seg
                    ex, ey = round(end[0], 4), round(end[1], 4)
                    iof = round(cx - cur[0], 4)
                    jof = round(cy - cur[1], 4)
                    letter = "G2" if code == 2 else "G3"
                    lines.append(
                        f"{letter} X{ex:.4f} Y{ey:.4f} "
                        f"I{iof:.4f} J{jof:.4f} F{f}")
                    cur = (ex, ey)
            lines.append("M5")
        return lines

    def _shape_gcode_lines(self, s, f):
        return self._paths_gcode_lines(self._shape_paths(s), f)

    def generate_gcode(self):
        _SPEED_MAP = {"Aqua Low": 50, "Super Low": 100, "Low (default)": 150, "Medium": 200, "High": 250}
        f = _SPEED_MAP.get(self.feed_rate.get(), 150)
        lines = ["$X", "G21", "G90", f"F{f}"]

        has_colour = any(s.get('colour') or s.get('path_colours')
                          for s in self.shapes)
        if self.multi_colour_var.get() and has_colour:
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

            # Legend so the Y-axis tap count after each switch is readable.
            lines.append(";COLOUR NUMBERS")
            for name, num in COLOUR_NUMBERS.items():
                lines.append(f";  {num} = {name} ({COLOUR_PALETTE[name]})")
            self.log_to_console(
                "Colour numbers — "
                + ", ".join(f"{n}={name}" for name, n in COLOUR_NUMBERS.items()),
                "info")

            for colour in ordered_colours:
                lines += [f"G1 Z0.00 F{f}", "G1 X0", "G1 Y0", "M5",
                          f"G1 Z{NOZZLE_OPEN_Z:.2f} F{f}",
                          f";COLOUR_SWITCH:{colour}",
                          f"G1 Z{NOZZLE_CLOSED_Z:.2f} F{f}"]
                # Tap out the colour number in Y: N x (out COLOUR_MARK_MM, back).
                num = COLOUR_NUMBERS.get(colour, 0)
                if num:
                    lines.append(f";COLOUR {colour} = {num} — marking {num} time(s)")
                    for _ in range(num):
                        lines += [f"G1 Y{COLOUR_MARK_MM:.2f} F{f}",
                                  f"G1 Y0.00 F{f}"]
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
    def _show_print_controls(self):
        """Slide Pause + Cancel in beside Send — the robot is drawing now."""
        try:
            self.pause_btn.configure(
                text="⏸ Pause", state="normal",
                fg_color="#b45309", hover_color="#b45309")
            self.pause_btn.pack(side="left", padx=(S(8), S(0)))
            self.cancel_btn.configure(state="normal")
            self.cancel_btn.pack(side="left", padx=(S(8), S(0)))
        except tk.TclError:
            pass

    def _hide_print_controls(self):
        """Take them back out once nothing is being drawn."""
        try:
            self.pause_btn.pack_forget()
            self.cancel_btn.pack_forget()
        except tk.TclError:
            pass

    def start_gcode_streaming(self):
        if self.is_sending: return
        if not self.port_var.get():
            self.log_to_console("Error: Choose a serial port first.", "err")
            return
        self.is_sending = True
        self.is_paused = False
        self.cancel_requested = False
        self.pause_event.set()
        self._show_print_controls()
        self.send_btn.configure(
            state="disabled", fg_color="#0f766e", hover_color="#0f766e",
            text_color=TEXT_DIM)
        self._start_live_camera()
        threading.Thread(target=self.send_gcode, daemon=True).start()

    def cancel_gcode_streaming(self):
        """Stop the print now and abandon the rest of the job."""
        if not self.is_sending or self.cancel_requested: return
        self.cancel_requested = True
        # A paused stream is parked on pause_event.wait(); release it so the
        # send loop can see the cancel flag and unwind instead of hanging.
        self.is_paused = False
        self.pause_event.set()
        self.log_to_console("Cancelling print...", "info")
        ser = self._active_serial
        if ser is not None:
            try:
                ser.write(b'!')          # feed hold — stop motion now
                ser.write(b'\x18')       # soft reset — drop GRBL's queue
            except Exception:
                pass
        # If we're parked on the colour-change prompt, that popup holds a
        # grab — tear it down or the user is left staring at a modal for a
        # job that is already dead.
        if self._pending_colour_event is not None:
            self._on_colour_emptied_click()
        try:
            self.cancel_btn.configure(state="disabled")
            self.pause_btn.configure(state="disabled")
        except tk.TclError:
            pass

    def toggle_pause(self):
        if not self.is_sending or self.cancel_requested: return
        self.is_paused = not self.is_paused
        ser = self._active_serial
        if self.is_paused:
            self.pause_event.clear()
            self.pause_btn.configure(
                text="▶ Resume", fg_color="#15803d", hover_color="#15803d")
            self.log_to_console("Print paused.", "info")
            # pause_event only stops us sending the *next* line; the move
            # already handed to GRBL keeps running. '!' is GRBL's real-time
            # feed hold, which decelerates and stops it immediately.
            if ser is not None:
                try:
                    ser.write(b'!')
                except Exception:
                    pass
        else:
            self.pause_event.set()
            self.pause_btn.configure(
                text="⏸ Pause", fg_color="#b45309", hover_color="#b45309")
            self.log_to_console("Print resumed.", "info")
            if ser is not None:
                try:
                    ser.write(b'~')      # cycle start — resume from hold
                except Exception:
                    pass

    def send_gcode(self):
        self.log_to_console("Generating G-code...", "info")
        if self._pending_raw_gcode is not None:
            path = os.path.expanduser("~/Downloads/design.gcode")
            with open(path, "w") as fh:
                fh.write("\n".join(self._pending_raw_gcode))
            self._pending_raw_gcode = None
        else:
            path = self.generate_gcode()
        self.log_to_console(f"G-code ready -> {path}", "recv")
        self.log_to_console("Connecting to GRBL...", "info")
        self.progress_var.set(0.0)
        self.progress_bar.set(0.0)
        # Watch the GRBL replies so callers can tell a real finish from a
        # job that errored, alarmed or went silent half-way through.
        grbl_ok, grbl_done = True, False
        try:
            ser = serial.Serial(self.port_var.get(), 115200, timeout=1)
            time.sleep(2)
            ser.write(b"\r\n\r\n")
            time.sleep(2)
            ser.reset_input_buffer()
            # Publish the handle so Pause/Cancel can send GRBL real-time bytes
            # while this thread is blocked on readline().
            self._active_serial = ser
            with open(path, "r") as fh:
                lines = [l.strip() for l in fh if l.strip()]
            total = max(len(lines), 1)

            for idx, clean in enumerate(lines):
                if self.cancel_requested:
                    grbl_ok = False
                    break
                if clean.startswith(";COLOUR_SWITCH:"):
                    colour = clean.split(":", 1)[1]
                    self.log_to_console(
                        f"At origin — Z stepper opened the nozzle. Empty out "
                        f"the current colour, then click 'Colour emptied, "
                        f"continue' to add {colour}.",
                        "info")
                    event = threading.Event()
                    self.root.after(
                        0, self._arm_colour_emptied_button, event, colour)
                    # Poll instead of blocking outright, so Cancel can break
                    # the operator out of the colour-change prompt.
                    while not event.wait(timeout=0.5):
                        if self.cancel_requested:
                            grbl_ok = False
                            break
                    if self.cancel_requested:
                        break
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
                self.pause_event.wait()
                if self.cancel_requested:
                    grbl_ok = False
                    break
                self.log_to_console(f"→ {clean}", "send")
                ser.write((clean + "\n").encode())
                silent = 0
                while True:
                    if self.cancel_requested:
                        # Soft reset dropped GRBL's queue, so the "ok" for
                        # this line is never coming. Stop waiting for it.
                        grbl_ok = False
                        break
                    res = ser.readline().decode().strip()
                    if res:
                        self.log_to_console(f"← {res}",
                            "recv" if "ok" in res.lower() else "err")
                    else:
                        # readline() timed out (1s). Don't wait forever if the
                        # controller has stopped answering altogether.
                        silent += 1
                        if silent >= 90:
                            grbl_ok = False
                            self.log_to_console(
                                "No reply from GRBL for 90s — giving up on "
                                "this job.", "err")
                            break
                        continue
                    low = res.lower()
                    if "error" in low or "alarm" in low:
                        grbl_ok = False
                        break
                    if "ok" in low:
                        break
                if not grbl_ok:
                    break
                progress = (idx + 1) / total
                self.progress_var.set(progress)
                self.root.after(0, lambda p=progress: (
                    self.progress_bar.set(p),
                    self.sidebar_progress_bar.set(p),
                    self.sidebar_pct_label.config(text=f"{int(p * 100)}%"),
                ))

            self._active_serial = None
            ser.close()
            if grbl_ok:
                grbl_done = True
                self.progress_bar.set(1.0)
                self.sidebar_progress_bar.set(1.0)
                self.root.after(
                    0, lambda: self.sidebar_pct_label.config(text="100%"))
                self.log_to_console("Job complete.", "recv")
            elif self.cancel_requested:
                self.log_to_console("Print cancelled.", "info")
            else:
                self.log_to_console(
                    "Job stopped early — GRBL reported a problem.", "err")
        except Exception as e:
            grbl_ok = False
            self.log_to_console(f"Connection Error: {e}", "err")
        finally:
            self._active_serial = None
            self.is_sending = False
            self.is_paused = False
            self.cancel_requested = False
            self.pause_event.set()
            self.root.after(0, self._stop_live_camera)
            self.root.after(0, self._hide_print_controls)
            self.send_btn.configure(
                state="normal", fg_color="#0d9488", hover_color="#0d9488",
                text_color="#ffffff")
            cb = self._on_send_complete
            self._on_send_complete = None
            if cb is not None:
                finished = bool(grbl_ok and grbl_done)
                self.root.after(0, lambda: cb(finished))


if __name__ == "__main__":
    root = tk.Tk()
    app  = ShapeApp(root)
    try:
        root.mainloop()
    finally:
        # The intro video runs in its own process — never outlive the app.
        app._stop_learn_video()
        app._stop_live_camera()
