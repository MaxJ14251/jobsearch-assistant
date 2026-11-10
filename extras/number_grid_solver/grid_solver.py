"""
Number Grid Solver - press F8 to read a 1..16 number grid off the screen and
click the numbers in ascending order. Press Esc to quit.

Pipeline (all in memory, no files written unless DEBUG_SAVE_IMAGES is on):
    F8 -> mss screenshot of the primary monitor (or CAPTURE_REGION)
       -> OCR (EasyOCR by default, Tesseract optional) restricted to digits
       -> keep integers 1..16, one box per number (highest confidence wins)
       -> sort 1..16 -> move + left-click each box centre with your delays

Run from a terminal:  python grid_solver.py
The OCR model is loaded and warmed up once at start-up, so the first F8 is as
fast as every later one. Wait for "Ready" before pressing F8.

Note: automating input can break a game's terms of service, especially in
online games. Use it where that's acceptable to you.
"""

import ctypes
import os
import random
import re
import sys
import threading
import time

# ---------------------------------------------------------------------------
# DPI awareness MUST be set before anything else touches the screen, so that
# screenshot pixels and mouse coordinates are the same physical pixels even
# with Windows display scaling at 125% / 150% etc. (See README troubleshooting.)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import mss
import numpy as np
from pynput import keyboard, mouse

# ===========================================================================
# SETTINGS - edit these
# ===========================================================================

HOTKEY = keyboard.Key.f8          # trigger key
EXIT_KEY = keyboard.Key.esc       # quits the program (also aborts a running click sequence)

# --- Click speed -----------------------------------------------------------
# CLICK_DELAY_MS: pause AFTER each click before moving to the next number.
#   Lower = faster. 16 numbers x (CLICK_DELAY_MS + MOVE_DURATION_MS) is the
#   total click time. e.g. 60 + 40 = 100 ms per number -> ~1.6 s for all 16.
# CLICK_DELAY_JITTER_MS: random +/- added to every delay so the rhythm is not
#   perfectly uniform. Set to 0 for an exact, fixed delay.
CLICK_DELAY_MS = 60
CLICK_DELAY_JITTER_MS = 20

# MOVE_DURATION_MS: how long the cursor takes to glide to the next number.
#   0 = jump instantly. MOVE_STEPS = how many intermediate points the glide
#   uses (more = smoother, a few is plenty).
MOVE_DURATION_MS = 40
MOVE_STEPS = 8

# MOUSE_DOWN_MS: how long the left button is held for each click. Some games
#   drop clicks that are too short; 15-40 ms is a safe range.
MOUSE_DOWN_MS = 25

# CLICK_POSITION_JITTER_PX: random offset (pixels) from the exact centre of
#   each number. Keep it smaller than half a grid cell. 0 = dead centre.
CLICK_POSITION_JITTER_PX = 3

# --- Where to look -----------------------------------------------------------
# None = whole primary monitor. Restricting to the grid area makes OCR much
# faster and stops it picking up HUD numbers (health, ammo, timers...).
# Format: dict(left=..., top=..., width=..., height=...) in screen pixels.
# Use DEBUG_SAVE_IMAGES once to find these values.
CAPTURE_REGION = None
# CAPTURE_REGION = dict(left=660, top=240, width=600, height=600)

# --- OCR -------------------------------------------------------------------
OCR_ENGINE = "easyocr"            # "easyocr" (more accurate) or "tesseract" (lighter)
USE_GPU = True                    # EasyOCR uses an NVIDIA GPU if CUDA torch is installed; falls back to CPU
MIN_CONFIDENCE = 0.30             # ignore OCR reads below this (0..1)
UPSCALE = 1.0                     # >1 (e.g. 1.5 or 2) helps tiny digits, costs speed
# Path to tesseract.exe if it is not on PATH (only used when OCR_ENGINE="tesseract")
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# If fewer than 16 numbers are found: True = click nothing (safe),
# False = click the ones that were found, in order.
REQUIRE_ALL_16 = False

# Save the screenshot plus an annotated copy (boxes + numbers) next to this
# script on every F8. Turn on while setting up, off for speed.
DEBUG_SAVE_IMAGES = False

# ===========================================================================

NUMBERS = range(1, 17)
_abort = threading.Event()
_busy = threading.Lock()
_mouse = mouse.Controller()


# ----------------------------------------------------------------------------- capture
def grab_screen():
    """Return (BGR image as numpy array, (offset_x, offset_y)) of the target area."""
    with mss.mss() as sct:
        mon = sct.monitors[1]  # [1] is the primary monitor; [0] is all monitors combined
        if CAPTURE_REGION:
            area = {
                "left": mon["left"] + CAPTURE_REGION["left"],
                "top": mon["top"] + CAPTURE_REGION["top"],
                "width": CAPTURE_REGION["width"],
                "height": CAPTURE_REGION["height"],
            }
        else:
            area = mon
        shot = sct.grab(area)
    img = np.asarray(shot)[:, :, :3]  # BGRA -> BGR (drop alpha)
    return np.ascontiguousarray(img), (area["left"], area["top"])


# ----------------------------------------------------------------------------- OCR
class EasyOcrEngine:
    def __init__(self):
        import easyocr
        try:
            self.reader = easyocr.Reader(["en"], gpu=USE_GPU, verbose=False)
        except Exception:
            self.reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    def read(self, img):
        """Return list of (text, confidence, (cx, cy)) in image pixels."""
        results = self.reader.readtext(
            img,
            allowlist="0123456789",
            detail=1,
            paragraph=False,
            width_ths=0.2,   # low = don't merge neighbouring grid cells ("3" "7" -> "37")
            mag_ratio=1.0,
        )
        out = []
        for box, text, conf in results:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append((text, float(conf), ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)))
        return out


class TesseractEngine:
    def __init__(self):
        import pytesseract
        if TESSERACT_CMD and os.path.exists(TESSERACT_CMD):
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        self.pt = pytesseract
        self.config = "--psm 11 --oem 3 -c tessedit_char_whitelist=0123456789"

    def read(self, img):
        gray = img.mean(axis=2).astype(np.uint8)
        data = self.pt.image_to_data(gray, config=self.config, output_type=self.pt.Output.DICT)
        out = []
        for i, text in enumerate(data["text"]):
            conf = float(data["conf"][i])
            if not text.strip() or conf < 0:
                continue
            cx = data["left"][i] + data["width"][i] / 2
            cy = data["top"][i] + data["height"][i] / 2
            out.append((text, conf / 100.0, (cx, cy)))
        return out


def make_engine():
    if OCR_ENGINE == "tesseract":
        return TesseractEngine()
    return EasyOcrEngine()


def resize(img, factor):
    if factor == 1.0:
        return img
    import cv2  # installed with easyocr (opencv-python-headless)
    return cv2.resize(img, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)


def pick_numbers(reads, min_conf=MIN_CONFIDENCE):
    """From raw OCR reads keep one position per number 1..16 (best confidence).

    Returns {number: (cx, cy)} in the same pixel space as the reads.
    """
    best = {}
    for text, conf, centre in reads:
        digits = re.sub(r"\D", "", text)
        if not digits or conf < min_conf:
            continue
        n = int(digits)
        if n not in NUMBERS:
            continue
        if n not in best or conf > best[n][0]:
            best[n] = (conf, centre)
    return {n: c for n, (conf, c) in best.items()}


# ----------------------------------------------------------------------------- mouse
def _sleep_ms(ms):
    if ms > 0:
        time.sleep(ms / 1000.0)


def glide_to(x, y):
    """Move the cursor to (x, y) over MOVE_DURATION_MS with a slight ease-out."""
    sx, sy = _mouse.position
    if MOVE_DURATION_MS <= 0 or MOVE_STEPS <= 1:
        _mouse.position = (x, y)
        return
    step_time = MOVE_DURATION_MS / 1000.0 / MOVE_STEPS
    for i in range(1, MOVE_STEPS + 1):
        t = i / MOVE_STEPS
        t = 1 - (1 - t) ** 2  # ease-out: fast start, gentle arrival
        _mouse.position = (round(sx + (x - sx) * t), round(sy + (y - sy) * t))
        time.sleep(step_time)


def click_at(x, y):
    j = CLICK_POSITION_JITTER_PX
    if j:
        x += random.randint(-j, j)
        y += random.randint(-j, j)
    glide_to(int(x), int(y))
    _mouse.press(mouse.Button.left)
    _sleep_ms(MOUSE_DOWN_MS)
    _mouse.release(mouse.Button.left)


# ----------------------------------------------------------------------------- debug
def save_debug(img, found, reads):
    try:
        import cv2
    except ImportError:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    stamp = time.strftime("%H%M%S")
    cv2.imwrite(os.path.join(here, f"debug_{stamp}_raw.png"), img)
    ann = img.copy()
    for text, conf, (cx, cy) in reads:  # every raw read in grey
        cv2.putText(ann, f"{text}:{conf:.2f}", (int(cx) + 12, int(cy) + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1)
    for n, (cx, cy) in found.items():  # accepted numbers in green
        cv2.circle(ann, (int(cx), int(cy)), 10, (0, 255, 0), 2)
        cv2.putText(ann, str(n), (int(cx) - 8, int(cy) - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    path = os.path.join(here, f"debug_{stamp}_annotated.png")
    cv2.imwrite(path, ann)
    print(f"    debug images saved: {path}")


# ----------------------------------------------------------------------------- main flow
def solve(engine):
    if not _busy.acquire(blocking=False):
        return  # already running; ignore repeated F8
    try:
        _abort.clear()
        t0 = time.perf_counter()
        img, (ox, oy) = grab_screen()
        t1 = time.perf_counter()

        reads = engine.read(resize(img, UPSCALE))
        if UPSCALE != 1.0:
            reads = [(t, c, (x / UPSCALE, y / UPSCALE)) for t, c, (x, y) in reads]
        found = pick_numbers(reads)
        t2 = time.perf_counter()

        missing = [n for n in NUMBERS if n not in found]
        print(f"[F8] capture {1000*(t1-t0):.0f} ms | OCR {1000*(t2-t1):.0f} ms | "
              f"found {len(found)}/16" + (f" | missing {missing}" if missing else ""))
        if DEBUG_SAVE_IMAGES:
            save_debug(img, found, reads)

        if missing and REQUIRE_ALL_16:
            print("    not clicking (REQUIRE_ALL_16 = True)")
            return

        for n in sorted(found):
            if _abort.is_set():
                print("    aborted")
                return
            cx, cy = found[n]
            click_at(ox + cx, oy + cy)  # image pixels -> screen pixels
            _sleep_ms(max(0, CLICK_DELAY_MS + random.randint(-CLICK_DELAY_JITTER_MS, CLICK_DELAY_JITTER_MS)))
        print(f"    clicked {len(found)} numbers in {1000*(time.perf_counter()-t2):.0f} ms")
    except Exception as exc:
        print(f"    error: {exc!r}")
    finally:
        _busy.release()


def main():
    print(f"Loading OCR engine ({OCR_ENGINE})...")
    engine = make_engine()
    # Warm-up pass so the first real F8 isn't slowed by lazy initialisation.
    engine.read(np.full((64, 64, 3), 255, dtype=np.uint8))
    print("Ready. F8 = solve grid, Esc = quit.")

    def on_press(key):
        if key == HOTKEY:
            threading.Thread(target=solve, args=(engine,), daemon=True).start()
        elif key == EXIT_KEY:
            _abort.set()
            print("Esc pressed - exiting.")
            return False  # stops the listener -> main() returns

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()
    os._exit(0)  # make sure no worker thread keeps the process alive


if __name__ == "__main__":
    main()
