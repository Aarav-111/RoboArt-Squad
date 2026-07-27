import copy
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


_patch_ctk_button_full_click()

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

MAX_X    = 28
MAX_Y    = 28

NOZZLE_OPEN_Z   = 0.05
NOZZLE_CLOSED_Z = 0.00

MARGIN_L = 50
MARGIN_B = 40
MARGIN_T = 10
MARGIN_R = 10

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

class ShapeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Rangoli-Bot")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.configure(bg=BG_DARK)

        global GRAPH_W, GRAPH_H, CANVAS_W, CANVAS_H
        screen_h = self.root.winfo_screenheight()
        screen_w = self.root.winfo_screenwidth()
        # Reserve only a slim banner + bottom action strip; canvas fills the rest.
        graph_size = max(400, min(screen_w - 40, screen_h - 100))
        GRAPH_W  = graph_size
        GRAPH_H  = graph_size
        CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
        CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

        self.shapes               = []
        self.selected_shape_index = None
        self._pending_raw_gcode   = None

        self.shape_type           = tk.StringVar(value="Select")
        self.feed_rate            = tk.StringVar(value="High (default)")
        self.port_var             = tk.StringVar()
        self.size_val             = tk.IntVar(value=50)
        self.is_moving            = False
        self.last_ports           = []
        self.is_sending           = False
        self.is_paused            = False
        self.pause_event          = threading.Event()
        self.pause_event.set()
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
        # Student and robot split the design and work in parallel: the student
        # takes the even-numbered parts, the robot the odd-numbered ones.
        self._learn_student_idx = None    # part the student is drawing now
        self._learn_robot_idx   = None    # part the robot is drawing now
        self._learn_done_parts  = set()   # indices finished by either of them
        self._learn_next_free   = 0       # next part nobody has claimed yet
        self._learn_streaming   = False   # robot part is on the real machine
        self._learn_cur_pts     = []      # preview-space points being traced
        self._learn_cur_col     = ACCENT_PURP
        self._learn_prev      = None      # the step popup's preview canvas
        self._learn_tf        = None      # (cx, cy, scale, mid_x, mid_y)
        self._learn_anim_id   = None      # after-id of the running preview draw
        self._learn_status    = None      # "what the robot is doing" label

        # ── Learn Mode: USB camera (install half only — no camera on the rig)
        self._camera_index    = None      # device index of the installed camera
        self._camera_name     = None      # friendly label shown in the UI
        self._camera_devices  = []        # last scan: [(index, label), …]
        self._camera_scanning = False     # a scan thread is running
        self._camera_status   = None      # status label on the camera popup
        self._camera_list     = None      # frame the scan results land in
        self._learn_photo_path = None     # last photo taken of the real mat
        self._load_camera_config()

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
        self.pen_mode_var   = tk.BooleanVar(value=False)
        self.pen_btn        = None    # lives in the Design Options popup
        self.mirror_mode_var = tk.StringVar(value="Off")

        self.setup_ui()
        self.setup_context_menu()
        self.poll_ports()
        # Open design chooser on launch (no toolbar button — forced entry point).
        self.root.after(180, self._open_design_options_popup)

    # ── Main UI ───────────────────────────────────────────────────────────────
    def setup_ui(self):
        main = tk.Frame(self.root, bg=BG_DARK)
        main.pack(fill="both", expand=True, padx=0, pady=0)

        # Slim top banner only — controls float on the canvas itself.
        self._build_banner(main)

        # Compact bottom strip: small action buttons + thin print progress.
        bottom = tk.Frame(main, bg=BG_DARK, height=44)
        bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        bottom.pack_propagate(False)

        btn_wrap = tk.Frame(bottom, bg=BG_DARK)
        btn_wrap.pack(side="left", padx=(0, 12))
        clear_btn = self._color_button(
            btn_wrap, "Clear", self.clear_canvas, "#7c3aed",
            width=84, height=34, font_size=11)
        clear_btn.pack(side="left", padx=(0, 8))
        self.send_btn = self._color_button(
            btn_wrap, "Send to Bot", self.start_gcode_streaming, "#0d9488",
            width=120, height=34, font_size=11)
        self.send_btn.pack(side="left")

        self.pause_btn = self._color_button(
            btn_wrap, "⏸", self.toggle_pause, "#334155",
            width=40, height=34, font_size=14, corner_radius=8)
        self.pause_btn.pack(side="left", padx=(8, 0))
        self.pause_btn.configure(state="disabled")


        prog_wrap = tk.Frame(bottom, bg=BG_DARK)
        prog_wrap.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.progress_bar = ctk.CTkProgressBar(
            prog_wrap, variable=self.progress_var,
            fg_color=BG_INPUT, progress_color=ACCENT_GREEN,
            height=8, corner_radius=4)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.progress_bar.set(0)
        self.sidebar_progress_var = self.progress_var
        self.sidebar_progress_bar = self.progress_bar
        self.sidebar_pct_label = tk.Label(
            prog_wrap, text="0%", bg=BG_DARK, fg=ACCENT_GREEN,
            font=("Segoe UI", 9, "bold"), width=4)
        self.sidebar_pct_label.pack(side="left")

        # Canvas fills all remaining space.
        canvas_outer = tk.Frame(main, bg=BG_DARK)
        canvas_outer.pack(side="top", fill="both", expand=True)
        self.canvas_outer = canvas_outer

        self.root.update_idletasks()
        avail_w = max(canvas_outer.winfo_width(), 400)
        avail_h = max(canvas_outer.winfo_height(), 400)

        global GRAPH_W, GRAPH_H, CANVAS_W, CANVAS_H
        # Fit the full canvas (grid + axis margins) into the available space.
        max_side = min(avail_w, avail_h) - 8
        graph_size = max(
            400,
            max_side - max(MARGIN_L + MARGIN_R, MARGIN_T + MARGIN_B),
        )
        GRAPH_W  = graph_size
        GRAPH_H  = graph_size
        CANVAS_W = GRAPH_W + MARGIN_L + MARGIN_R
        CANVAS_H = GRAPH_H + MARGIN_T + MARGIN_B

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

    def _color_button(self, parent, text, command, color, *,
                      width=110, height=36, font_size=12, corner_radius=10,
                      text_color="#ffffff"):
        """Solid colourful CTk button — full area clickable, no custom hover hacks."""
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
            fr = tk.Frame(host, bg=panel_bg, padx=6, pady=4, highlightthickness=0)
            fr.place(**place_kw)
            fr.lift()
            self._canvas_overlay_frames.append(fr)
            return fr

        # Top-left: Settings / Log
        top_left = _panel(relx=0.0, rely=0.0, anchor="nw", x=6, y=6)

        self.settings_btn = self._color_button(
            top_left, "\u2699", self._open_settings_popup, ACCENT_PURP,
            width=44, height=40, font_size=16, corner_radius=20)
        self.settings_btn.pack(side="left", padx=(0, 6))

        self.log_switch_var = tk.BooleanVar(value=False)
        self.log_switch = ctk.CTkSwitch(
            top_left, text="Log", variable=self.log_switch_var,
            command=self._toggle_log_popup,
            fg_color="#cbd5e1", progress_color=ACCENT_PURP,
            text_color="#4c1d95", font=("Segoe UI", 11, "bold"),
            bg_color=panel_bg, width=48, height=22)
        self.log_switch.pack(side="left", padx=(0, 6))

        # Top-right: Simulate / AI Enhance
        top_right = _panel(relx=1.0, rely=0.0, anchor="ne", x=-6, y=6)

        # The pen tool now lives in the Design Options popup; this slot holds
        # Features (Learn Mode and anything added alongside it).
        self.features_btn = self._color_button(
            top_right, "Features", self._open_features_popup,
            "#0d9488", width=70, height=40, font_size=12)
        self.features_btn.pack(side="left", padx=(0, 8))

        self.simulate_btn = self._color_button(
            top_right, "\u25b6 Simulate", self.simulate_pattern, ACCENT_AMBER,
            width=130, height=40, font_size=12)
        self.simulate_btn.pack(side="left", padx=(0, 8))

        self.ai_fx_btn = self._color_button(
            top_right, "\u2728 AI Enhance", self.toggle_ai_effects, ACCENT_PURP,
            width=140, height=40, font_size=12)
        self.ai_fx_btn.pack(side="left")
        self._sim_running = False

        # Bottom: Size + Multi-colour (+ colour/part pickers)
        bottom_ov = _panel(relx=0.5, rely=1.0, anchor="s", x=0, y=-6)

        size_frame = tk.Frame(bottom_ov, bg=panel_bg)
        size_frame.pack(side="left", padx=(0, 12))
        tk.Label(size_frame, text="Size:", bg=panel_bg, fg=ACCENT_CYAN,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 6))
        self.size_slider = ctk.CTkSlider(
            size_frame, from_=1, to=800, variable=self.size_val,
            command=self._on_slider, width=200, height=18,
            fg_color="#e2e8f0", progress_color=ACCENT_PURP,
            button_color=ACCENT_PINK, button_hover_color="#f9a8d4",
            bg_color=panel_bg)
        self.size_slider.pack(side="left", padx=(0, 6))
        self.size_display = tk.Label(
            size_frame, text="50", bg=panel_bg, fg=ACCENT_CYAN,
            font=("Segoe UI", 10, "bold"), width=4)
        self.size_display.pack(side="left")

        self.multi_colour_switch = ctk.CTkSwitch(
            bottom_ov, text="Multi-colour", variable=self.multi_colour_var,
            command=self._on_multi_colour_toggle,
            fg_color="#cbd5e1", progress_color=ACCENT_PINK,
            text_color="#9d174d", font=("Segoe UI", 11, "bold"),
            bg_color=panel_bg, width=48, height=22)
        self.multi_colour_switch.pack(side="left", padx=(0, 10))

        colour_row = tk.Frame(bottom_ov, bg=panel_bg)
        colour_row.pack(side="left")
        tk.Label(colour_row, text="Colour:", bg=panel_bg, fg=ACCENT_PINK,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 4))
        self.colour_combo = ctk.CTkComboBox(
            colour_row, variable=self.shape_colour_var,
            values=list(COLOUR_PALETTE.keys()), state="readonly",
            width=120, height=32, fg_color="#f8fafc", border_color=ACCENT_PINK,
            button_color="#f8fafc", button_hover_color="#fce7f3",
            text_color="#9d174d", dropdown_fg_color="#ffffff",
            dropdown_text_color="#0f172a", font=("Segoe UI", 10),
            command=self._on_colour_select)
        self.colour_combo.pack(side="left")
        self.colour_combo.configure(state="disabled")

        self.part_label = tk.Label(
            colour_row, text="  Part:", bg=panel_bg, fg=ACCENT_PURP,
            font=("Segoe UI", 10, "bold"))
        self.part_select_var = tk.StringVar(value="Whole shape")
        self.part_combo = ctk.CTkComboBox(
            colour_row, variable=self.part_select_var, values=self._PART_OPTIONS,
            state="readonly", width=120, height=32, fg_color="#f8fafc",
            border_color=ACCENT_PURP, button_color="#f8fafc",
            button_hover_color="#ede9fe", text_color="#5b21b6",
            dropdown_fg_color="#ffffff", dropdown_text_color="#0f172a",
            font=("Segoe UI", 10), command=self._on_part_select)
        self.part_label.pack_forget()
        self.part_combo.pack_forget()

        # Coordinates — bottom-left corner of the canvas
        self.coord_label = tk.Label(
            host, text="X: 0.00  Y: 0.00",
            bg=panel_bg, fg=ACCENT_PURP, font=("Consolas", 10, "bold"),
            padx=6, pady=4, highlightthickness=0)
        self.coord_label.place(relx=0.0, rely=1.0, anchor="sw", x=8, y=-8)
        self.coord_label.lift()
        self._canvas_overlay_frames.append(self.coord_label)

        # Colour-emptied — under top-left toolbar, still on canvas
        self.colour_emptied_btn = self._color_button(
            host, "\U0001f3a8 Emptied", self._on_colour_emptied_click, ACCENT_AMBER,
            width=120, height=34, font_size=11)
        self.colour_emptied_btn.place(relx=0.0, rely=0.0, anchor="nw", x=8, y=52)
        self.colour_emptied_btn.lift()
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
        banner = tk.Frame(parent, bg="#000000", height=44)
        banner.pack(fill="x", side="top")
        banner.pack_propagate(False)

        center = tk.Frame(banner, bg="#000000")
        center.place(relx=0.5, rely=0.5, anchor="center")

        icon_c = tk.Canvas(center, width=32, height=32, bg="#000000",
                           highlightthickness=0)
        icon_c.pack(side="left", padx=(0, 8))
        self._draw_flower_icon(icon_c, 16, 16, 13)

        title_c = tk.Canvas(center, bg="#000000", highlightthickness=0,
                            width=200, height=30)
        title_c.pack(side="left")
        title = "Rangoli Bot"
        colors = ["#f9a825", "#f97316", "#ec4899", "#a855f7",
                  "#6366f1", "#3b82f6", "#06b6d4", "#10b981",
                  "#f9a825", "#f97316", "#ec4899"]
        _char_w = {'i': 7, 'l': 8, 'r': 9, 't': 9, 'f': 9, ' ': 11,
                   'a': 12, 'n': 12, 'g': 12, 'o': 12, 'e': 11, 'k': 11,
                   'R': 14, 'B': 13}
        x_off = 2
        for ch, col in zip(title, colors):
            title_c.create_text(x_off, 15, text=ch, fill=col,
                                font=("Georgia", 16, "bold"), anchor="w")
            x_off += _char_w.get(ch, 13)

        # Hidden rangoli trigger: an invisible black hit-area filling the
        # banner's right corner, with a tiny dot as the only visual marker.
        # Clicking anywhere in the corner region works, not just the dot.
        hot = tk.Canvas(banner, width=90, height=44, bg="#000000",
                        highlightthickness=0, cursor="hand2")
        hot.create_oval(78, 20, 82, 24, fill="#1a1a1a", outline="")
        hot.place(relx=1.0, rely=0.5, anchor="e", x=0)
        hot.bind("<Button-1>", self.load_and_send_rangoli)

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
        wrap.pack(side="right", padx=(4, 0))
        btn = self._color_button(
            wrap, text, _once, fg_color,
            width=112, height=40, font_size=12)
        btn.pack(padx=4, pady=4)
        return btn

    def _label(self, parent, text, fg=TEXT_DIM):
        tk.Label(parent, text=text, bg=BG_CARD, fg=fg,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(6, 1))

    _PART_OPTIONS = ["Whole shape"] + [f"Petal {i+1}" for i in range(8)] + ["Center"]

    # ── NEW: Design Options popup (Pre-designed / AI Generated / Import) ───
    def _open_design_options_popup(self):
        self._close_design_options_popup()
        self.root.update_idletasks()
        # Tall enough for 4 full-height action rows + title without clipping Pen.
        W, H = 400, 380
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
            glass, 4, 4, W - 4, H - 4, radius=22,
            fill=BG_CARD, outline=GLASS_BORDER, width=1)
        glass.create_text(
            28, 30, text="Choose a design", anchor="w",
            fill=TEXT_PRIMARY, font=("Segoe UI", 15, "bold"))
        glass.create_text(
            28, 54, text="Pick how you want to start your rangoli.",
            anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))
        # Decorative only — never steal clicks from the buttons below.
        glass.bind("<Button-1>", lambda e: "break")
        glass.bind("<ButtonRelease-1>", lambda e: "break")

        close_id = glass.create_text(
            W - 26, 26, text="✕", anchor="center",
            fill=TEXT_DIM, font=("Segoe UI", 13, "bold"), tags="close_btn")

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
            row.pack(fill="x", pady=(0, 12))
            tk.Label(row, text=label, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 12, "bold")).pack(side="left")

            if label == "Import Designs":
                self._small_btn(row, "Browse",
                                 _pick(self.import_design),
                                 ACCENT_AMBER, "#b45309")
            elif label == "AI Generated":
                self._small_btn(row, "Generate",
                                 _pick(self.generate_ai_design),
                                 ACCENT_PURP, "#8b5cf6")
            elif label == "Pre-designed":
                self._small_btn(row, "Gallery",
                                 _pick(self._open_gallery),
                                 ACCENT_BLUE, "#3b82f6")

        # Draw it yourself — the pen tool used to sit on the canvas toolbar.
        pen_row = tk.Frame(body, bg=BG_CARD)
        pen_row.pack(fill="x", pady=(0, 12))
        tk.Label(pen_row, text="Freehand Pen", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        self.pen_btn = self._small_btn(
            pen_row, self._pen_btn_label(), _pick(self.toggle_pen_mode),
            self._pen_btn_colour(), "#14b8a6")

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

    # ── NEW: Settings popup (Connection / Speed / Robot Test) ──────────────
    def _open_settings_popup(self):
        self._close_settings_popup()
        self.root.update_idletasks()
        W, H = 400, 512
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
        self._settings_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=20,
                                fill=BG_CARD, outline=ACCENT_PURP, width=2)
        glass.create_text(24, 26, text="Settings", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"))

        close_lbl = tk.Label(popup, text="\u2715", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 13, "bold"), cursor="hand2")
        close_lbl.place(x=W-38, y=14)
        close_lbl.bind("<Button-1>", lambda e: self._close_settings_popup())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=20, y=54, width=W-40, height=H-74)

        def _row(label_text, sub_text=None):
            row_outer = tk.Frame(body, bg=BG_CARD)
            row_outer.pack(fill="x", pady=(0, 14))
            top = tk.Frame(row_outer, bg=BG_CARD)
            top.pack(fill="x")
            tk.Label(top, text=label_text, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 12, "bold")).pack(side="left")
            select_slot = tk.Frame(top, bg=BG_CARD)
            select_slot.pack(side="right")
            if sub_text:
                tk.Label(row_outer, text=sub_text, bg=BG_CARD, fg=TEXT_DIM,
                         font=("Segoe UI", 9), wraplength=W-60,
                         justify="left").pack(anchor="w", pady=(2, 0))
            return select_slot

        # 1) Connection — select a serial port
        slot = _row("Connection", "Choose the serial port your robot is connected on.")
        current_ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo = ctk.CTkComboBox(
            slot, variable=self.port_var, values=current_ports, state="readonly",
            width=170, fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 11))
        self.port_combo.pack(side="right")
        self.port_menu = self.port_combo

        # 2) Speed — select a feed rate
        slot = _row("Speed", "Feed rate used when streaming G-code.")
        self.feed_combo = ctk.CTkComboBox(
            slot, variable=self.feed_rate,
            values=["Low", "Medium", "High (default)"], state="readonly",
            width=170, fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 11))
        self.feed_combo.pack(side="right")

        # 3) Robot Test — select a test shape
        slot = _row("Robot Test", "Pick a test shape, then click the canvas to place it.")
        self.shape_menu = ctk.CTkComboBox(
            slot, variable=self.shape_type,
            values=["Select", "Square", "Rectangle", "Circle",
                    "Triangle", "Flower", "Complex Flower"],
            state="readonly", width=170,
            fg_color=BG_INPUT, border_color=GLASS_EDGE,
            button_color=GLASS_EDGE, button_hover_color=ACCENT_GREEN,
            text_color=TEXT_PRIMARY, dropdown_fg_color=BG_CARD,
            dropdown_text_color=TEXT_PRIMARY, font=("Segoe UI", 10),
            command=lambda v: self._on_shape_menu_select(v))
        self.shape_menu.pack(side="right")

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
        W, H = 400, 260
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
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=20,
                                fill=BG_CARD, outline="#0d9488", width=2)
        glass.create_text(24, 26, text="Features", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"))
        glass.create_text(24, 50, text="Extra ways to work with the robot.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))
        glass.bind("<Button-1>", lambda e: "break")

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 13, "bold"), cursor="hand2")
        close_lbl.place(x=W-38, y=14)
        close_lbl.bind("<Button-1>", lambda e: self._close_features_popup())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=20, y=74, width=W-40, height=H-94)
        body.lift()

        # Learn Mode — guided, step-by-step rangoli lessons
        row = tk.Frame(body, bg=BG_CARD)
        row.pack(fill="x", pady=(0, 6))
        tk.Label(row, text="Learn Mode", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        on = self.learn_mode_var.get()
        self.learn_btn = self._small_btn(
            row, "Stop" if on else "Start", self._toggle_learn_mode,
            ACCENT_PINK if on else ACCENT_GREEN, "#f472b6" if on else "#4ade80")
        tk.Label(body,
                 text="Learn to draw rangoli by hand. The robot draws one "
                      "part, then you copy it with your powder bottle.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 9),
                 wraplength=W-60, justify="left").pack(anchor="w")

        popup.lift()
        popup.focus_force()
        popup.update_idletasks()
        body.lift()

    def _close_features_popup(self):
        popup = self._features_popup
        self.learn_btn = None
        self._features_popup = None
        if popup is None:
            return
        try: popup.destroy()
        except Exception: pass

    # ── NEW: Log popup toggle ───────────────────────────────────────────────
    def _toggle_log_popup(self):
        if self.log_switch_var.get():
            self._open_log_popup()
        else:
            self._close_log_popup()

    def _open_log_popup(self):
        self._close_log_popup(reset_switch=False)
        self.root.update_idletasks()
        W, H = 640, 340
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
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=10, pady=6)
        tk.Button(hdr, text="Clear", bg=BG_PANEL, fg=TEXT_DIM, bd=0,
                  font=("Segoe UI", 9), activebackground=BG_PANEL,
                  command=lambda: self._clear_console()).pack(side="right", padx=6)
        close_lbl = tk.Label(hdr, text="\u2715", bg=BG_PANEL, fg=TEXT_DIM,
                             font=("Segoe UI", 11, "bold"), cursor="hand2")
        close_lbl.pack(side="right", padx=6)
        close_lbl.bind("<Button-1>", lambda e: self._on_log_close_clicked())

        # Create a brand-new Text widget as a real child of this popup (a
        # widget can't be safely moved between Toplevels in Tkinter), and
        # replay the buffered history into it.
        console = tk.Text(popup, bg="#110e2e", fg="#a8d8a8",
                          font=("Consolas", 10), bd=0, highlightthickness=0,
                          insertbackground=ACCENT_GREEN)
        console.tag_config("send", foreground=ACCENT_CYAN)
        console.tag_config("recv", foreground=ACCENT_GREEN)
        console.tag_config("err",  foreground=ACCENT_PINK)
        console.tag_config("info", foreground=ACCENT_AMBER)
        console.pack(fill="both", expand=True, padx=2, pady=(0, 2))
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
            self.part_label.pack(side="left", padx=(10, 4))
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
                                    font=("Segoe UI", 10))
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
        r = 11
        c.create_oval(ox-r, oy-r, ox+r, oy+r, fill="#7c3aed",
                      outline="#ffffff", width=2, tags="grid")
        c.create_oval(ox-5, oy-5, ox+5, oy+5, fill="#ffffff", outline="", tags="grid")
        c.create_text(ox-4, oy+16, text="(0,0)", fill="#7c3aed",
                      font=("Segoe UI", 9, "bold"), tags="grid")
        c.create_text(ox+arm+14, oy-6,   text="X+", fill="#7c3aed",
                      font=("Segoe UI", 8, "bold"), tags="grid")
        c.create_text(ox+14, oy-arm-8,   text="Y+", fill="#7c3aed",
                      font=("Segoe UI", 8, "bold"), tags="grid")

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

        W, H  = 640, 760
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
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=24,
                                fill=BG_CARD, outline=ACCENT_PURP, width=2)
        glass.create_text(28, 30, text=f"Preview: {filename}", anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"))
        glass.create_text(
            28, 54,
            text="Edit → click a stroke → Delete or Make multi-colour.",
            anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))

        prev_x = (W - CW) // 2
        prev_y = 76
        preview = tk.Canvas(popup, width=CW, height=CW, bg=CANVAS_BG, highlightthickness=0)
        preview.place(x=prev_x, y=prev_y)

        status_lbl = tk.Label(popup, text="", bg=BG_CARD, fg=TEXT_DIM,
                              font=("Segoe UI", 9, "bold"))
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
                          highlightthickness=2, bd=0, padx=6, pady=6)
            state['action_frame'] = fr
            tk.Label(fr, text="Pick a colour", bg=BG_PANEL, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
            row = tk.Frame(fr, bg=BG_PANEL)
            row.pack()
            for name, hex_col in COLOUR_PALETTE.items():
                sw = tk.Canvas(row, width=22, height=22, bg=BG_PANEL,
                               highlightthickness=1, highlightbackground="#ffffff")
                sw.create_rectangle(2, 2, 20, 20, fill=hex_col, outline=hex_col)
                sw.pack(side="left", padx=2)
                sw.bind("<Button-1>", lambda _e, n=name: apply_colour(n))
                sw.configure(cursor="hand2")
                sw.bind("<Enter>", lambda _e, n=name: status_lbl.config(
                    text=f"Colour: {n}"))
            tk.Button(
                fr, text="Cancel", command=dismiss_action_menu,
                bg=BG_INPUT, fg=TEXT_DIM, relief="flat",
                font=("Segoe UI", 8), cursor="hand2",
                activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
            ).pack(anchor="e", pady=(6, 0))
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
                          highlightthickness=2, bd=0, padx=6, pady=6)
            state['action_frame'] = fr

            def _act_btn(parent, text, cmd, accent):
                b = tk.Button(
                    parent, text=text, command=cmd,
                    bg=accent, fg="#ffffff", relief="flat",
                    font=("Segoe UI", 9, "bold"), cursor="hand2",
                    activebackground=accent, activeforeground="#ffffff",
                    padx=10, pady=4)
                b.pack(side="left", padx=3)
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
                font=("Segoe UI", 9, "bold"), cursor="hand2",
                activebackground=BG_CARD, activeforeground=TEXT_PRIMARY,
                padx=6, pady=4,
            ).pack(side="left", padx=(6, 0))

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
            popup, text="Edit: OFF", width=110, height=32,
            fg_color="transparent", hover_color="#f1f5f9",
            border_width=1, border_color=GLASS_EDGE,
            text_color=TEXT_PRIMARY, font=("Segoe UI", 10, "bold"))
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
            popup, text="Cancel", width=110, height=32,
            fg_color="transparent", hover_color="#f1f5f9",
            border_width=1, border_color=GLASS_EDGE,
            text_color=TEXT_DIM, font=("Segoe UI", 10, "bold"),
            command=lambda: self._dxf_preview_cancel())
        cancel_btn.place(x=W - 260, y=H - 56)

        confirm_btn = ctk.CTkButton(
            popup, text="Confirm Import", width=120, height=32,
            fg_color="transparent", hover_color="#d1fae5",
            border_width=1, border_color=ACCENT_GREEN,
            text_color=ACCENT_GREEN, font=("Segoe UI", 10, "bold"),
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

    # ── AI GENERATED DESIGN (OpenAI) ──────────────────────────────────────
    def _get_openai_api_key(self):
        HARDCODED_API_KEY = "ADD YOUR API KEY HERE"
        return HARDCODED_API_KEY

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
        pad.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(pad, text="Tell us about your design", bg=BG_CARD, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 12))

        def add_field(label_text, options):
            tk.Label(pad, text=label_text, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 2))
            var = tk.StringVar(value=options[0])
            combo = ctk.CTkComboBox(
                pad, variable=var, values=options, state="readonly",
                width=340, fg_color=BG_INPUT, border_color=GLASS_EDGE,
                button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
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
            fg_color=BG_INPUT, border_color=GLASS_EDGE,
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
                width=360, fg_color=BG_INPUT, border_color=GLASS_EDGE,
                button_color=GLASS_EDGE, button_hover_color=ACCENT_AMBER,
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

        W, H = 420, 260
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
            glass, 4, 4, W - 4, H - 4, radius=20,
            fill=BG_CARD, outline=ACCENT_AMBER, width=2)
        glass.create_text(
            24, 28, text="Colour change required", anchor="w",
            fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"))

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=24, y=56, width=W - 48, height=H - 76)

        tk.Label(
            body,
            text="The nozzle is open at origin.\n"
                 "Empty out the current colour, then load the next one.",
            bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 10),
            justify="left", wraplength=W - 56,
        ).pack(anchor="w", pady=(0, 14))

        next_row = tk.Frame(body, bg=BG_CARD)
        next_row.pack(anchor="w", fill="x", pady=(0, 18))
        tk.Label(
            next_row, text="Next colour:", bg=BG_CARD, fg=TEXT_DIM,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        swatch = COLOUR_PALETTE.get(colour, ACCENT_AMBER)
        sw = tk.Canvas(next_row, width=22, height=22, bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(10, 8))
        sw.create_oval(2, 2, 20, 20, fill=swatch, outline="#ffffff", width=1)
        tk.Label(
            next_row, text=colour or "next colour", bg=BG_CARD, fg=TEXT_PRIMARY,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        ctk.CTkButton(
            body,
            text="Colour emptied, continue",
            command=self._on_colour_emptied_click,
            fg_color="#f97316", hover_color="#fb923c",
            border_width=2, border_color="#facc15",
            text_color="#ffffff", font=("Segoe UI", 12, "bold"),
            height=42, corner_radius=10,
        ).pack(fill="x", pady=(4, 0))

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
                with open(full) as fh:
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

    def show_gallery_popup(self):
        self._close_gallery_popup()
        self.root.update_idletasks()

        W, H = 860, 620
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
        glass.create_text(28, 54, text="Click a design to place it. "
                          "★ My Designs holds patterns you saved with the "
                          "\U0001f4be Save button.",
                          anchor="w", fill=TEXT_DIM, font=("Segoe UI", 9))

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 14, "bold"), cursor="hand2")
        close_lbl.place(x=W-44, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._close_gallery_popup())

        grid_outer = tk.Frame(popup, bg=BG_CARD)
        grid_top = 90
        grid_outer.place(x=26, y=grid_top, width=W-52, height=H-grid_top-24)

        library = self._load_predesigned_dxf_library()
        saved   = self._load_saved_designs()

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

        def _make_card(r, c, name, draw_thumb, on_click, on_delete=None):
            card = tk.Frame(inner, bg=BG_INPUT, cursor="hand2")
            card.grid(row=r, column=c, padx=8, pady=8, sticky="n")
            thumb = tk.Canvas(card, width=140, height=140, bg=CANVAS_BG,
                              highlightthickness=0)
            thumb.pack(padx=6, pady=(6, 2))
            draw_thumb(thumb)
            tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 10, "bold")).pack(pady=(0, 8))
            for widget in [card, thumb] + list(card.winfo_children()):
                widget.bind("<Button-1>", lambda e: on_click())
            if on_delete is not None:
                del_lbl = tk.Label(card, text="✕", bg=BG_INPUT, fg=TEXT_DIM,
                                   font=("Segoe UI", 10, "bold"),
                                   cursor="hand2")
                del_lbl.place(relx=1.0, y=2, x=-6, anchor="ne")
                del_lbl.bind("<Button-1>", lambda e: (on_delete(), "break")[1])

        if not library and not saved:
            tk.Label(inner, text="No designs found. Place funnel.dxf and "
                     "image.dxf in your Downloads folder, or save your own "
                     "with the \U0001f4be Save button.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 12, "bold"),
                     wraplength=W-100, justify="center").grid(
                row=0, column=0, columnspan=cols, pady=60)
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
                tk.Label(inner, text="★ My Designs", bg=BG_CARD,
                         fg=ACCENT_PURP, font=("Segoe UI", 13, "bold")
                         ).grid(row=row, column=0, columnspan=cols,
                                sticky="w", padx=8, pady=(14, 2))
                row += 1
                for idx, (name, full, data) in enumerate(saved):
                    r, c = divmod(idx, cols)
                    all_paths = [p for entry in data['shapes']
                                 for p in entry.get('paths', [])]
                    _make_card(
                        row + r, c, name,
                        lambda t, ap=all_paths:
                            self._draw_flat_paths_thumbnail(t, ap, 70, 70, 56),
                        lambda d=data: self._place_saved_design(d),
                        on_delete=lambda nm=name, fp=full:
                            self._delete_saved_design(nm, fp))

        self._fade(popup, 0.0, 0.97, 0.08)
        popup.lift()
        popup.focus_force()
        popup.grab_set()

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

    def _learn_shell(self, W, H, title, subtitle=None, outline=ACCENT_GREEN):
        """Build a standard Learn-Mode popup shell; return (popup, body frame)."""
        self._close_learn_popup()
        self.root.update_idletasks()
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
        self._learn_popup = popup

        glass = tk.Canvas(popup, width=W, height=H, bg=BG_DARK, highlightthickness=0)
        glass.pack(fill="both", expand=True)
        self._draw_rounded_rect(glass, 4, 4, W-4, H-4, radius=22,
                                fill=BG_CARD, outline=outline, width=2)
        glass.create_text(28, 30, text=title, anchor="w",
                          fill=TEXT_PRIMARY, font=("Segoe UI", 16, "bold"))
        top = 60
        if subtitle:
            glass.create_text(28, 56, text=subtitle, anchor="w", fill=TEXT_DIM,
                              font=("Segoe UI", 9), width=W-90)
            top = 84

        close_lbl = tk.Label(popup, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 14, "bold"), cursor="hand2")
        close_lbl.place(x=W-42, y=20)
        close_lbl.bind("<Button-1>", lambda e: self._exit_learn_mode())

        body = tk.Frame(popup, bg=BG_CARD)
        body.place(x=26, y=top, width=W-52, height=H-top-22)

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
        controls.pack(side="bottom", fill="x", pady=(12, 0))
        self._learn_video_pause_btn = self._color_button(
            controls, "Pause", self._toggle_learn_video_pause,
            "#334155", width=110, height=44, font_size=12)
        self._learn_video_pause_btn.pack(side="left")
        # Live from the first frame, so it doubles as "skip the video".
        self._color_button(
            controls, "Start Interactive learning →", self._open_learn_gallery,
            ACCENT_GREEN, width=max(160, (W - 52) - 122), height=44,
            font_size=13, text_color="#06281c").pack(side="left", padx=(12, 0))

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
        W, H = 720, 560
        popup, body = self._learn_shell(
            W, H, "Choose a design to learn",
            "Pick a rangoli. The robot will draw it one part at a time so you "
            "can copy each part by hand.", outline=ACCENT_BLUE)

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
            card.grid(row=r, column=c, padx=8, pady=8, sticky="n")
            thumb = tk.Canvas(card, width=150, height=150, bg=CANVAS_BG,
                              highlightthickness=0)
            thumb.pack(padx=6, pady=(6, 2))
            self._learn_draw_preview(thumb, paths, 75, 75, 62)
            tk.Label(card, text=name, bg=BG_INPUT, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 10, "bold")).pack()
            diff = meta.get('difficulty', '')
            tk.Label(card, text=diff, bg=BG_INPUT,
                     fg=DIFFICULTY_COLORS.get(diff, TEXT_DIM),
                     font=("Segoe UI", 9, "bold")).pack(pady=(0, 8))
            for w in [card, thumb] + list(card.winfo_children()):
                w.bind("<Button-1>", lambda e, nm=name: self._choose_learn_design(nm))

    def _choose_learn_design(self, name):
        """Split the design between student and robot, then start the lesson."""
        cx = MARGIN_L + GRAPH_W // 2
        cy = MARGIN_T + GRAPH_H // 2
        size = min(GRAPH_W, GRAPH_H) * 0.35
        paths = PRESET_DESIGNS[name]['generator'](cx, cy, size)
        self._learn_design = name
        self._learn_parts = [p for p in paths if len(p) >= 2]
        if not self._learn_parts:
            self.show_hint_popup("That design has no parts to teach")
            return
        self._learn_done_parts = set()
        self._learn_robot_idx = None
        self._learn_streaming = False
        self._learn_next_free = 0
        self._learn_student_idx = self._learn_take_next()
        self.log_to_console(
            f"Learn Mode: '{name}' has {len(self._learn_parts)} parts. You and "
            f"the robot fill them in together.", "info")
        self._open_learn_step()

    def _learn_take_next(self):
        """Claim the next part nobody is working on yet (None when exhausted)."""
        if self._learn_next_free >= len(self._learn_parts):
            return None
        idx = self._learn_next_free
        self._learn_next_free += 1
        return idx

    @staticmethod
    def _learn_part_color(idx):
        """(name, hex) colour assigned to part ``idx`` — cycles the palette."""
        return LEARN_PART_COLORS[idx % len(LEARN_PART_COLORS)]

    @staticmethod
    def _learn_tf_for(paths, cx, cy, size):
        """Transform that fits ``paths`` into a ``size``-radius preview box."""
        pts = [pt for p in paths for pt in p]
        if not pts:
            return None
        xs = [x for x, _ in pts]
        ys = [y for _, y in pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
        return (cx, cy, (size * 2) / span,
                (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)

    @staticmethod
    def _learn_map(tf, x, y):
        cx, cy, scale, mid_x, mid_y = tf
        return cx + (x - mid_x) * scale, cy - (y - mid_y) * scale

    def _learn_draw_preview(self, canvas, paths, cx, cy, size):
        """Gallery thumbnail — every part in its own colour."""
        tf = self._learn_tf_for(paths, cx, cy, size)
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
        try: prev.tag_raise("learn_robot")
        except tk.TclError: pass

    def _learn_status_text(self):
        if self._learn_robot_idx is not None:
            name = self._learn_part_color(self._learn_robot_idx)[0]
            return (f"\U0001f916 Robot is drawing part "
                    f"{self._learn_robot_idx + 1} ({name})… hold on, your next "
                    f"part comes up the moment it finishes.")
        if self._learn_next_free < len(self._learn_parts):
            return ("\U0001f916 Robot is waiting its turn — press Done when "
                    "your part is finished.")
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
        W, H = 640, 590
        popup, body = self._learn_shell(
            W, H, "Your turn — draw this part",
            f"Part {idx + 1} of {total} of '{self._learn_design}' — draw the "
            f"highlighted {col_name.lower()} part with your bottle, then press "
            f"Done and the robot takes the next one.")

        upper = tk.Frame(body, bg=BG_CARD)
        upper.pack(fill="both", expand=True)

        prev = tk.Canvas(upper, width=210, height=210, bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(side="left", anchor="n", padx=(0, 16), pady=(2, 0))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 105, 105, 92)
        self._learn_render_preview()

        right = tk.Frame(upper, bg=BG_CARD)
        right.pack(side="left", fill="both", expand=True)

        # Colour chip — which powder to load in the bottle for this part.
        chip = tk.Frame(right, bg=BG_CARD)
        chip.pack(fill="x", anchor="w", pady=(0, 8))
        sw = tk.Canvas(chip, width=18, height=18, bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(0, 8))
        sw.create_oval(2, 2, 16, 16, fill=col_hex, outline="#ffffff", width=1)
        tk.Label(chip, text=f"Your part: {col_name} powder", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", 11, "bold")).pack(side="left")

        tk.Label(right, text="How to draw it:", bg=BG_CARD, fg=ACCENT_GREEN,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 6))
        for i, line in enumerate(self._learn_step_instructions(idx), 1):
            r = tk.Frame(right, bg=BG_CARD)
            r.pack(fill="x", anchor="w", pady=(0, 5))
            tk.Label(r, text=str(i), bg=ACCENT_GREEN, fg="#06281c",
                     font=("Segoe UI", 9, "bold"), width=2).pack(
                side="left", anchor="n", padx=(0, 8))
            tk.Label(r, text=line, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 10), justify="left",
                     wraplength=W-330).pack(side="left", anchor="w")

        footer = tk.Frame(body, bg=BG_CARD)
        footer.pack(fill="x", side="bottom", pady=(8, 0))
        pick = tk.Label(footer, text="← Pick another design", bg=BG_CARD,
                        fg=TEXT_DIM, font=("Segoe UI", 9, "bold"), cursor="hand2")
        pick.pack(side="left")
        pick.bind("<Button-1>", lambda e: self._open_learn_gallery())
        basics = tk.Label(footer, text="Re-read the 10 basics", bg=BG_CARD,
                          fg=TEXT_DIM, font=("Segoe UI", 9, "bold"), cursor="hand2")
        basics.pack(side="right")
        basics.bind("<Button-1>", lambda e: self._show_learn_intro())

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom", pady=(10, 0))
        self._color_button(
            btns, "✓ Done drawing my part", self._learn_done_my_part,
            ACCENT_GREEN, width=250, height=46, font_size=13).pack(side="left")

        self._learn_status = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_PURP,
            font=("Segoe UI", 10, "bold"), anchor="w", justify="left",
            wraplength=W-70)
        self._learn_status.pack(fill="x", side="bottom", pady=(10, 0))
        self._learn_update_status()

    def _open_learn_robot_turn(self):
        """The robot's turn — the student watches until it finishes."""
        idx = self._learn_robot_idx
        if idx is None:
            self._open_learn_camera_step()
            return
        total = len(self._learn_parts)
        col_name, col_hex = self._learn_part_color(idx)
        W, H = 520, 470
        _, body = self._learn_shell(
            W, H, "Robot's turn — watch",
            f"Part {idx + 1} of {total} — the robot is drawing its "
            f"{col_name.lower()} part. Watch how the line flows; your next "
            f"part appears as soon as it finishes.", outline=ACCENT_PURP)

        prev = tk.Canvas(body, width=210, height=210, bg=CANVAS_BG,
                         highlightthickness=0)
        prev.pack(pady=(2, 12))
        self._learn_prev = prev
        self._learn_tf = self._learn_tf_for(self._learn_parts, 105, 105, 92)
        self._learn_render_preview()

        chip = tk.Frame(body, bg=BG_CARD)
        chip.pack(pady=(0, 8))
        sw = tk.Canvas(chip, width=18, height=18, bg=BG_CARD, highlightthickness=0)
        sw.pack(side="left", padx=(0, 8))
        sw.create_oval(2, 2, 16, 16, fill=col_hex, outline="#ffffff", width=1)
        tk.Label(chip, text=f"Robot's part: {col_name}", bg=BG_CARD,
                 fg=TEXT_PRIMARY, font=("Segoe UI", 11, "bold")).pack(side="left")

        self._learn_status = tk.Label(
            body, text="", bg=BG_CARD, fg=ACCENT_PURP,
            font=("Segoe UI", 10, "bold"), wraplength=W-70, justify="center")
        self._learn_status.pack()
        self._learn_update_status()

        self._learn_animate_robot(idx)

    def _learn_step_instructions(self, step):
        return LEARN_STEP_SETS[step % len(LEARN_STEP_SETS)]

    def _learn_done_my_part(self):
        """Student finished their part — hand the turn to the robot and watch.

        The student's next part is NOT revealed yet: it pops up only once the
        robot has finished, so the two of them strictly alternate.
        """
        idx = self._learn_student_idx
        if idx is None:
            return
        self._learn_done_parts.add(idx)
        self._learn_student_idx = None
        self.log_to_console(f"Learn Mode: you finished part {idx + 1}.", "recv")

        nxt = self._learn_take_next()
        if nxt is None:
            self._open_learn_camera_step()
            return
        self._learn_start_robot(nxt)
        self._open_learn_robot_turn()

    def _learn_start_robot(self, idx):
        """Robot begins drawing part ``idx`` — for real if a port is connected,
        and always as an animated trace in the preview."""
        self._learn_robot_idx = idx
        name = self._learn_part_color(idx)[0]
        self._learn_streaming = False
        if self.port_var.get() and not self.is_sending:
            part = self._learn_parts[idx]
            _SPEED_MAP = {"Low": 150, "Medium": 200, "High (default)": 250}
            f = _SPEED_MAP.get(self.feed_rate.get(), 250)
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

        self._learn_student_idx = self._learn_take_next()
        if self._learn_student_idx is None:
            self._open_learn_camera_step()
            return
        self.show_hint_popup("Robot's done — your turn!")
        self._open_learn_step()

    def _open_learn_robot_error(self, idx):
        """The robot's part did not complete — never advance the student on a
        failed print; let them retry it or take the part over by hand."""
        col_name = self._learn_part_color(idx)[0]
        W, H = 520, 340
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
            ACCENT_PURP, width=W-52, height=44, font_size=12).pack(pady=(10, 8))
        self._color_button(
            body, "I'll draw this part myself", _take_over,
            ACCENT_GREEN, width=W-52, height=44, font_size=12).pack()

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
        W, H = 520, 400
        _, body = self._learn_shell(
            W, H, "Set up the rangoli camera",
            "A USB camera mounted over the mat photographs your finished "
            "rangoli. Plug it in and install it here — the choice is "
            "remembered for next time.",
            outline=ACCENT_CYAN)

        self._camera_status = tk.Label(
            body, text=self._camera_installed_text(),
            bg=BG_CARD, fg=ACCENT_GREEN if self._camera_index is not None
            else TEXT_DIM, font=("Segoe UI", 10, "bold"),
            justify="left", wraplength=W-70)
        self._camera_status.pack(anchor="w", pady=(0, 6))

        self._camera_list = tk.Frame(body, bg=BG_CARD)
        self._camera_list.pack(fill="both", expand=True)
        self._render_camera_devices()

        btns = tk.Frame(body, bg=BG_CARD)
        btns.pack(fill="x", side="bottom")
        self._color_button(
            btns, "\U0001f50d Scan for USB cameras", self._start_camera_scan,
            ACCENT_CYAN, width=W-52, height=40, font_size=12).pack(pady=(8, 6))
        row = tk.Frame(btns, bg=BG_CARD)
        row.pack(fill="x")
        self._color_button(
            row, "\U0001f4f7 Take the photo", self._learn_take_photo,
            ACCENT_PURP, width=(W-72)//3, height=38,
            font_size=11).pack(side="left")
        self._color_button(
            row, "\U0001f4c1 Upload image", self._learn_upload_photo,
            ACCENT_CYAN, width=(W-72)//3, height=38,
            font_size=11).pack(side="left", padx=6)
        self._color_button(
            row, "Continue →", self._show_learn_evaluation,
            ACCENT_GREEN, width=(W-72)//3, height=38,
            font_size=11).pack(side="right")

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
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))
            return
        if not self._camera_devices:
            tk.Label(holder,
                     text="Scan to see the cameras connected over USB. "
                          "Nothing listed yet.",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 9),
                     justify="left", wraplength=430).pack(anchor="w",
                                                          pady=(4, 0))
            return

        for idx, label in self._camera_devices:
            chosen = (idx == self._camera_index)
            row = tk.Frame(holder, bg=BG_INPUT, highlightthickness=1,
                           highlightbackground=ACCENT_GREEN if chosen
                           else GLASS_EDGE, cursor="hand2")
            row.pack(fill="x", pady=3)
            tk.Label(row, text=("● " if chosen else "○ ") + label,
                     bg=BG_INPUT, fg=TEXT_PRIMARY if chosen else TEXT_DIM,
                     font=("Segoe UI", 10, "bold" if chosen else "normal"),
                     anchor="w").pack(side="left", padx=10, pady=7)
            tk.Label(row, text="installed" if chosen else "click to install",
                     bg=BG_INPUT, fg=ACCENT_GREEN if chosen else TEXT_DIM,
                     font=("Segoe UI", 8)).pack(side="right", padx=10)
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
        complexity = PRESET_DESIGNS.get(self._learn_design, {}).get(
            "difficulty", "Medium")
        return {"name": design, "complexity": complexity, "out_of": 10}

    def _learn_fallback_verdict(self):
        """Used when there's no photo / no API key / the AI call fails."""
        return {"score": 9, "improvements": [
            "Make the white outlines more uniform.",
            "Improve the smoothness of a few curved edges.",
            "Ensure slightly more consistent spacing in the inner "
            "decorative patterns.",
        ]}

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
        try:
            ai = self._score_learn_photo_ai(
                api_key, photo_path, facts["name"], facts["complexity"])
            verdict, note = {**facts, **ai}, None
            self.log_to_console(f"Learn Mode: AI scored {ai['score']}/10.", "recv")
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
        self.root.after(0, lambda: self._render_learn_result(popup, verdict, note))

    def _show_learn_evaluation(self):
        """Neon 'RANGOLI RESULT' card. Shows an 'Evaluating…' state, sends the
        camera photo to the AI on a background thread, then fills in the real
        score. Falls back to sample values when there's no photo or no key."""
        import math, random
        import tkinter.font as _tkfont

        # ── geometry (fixed up front so the window never needs resizing) ──────
        W, pad, ib = 540, 22, 56
        tl = pad + 14 + ib + 18
        ROW_H, GAP, IMPR_H, BTN_H = 74, 12, 152, 44
        y = 150
        name_y  = y; y += ROW_H + GAP
        comp_y  = y; y += ROW_H + GAP
        score_y = y; y += ROW_H + GAP
        impr_y  = y
        note_y  = impr_y + IMPR_H + 8
        btn_y   = impr_y + IMPR_H + 26
        H = btn_y + BTN_H * 2 + 12 + 18
        self._eval_layout = dict(
            W=W, H=H, pad=pad, ib=ib, tl=tl, ROW_H=ROW_H, IMPR_H=IMPR_H,
            BTN_H=BTN_H, name_y=name_y, comp_y=comp_y, score_y=score_y,
            impr_y=impr_y, note_y=note_y, btn_y=btn_y)

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
        self._draw_rounded_rect(cv, 8, 8, W - 8, H - 8, radius=26, fill=CARD_BG)

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
                             font=("Segoe UI", 13, "bold"), cursor="hand2")
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
        tfont = _tkfont.Font(family="Segoe UI", size=26, weight="bold")
        title = "RANGOLI RESULT"
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
                font=("Segoe UI", 14, "bold"), tags="eval")
            cv.create_text(W // 2, 362, text="The AI is judging your photo…",
                           fill=TEXT_DIM, font=("Segoe UI", 10), tags="eval")

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
                note=f"{why} — showing sample values.")

        self._fade(popup, 0.0, 0.98, 0.08)
        popup.lift()
        popup.focus_force()

    def _render_learn_result(self, popup, verdict, note=None):
        """Paint the real result rows + buttons onto the card. Safe to call from
        a background-thread callback: it no-ops if the popup has been closed."""
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
        _blend = self._eval_blend

        cv.delete("eval")                    # clear the loading text

        def _field(y0, h, accent, glow=False):
            self._draw_rounded_rect(
                cv, pad, y0, W - pad, y0 + h, radius=16, fill="#101018",
                outline=_blend(accent, 0.5 if glow else 0.3),
                width=2 if glow else 1, tags="eval")

        def _icon(x, y0, accent, kind):
            self._draw_rounded_rect(cv, x, y0, x + ib, y0 + ib, radius=12,
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
                                            radius=3, outline=accent, width=2,
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
                       fill=TEXT_DIM, font=("Segoe UI", 11), tags="eval")
        cv.create_text(tl, name_y + 49, text=verdict["name"], anchor="w",
                       fill=TEXT_PRIMARY, font=("Segoe UI", 17, "bold"), tags="eval")
        # Complexity
        _field(comp_y, ROW_H, ACCENT_CYAN)
        _icon(pad + 14, comp_y + (ROW_H - ib)//2, ACCENT_CYAN, "bars")
        cv.create_text(tl, comp_y + 24, text="Complexity", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", 11), tags="eval")
        cv.create_text(tl, comp_y + 49, text=verdict["complexity"], anchor="w",
                       fill=TEXT_PRIMARY, font=("Segoe UI", 17, "bold"), tags="eval")
        # Total Score
        _field(score_y, ROW_H, ACCENT_GREEN, glow=True)
        _icon(pad + 14, score_y + (ROW_H - ib)//2, ACCENT_GREEN, "star")
        cv.create_text(tl, score_y + 22, text="Total Score", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", 11), tags="eval")
        sfont = _tkfont.Font(family="Segoe UI", size=24, weight="bold")
        cv.create_text(tl, score_y + 50, text=str(verdict["score"]), anchor="w",
                       fill=ACCENT_GREEN, font=sfont, tags="eval")
        cv.create_text(tl + sfont.measure(str(verdict["score"])) + 6, score_y + 52,
                       text=f"/ {verdict['out_of']}", anchor="w",
                       fill=TEXT_PRIMARY, font=("Segoe UI", 14, "bold"), tags="eval")
        # Improvements
        _field(impr_y, IMPR_H, ACCENT_AMBER, glow=True)
        _icon(pad + 14, impr_y + 16, ACCENT_AMBER, "bulb")
        cv.create_text(tl, impr_y + 26, text="Improvements", anchor="w",
                       fill=TEXT_DIM, font=("Segoe UI", 11), tags="eval")
        ty = impr_y + 52
        for tip in verdict["improvements"][:3]:
            cv.create_oval(tl, ty + 5, tl + 6, ty + 11, fill=ACCENT_AMBER,
                           outline="", tags="eval")
            item = cv.create_text(tl + 16, ty, text=tip, anchor="nw",
                                  fill=TEXT_PRIMARY, font=("Segoe UI", 11),
                                  width=W - tl - pad - 20, tags="eval")
            bb = cv.bbox(item)
            ty = (bb[3] if bb else ty + 20) + 8

        # optional note (fallback reason)
        if note:
            cv.create_text(W // 2, note_y, text=note, fill=TEXT_DIM,
                           font=("Segoe UI", 9), tags="eval")

        # buttons (original completion actions)
        b1 = self._color_button(popup, "🏠  Learn another design",
                                self._open_learn_gallery, ACCENT_GREEN,
                                width=W - 2*pad, height=BTN_H, font_size=13,
                                corner_radius=14)
        b1.place(x=pad, y=btn_y)
        b2 = self._color_button(popup, "Finish", self._exit_learn_mode,
                                ACCENT_PURP, width=W - 2*pad, height=BTN_H,
                                font_size=13, corner_radius=14)
        b2.place(x=pad, y=btn_y + BTN_H + 12)
    def _show_learn_complete(self):
        W, H = 460, 300
        _, body = self._learn_shell(
            W, H, "Rangoli complete! \U0001f389",
            f"You drew every part of '{self._learn_design}' by hand. "
            f"Beautiful work!", outline=ACCENT_PINK)
        tk.Label(body,
                 text="Keep practising — each design gets easier as your hand "
                      "learns the strokes.",
                 bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 10),
                 justify="left", wraplength=W-70).pack(anchor="w", pady=(0, 16))
        self._color_button(
            body, "Learn another design", self._open_learn_gallery,
            ACCENT_GREEN, width=W-52, height=42, font_size=12).pack(pady=(0, 8))
        self._color_button(
            body, "Finish", self._exit_learn_mode,
            ACCENT_PURP, width=W-52, height=42, font_size=12).pack()

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
        mx = max(0, min(MAX_X, ((event.x - MARGIN_L) / GRAPH_W) * MAX_X))
        my = max(0, min(MAX_Y, ((CANVAS_H - MARGIN_B - event.y) / GRAPH_H) * MAX_Y))
        self.coord_label.config(text=f"X: {mx:.2f}  Y: {my:.2f}")
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
                if 'paths' in s:
                    s['paths'] = [[(cx + dx, cy + dy) for cx, cy in path]
                                  for path in s['paths']]
            self.redraw()
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

        outer = tk.Frame(popup, bg=BG_CARD, padx=10, pady=8)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        body = tk.Frame(outer, bg=BG_CARD)
        body.pack(fill="both", expand=True)

        def _clear_body():
            for w in body.winfo_children():
                w.destroy()

        def _header(text):
            head = tk.Frame(body, bg=BG_CARD)
            head.pack(fill="x", pady=(0, 6))
            tk.Label(head, text=text, bg=BG_CARD, fg=TEXT_PRIMARY,
                     font=("Segoe UI", 11, "bold")).pack(side="left")
            close = tk.Label(head, text="✕", bg=BG_CARD, fg=TEXT_DIM,
                             font=("Segoe UI", 11, "bold"), cursor="hand2")
            close.pack(side="right")
            close.bind("<Button-1>", lambda e: self._close_edit_popup())

        def _menu_btn(text, cmd, color):
            btn = self._color_button(body, text, cmd, color,
                                     width=W - 24, height=32, font_size=11)
            btn.pack(fill="x", pady=(0, 6))
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
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 10),
                     wraplength=W - 30, justify="left").pack(
                anchor="w", pady=(0, 6))
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
                     fg=TEXT_DIM, font=("Segoe UI", 10)).pack(
                anchor="w", pady=(0, 6))
            _menu_btn("Yes, delete", do_delete, "#dc2626")
            _menu_btn("Cancel", show_main, "#4b5563")
            _fit()

        def show_draw_order():
            _clear_body()
            _header("Draw order")
            tk.Label(body, text="When should the bot draw this stroke?",
                     bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 10),
                     wraplength=W - 30, justify="left").pack(
                anchor="w", pady=(0, 6))
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
                    width=(W - 30) // 2, height=28, font_size=10,
                    text_color="#0f172a" if name in ("White", "Yellow")
                    else "#ffffff")
                btn.grid(row=n // 2, column=n % 2, padx=2, pady=2, sticky="ew")
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

    # ── Pen tool + mirror symmetry ───────────────────────────────────────────
    def _pen_btn_colour(self):
        return "#f43f5e" if self.pen_mode_var.get() else "#0d9488"

    def _pen_btn_label(self):
        return "✏ Pen off" if self.pen_mode_var.get() else "✏ Pen"

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
        paths = [pts] + self._mirror_paths(pts)
        colour = self.shape_colour_var.get() \
            if self.multi_colour_var.get() else None
        self.shapes.append({'type': 'Pen', 'paths': paths,
                            'x': pts[0][0], 'y': pts[0][1],
                            'size': 0, 'colour': colour})
        self.selected_shape_index = len(self.shapes) - 1
        self.redraw()
        mirrored = "" if len(paths) == 1 else f" (+{len(paths)-1} mirrored)"
        self.log_to_console(f"Pen stroke added{mirrored}.", "info")

    def _mirror_transforms(self):
        """Reflection functions for the active mirror mode, about the canvas
        centre: 2-way = vertical axis; 4-way adds horizontal; 8-way adds
        both diagonals."""
        mode = self.mirror_mode_var.get()
        cx = MARGIN_L + GRAPH_W / 2.0
        cy = MARGIN_T + GRAPH_H / 2.0
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

    def _mirror_paths(self, pts):
        return [[f(x, y) for x, y in pts] for f in self._mirror_transforms()]

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
        except tk.TclError:
            pass
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
        max_frames = 3500
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

            if s['type'] == "Square":
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
        if getattr(self, "_sim_running", False):
            self._stop_simulation()
        self._close_edit_popup()
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
        if self._ai_fx_running:
            self.toggle_ai_effects()
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
        _SPEED_MAP = {"Low": 150, "Medium": 200, "High (default)": 250}
        f = _SPEED_MAP.get(self.feed_rate.get(), 250)
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
    def start_gcode_streaming(self):
        if self.is_sending: return
        if not self.port_var.get():
            self.log_to_console("Error: Choose a serial port first.", "err")
            return
        self.is_sending = True
        self.is_paused = False
        self.pause_event.set()
        self.pause_btn.configure(text="⏸", state="normal")
        self.send_btn.configure(
            state="disabled", fg_color="#0f766e", hover_color="#0f766e",
            text_color=TEXT_DIM)
        threading.Thread(target=self.send_gcode, daemon=True).start()

    def toggle_pause(self):
        if not self.is_sending: return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.pause_event.clear()
            self.pause_btn.configure(text="▶")
            self.log_to_console("Print paused.", "info")
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="⏸")
            self.log_to_console("Print resumed.", "info")

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
            with open(path, "r") as fh:
                lines = [l.strip() for l in fh if l.strip()]
            total = max(len(lines), 1)

            for idx, clean in enumerate(lines):
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
                self.pause_event.wait()
                self.log_to_console(f"→ {clean}", "send")
                ser.write((clean + "\n").encode())
                silent = 0
                while True:
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

            ser.close()
            if grbl_ok:
                grbl_done = True
                self.progress_bar.set(1.0)
                self.sidebar_progress_bar.set(1.0)
                self.root.after(
                    0, lambda: self.sidebar_pct_label.config(text="100%"))
                self.log_to_console("Job complete.", "recv")
            else:
                self.log_to_console(
                    "Job stopped early — GRBL reported a problem.", "err")
        except Exception as e:
            grbl_ok = False
            self.log_to_console(f"Connection Error: {e}", "err")
        finally:
            self.is_sending = False
            self.is_paused = False
            self.pause_event.set()
            self.root.after(0, lambda: self.pause_btn.configure(
                text="⏸", state="disabled"))
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
