"""
Prolabs Viewer - Tkinter GUI
Plays a.mp4/a.mov on loop. Press SPACE to switch to b.png/b.jpeg.
Press SPACE again to resume video. Press Q or Esc to quit.
"""

import os
import cv2
import tkinter as tk
from PIL import Image, ImageTk

DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

VIDEO_CANDIDATES = [
    os.path.join(DOWNLOADS_DIR, "a.mov"),
    os.path.join(DOWNLOADS_DIR, "a.mp4"),
]
IMAGE_CANDIDATES = [
    os.path.join(DOWNLOADS_DIR, "b.png"),
    os.path.join(DOWNLOADS_DIR, "b.jpeg"),
    os.path.join(DOWNLOADS_DIR, "b.jpg"),
]


def find_existing(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"None of these files found: {candidates}")


class ViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Prolabs Viewer")
        self.root.configure(bg="black")

        self.video_path = find_existing(VIDEO_CANDIDATES)
        self.image_path = find_existing(IMAGE_CANDIDATES)

        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise IOError(f"Cannot open video: {self.video_path}")

        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        PLAYBACK_SPEED = 2.0  # 2x speed
        self.delay_ms = max(1, int(1000 / (fps * PLAYBACK_SPEED)))

        # Load the static image once, converted to PIL
        pil_image = Image.open(self.image_path).convert("RGB")
        self.pil_image_original = pil_image

        # Canvas fills the window
        self.canvas = tk.Label(root, bg="black")
        self.canvas.pack(fill="both", expand=True)

        self.showing_image = False
        self.current_photo = None  # keep reference to avoid garbage collection

        # Bindings
        self.root.bind("<space>", self.toggle_view)
        self.root.bind("<Escape>", lambda e: self.quit_app())
        self.root.bind("q", lambda e: self.quit_app())
        self.root.bind("<Configure>", self.on_resize)

        self.win_width = 800
        self.win_height = 600
        self.root.geometry(f"{self.win_width}x{self.win_height}")

        self.update_frame()

    def on_resize(self, event):
        # Track window size for scaling content to fit
        if event.widget == self.root:
            self.win_width = event.width
            self.win_height = event.height

    def toggle_view(self, event=None):
        self.showing_image = not self.showing_image

    def fit_image(self, pil_img):
        """Resize a PIL image to fit the current window, preserving aspect ratio."""
        img_w, img_h = pil_img.size
        win_w, win_h = max(self.win_width, 1), max(self.win_height, 1)
        scale = min(win_w / img_w, win_h / img_h)
        new_w, new_h = max(1, int(img_w * scale)), max(1, int(img_h * scale))
        return pil_img.resize((new_w, new_h), Image.LANCZOS)

    def update_frame(self):
        if self.showing_image:
            frame_pil = self.fit_image(self.pil_image_original)
        else:
            ret, frame = self.cap.read()
            if not ret:
                # Loop back to start
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self.cap.read()
                if not ret:
                    self.root.after(self.delay_ms, self.update_frame)
                    return
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame_pil = Image.fromarray(frame_rgb)
            frame_pil = self.fit_image(frame_pil)

        self.current_photo = ImageTk.PhotoImage(frame_pil)
        self.canvas.configure(image=self.current_photo)

        self.root.after(self.delay_ms, self.update_frame)

    def quit_app(self):
        self.cap.release()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ViewerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    root.mainloop()


if __name__ == "__main__":
    main()
