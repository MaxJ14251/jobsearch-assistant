"""
Number Grid Solver - press F8 to read a numbered grid (1..16, 1..12, ...) off the screen and
click the numbers in ascending order. Press End to quit. MODE = "overlay" (default) only draws the path for you to click.

Pipeline (all in memory, no files written unless DEBUG_SAVE_IMAGES is on):
    F8 -> mss screenshot of the primary monitor (or CAPTURE_REGION)
       -> OCR (EasyOCR by default, Tesseract optional) restricted to digits
       -> find the grid lattice, one number per cell (re-read / deduce gaps)
       -> sort ascending -> move + left-click each box centre with your delays

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

# Trigger keys (any of them arms the solver). F8 is FiveM's console key and
# FiveM swallows it, so in FiveM use Numpad 7. Add/replace keys as needed,
# e.g. keyboard.Key.f9 or keyboard.KeyCode.from_char('k').
# Numpad keys are matched by virtual-key code (Numpad 0-9 = 96-105, NumLock on),
# so the 7 on the top row does NOT trigger it.
HOTKEYS = (keyboard.Key.f8, keyboard.KeyCode.from_vk(103))  # F8, Numpad 7
EXIT_KEY = keyboard.Key.end       # quits the program (Esc is used too much in games)
CLEAR_KEY = keyboard.Key.delete   # hides the path overlay
# PAUSE_KEY toggles pause. While paused the trigger keys are ignored, any armed
# watch stops, and nothing is captured or read - the program just sleeps until
# you press it again. Numpad 9 = virtual-key 105 (NumLock on).
PAUSE_KEY = keyboard.KeyCode.from_vk(105)
START_PAUSED = False

# --- Keep the game smooth ------------------------------------------------------
# LOW_PRIORITY: run this program at "below normal" CPU priority so the game
#   always gets the CPU first (Windows only).
# OCR_THREADS: CPU threads the text reader may use while it is armed. Fewer =
#   lighter on the game but a little slower to read. None = use every core.
LOW_PRIORITY = True
OCR_THREADS = 2

# --- What to do once the numbers are read --------------------------------------
# "overlay": draw the 1 -> N path on top of the screen and YOU click the tiles
#            (the overlay is click-through). No mouse input is sent at all.
# "click":   click the tiles automatically (settings further down).
MODE = "overlay"
OVERLAY_SECONDS = 15              # path disappears after this (or press CLEAR_KEY)
OVERLAY_COLOR = "#00e5ff"         # line colour; badges show the numbers
OVERLAY_START_COLOR = "#39ff6a"   # ring around the tile to click now
# "rings": only a bright ring on the tile to click now and a faint, smaller ring
#          on the one after it (moves on with each correct click).
# "path":  arrows through every tile plus number badges.
OVERLAY_STYLE = "rings"
OVERLAY_NEXT_COLOR = "#1d7a36"    # faint ring on the tile after that

# --- Reveal-then-hide grids (chimp-test style) ---------------------------------
# F8 ARMS the solver: it watches the screen ~10x per second until a grid of
# numbered tiles appears, reads it in that moment, then waits until the numbers
# are hidden (tiles go blank/white) before clicking from memory.
# Press F8 any time before or while the numbers are showing.
ARM_TIMEOUT_S = 30       # stop watching if no numbered grid shows up in this time
WAIT_FOR_HIDE = True     # don't click until the numbers have disappeared
HIDE_TIMEOUT_S = 6       # numbers never disappear (normal grid)? click after this anyway
HIDE_SETTLE_MS = 120     # short pause after they disappear, before the first click

# --- Total solve time ------------------------------------------------------
# TARGET_TOTAL_SECONDS = (min, max): the whole solve, counted from the moment
#   the numbers first appear on screen (or F8, if they were already up), finishes
#   at a random time in this range. The clicks are spread over whatever time is
#   left after reading and waiting for the hide, with uneven gaps and slower
#   glides so it looks hand-paced. If no time is left, it falls back to the fast
#   settings below.
#   Keep the max under the game's limit (15 s) with some margin.
#   None = no pacing: click as fast as the settings below allow.
TARGET_TOTAL_SECONDS = (8.0, 10.0)

# --- Click speed (used when TARGET_TOTAL_SECONDS is None, and as the floor) --
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

# Move the cursor to the right edge of the screen before the screenshot, so a
# tile under the mouse isn't drawn in its faded "hover" colour (hard to read).
# PARK_WAIT_MS gives the game time to redraw the tile. Only moves, never clicks.
PARK_MOUSE = True
PARK_WAIT_MS = 60

# --- OCR -------------------------------------------------------------------
OCR_ENGINE = "easyocr"            # "easyocr" (more accurate) or "tesseract" (lighter)
USE_GPU = True                    # EasyOCR uses an NVIDIA GPU if CUDA torch is installed; falls back to CPU
MIN_CONFIDENCE = 0.30             # ignore OCR reads below this (0..1)
UPSCALE = 1.0                     # >1 (e.g. 1.5 or 2) helps tiny digits, costs speed
# Path to tesseract.exe if it is not on PATH (only used when OCR_ENGINE="tesseract")
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# --- Grid layout -------------------------------------------------------------
# The numbers sit in a rows x columns lattice (e.g. 4x4 = 1..16, or 4 rows x 3
# columns = 1..12). The solver finds that lattice from the confident reads,
# throws away numbers that are not on it (URL bars, hint text, HUD), re-reads
# empty cells one by one, and fills a single remaining gap by elimination.
# Clicks go to the cell centre.
# None = detect the size automatically on every F8 (works when it varies).
# Set both (e.g. GRID_ROWS = 4, GRID_COLS = 3) to force a fixed size.
GRID_ROWS = None
GRID_COLS = None

# If not every cell's number is found: True = click nothing (safe),
# False = click the ones that were found, in order.
REQUIRE_ALL = False

# Save the screenshot plus an annotated copy (boxes + numbers) next to this
# script on every F8. Turn on while setting up, off for speed.
DEBUG_SAVE_IMAGES = False

# ===========================================================================

MAX_NUMBER = 36  # largest number ever accepted from OCR (6x6 grid)
NUMBERS = range(1, MAX_NUMBER + 1)
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
        if OCR_THREADS:
            try:
                import torch
                torch.set_num_threads(OCR_THREADS)
            except Exception:
                pass
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
            out.append((text, float(conf), ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2),
                        max(ys) - min(ys)))
        return out

    def read_cell(self, crop, detect_only=False):
        """Read the single number in a cell crop. Returns (text, conf, height) or None.

        height is the text box height in original crop pixels (None when unknown).
        Small lone digits ("1", "7") are often only detected at 3x, so 2x is
        tried first, then 3x, then plain recognition of the whole crop.
        """
        for f in (2.0, 3.0):
            reads = self.reader.readtext(resize(crop, f), allowlist="0123456789", detail=1,
                                         paragraph=False, width_ths=0.7, mag_ratio=1.0)
            reads = [(t, float(c), (max(p[1] for p in b) - min(p[1] for p in b)) / f)
                     for b, t, c in reads if t.strip()]
            if reads:
                return max(reads, key=lambda r: r[1])
        if detect_only:
            return None
        big = resize(crop, 3.0)
        h, w = big.shape[:2]
        reads = [(t, float(c), None) for _, t, c in self.reader.recognize(
            big, horizontal_list=[[0, w, 0, h]], free_list=[],
            allowlist="0123456789", detail=1) if t.strip() and c >= 0.5]
        return max(reads, key=lambda r: r[1]) if reads else None

    def recognize_boxes(self, img, boxes):
        """Recognise text in known boxes [x0, x1, y0, y1] in one batch (no detection pass).

        Returns {box index: (text, conf)}. Much faster than readtext on a full screen.
        """
        if not boxes:
            return {}
        res = self.reader.recognize(img, horizontal_list=boxes, free_list=[],
                                    allowlist="0123456789", detail=1)
        out = {}
        for pts, text, conf in res:  # map results back to boxes by position
            cx = (pts[0][0] + pts[1][0]) / 2
            cy = (pts[0][1] + pts[2][1]) / 2
            i = min(range(len(boxes)), key=lambda k: abs((boxes[k][0] + boxes[k][1]) / 2 - cx)
                    + abs((boxes[k][2] + boxes[k][3]) / 2 - cy))
            if i not in out or float(conf) > out[i][1]:
                out[i] = (text, float(conf))
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
            out.append((text, conf / 100.0, (cx, cy), data["height"][i]))
        return out

    def read_cell(self, crop, detect_only=False):
        gray = crop.mean(axis=2).astype(np.uint8)
        data = self.pt.image_to_data(gray, config="--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789",
                                     output_type=self.pt.Output.DICT)
        reads = [(t, float(c) / 100.0, hh) for t, c, hh in zip(data["text"], data["conf"], data["height"])
                 if t.strip() and float(c) >= 0]
        return max(reads, key=lambda r: r[1]) if reads else None

    def recognize_boxes(self, img, boxes):
        out = {}
        for i, (x0, x1, y0, y1) in enumerate(boxes):
            got = self.read_cell(resize(img[max(0, y0):y1, max(0, x0):x1], 2.0))
            if got:
                out[i] = (got[0], got[1])
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
    """From raw OCR reads keep one position per number (best confidence).

    Returns {number: (cx, cy)} in the same pixel space as the reads.
    """
    best = {}
    for text, conf, centre, *_ in reads:
        digits = re.sub(r"\D", "", text)
        if not digits or conf < min_conf:
            continue
        n = int(digits)
        if n not in NUMBERS:
            continue
        if n not in best or conf > best[n][0]:
            best[n] = (conf, centre)
    return {n: c for n, (conf, c) in best.items()}


def _groups(values, tol):
    """Split values into clusters (gap > tol starts a new one). Returns list of lists."""
    groups = []
    for v in sorted(values):
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _lattice_axis(values, tol, min_spacing):
    """Fit evenly spaced lines (rows or columns) through the read positions.

    Stray reads off the lattice are ignored, and a row/column OCR missed
    entirely is filled in (one missing line between two found ones).
    Returns (line centres, spacing) or None.
    """
    groups = _groups(values, tol)
    centres = np.array([float(np.median(g)) for g in groups])
    counts = np.array([len(g) for g in groups])
    if len(centres) < 2:
        return None
    anchor = centres[counts.argmax()]  # the best-populated line is surely on the grid
    tries = {abs(a - b) / k for a in centres for b in centres for k in (1, 2)}
    best = None
    for s in tries:
        if s < min_spacing:
            continue
        k = np.round((centres - anchor) / s)
        inl = np.abs(centres - anchor - k * s) <= min(0.2 * s, tol)
        # The run of lines around the anchor; it may skip one missing line
        # but stops at a gap of 2+ (so far-away strays don't join).
        ks = set(k[inl].astype(int))
        lo = hi = 0
        while lo - 1 in ks or lo - 2 in ks:
            lo -= 1 if lo - 1 in ks else 2
        while hi + 1 in ks or hi + 2 in ks:
            hi += 1 if hi + 1 in ks else 2
        if hi == lo or hi - lo + 1 > 8:
            continue
        sel = inl & (k >= lo) & (k <= hi)
        score = (counts[sel].sum(), s)  # most reads on the run, then the widest spacing
        if best is None or score > best[0]:
            best = (score, s, k, sel, lo, hi)
    if best is None:
        return None
    _, s, k, sel, lo, hi = best
    # Least-squares refine the spacing/offset on the lines that were seen.
    if sel.sum() >= 2:
        s, off = np.polyfit(k[sel], centres[sel], 1)
    else:
        off = anchor
    return [float(off + i * s) for i in range(lo, hi + 1)], float(s)


def _extend(lines, spacing, want, limit):
    """Add candidate lines beyond both edges (rows/columns OCR missed entirely)."""
    extra = max(1, want - len(lines)) if want else 1
    before = [lines[0] - spacing * i for i in range(extra, 0, -1)]
    after = [lines[-1] + spacing * i for i in range(1, extra + 1)]
    inside = lambda v: 0.4 * spacing <= v <= limit - 0.4 * spacing
    return [v for v in before if inside(v)] + lines + [v for v in after if inside(v)]


def pick_grid(reads, img, engine):
    """Locate the number lattice; return ({number: (x, y)} at cell centres, (rows, cols)).

    Returns None if no lattice can be found (caller falls back to pick_numbers).
    """
    cands = []
    for text, conf, (cx, cy), h in reads:
        digits = re.sub(r"\D", "", text)
        if digits and conf >= MIN_CONFIDENCE and int(digits) in NUMBERS:
            cands.append((int(digits), conf, cx, cy, h))
    if len(cands) < 3:
        return None

    # Grid digits share one font size; small text (URLs, hints) and huge HUD
    # numbers are dropped.
    strong = [c[4] for c in cands if c[1] >= 0.8] or [c[4] for c in cands]
    med_h = float(np.median(strong))

    def size_ok(h):
        return 0.65 * med_h <= h <= 1.5 * med_h

    cands = [c for c in cands if size_ok(c[4])]

    # A cell is always bigger than its digit, so lines are >= 1.5 digit heights apart.
    fit_r = _lattice_axis([c[3] for c in cands], tol=0.8 * med_h, min_spacing=1.5 * med_h)
    fit_c = _lattice_axis([c[2] for c in cands], tol=0.8 * med_h, min_spacing=1.5 * med_h)
    if fit_r is None or fit_c is None:
        return None
    (rows, dy), (cols, dx) = fit_r, fit_c

    # An outer line backed by a single read is likely a stray (URL bar, HUD)
    # that happens to line up; drop it. If it was a real row/column, the edge
    # probe below finds it again from a properly sized digit.
    def trim(lines, step, pos):
        n_on = lambda v: sum(abs(p - v) <= 0.2 * step for p in pos)
        if len(lines) > 2 and n_on(lines[0]) < 2:
            lines = lines[1:]
        if len(lines) > 2 and n_on(lines[-1]) < 2:
            lines = lines[:-1]
        return lines

    rows = trim(rows, dy, [c[3] for c in cands])
    cols = trim(cols, dx, [c[2] for c in cands])

    # Probe one line beyond each edge (or up to GRID_ROWS/GRID_COLS) in case a
    # whole outer row/column went unread; probes are kept only if a digit is found.
    h_img, w_img = img.shape[:2]
    all_rows = _extend(rows, dy, GRID_ROWS, h_img)
    all_cols = _extend(cols, dx, GRID_COLS, w_img)
    r_off, c_off = all_rows.index(rows[0]), all_cols.index(cols[0])
    core = {(r + r_off, c + c_off) for r in range(len(rows)) for c in range(len(cols))}
    rows, cols = all_rows, all_cols
    nr, nc = len(rows), len(cols)

    # Assign each confident read on the core lattice to its cell; best
    # confidence wins per cell and per number. Reads on probe lines wait.
    slot, probe_reads = {}, {}  # (r, c) -> (conf, n)
    for n, conf, x, y, _ in cands:
        r = min(range(nr), key=lambda i: abs(rows[i] - y))
        c = min(range(nc), key=lambda i: abs(cols[i] - x))
        if abs(rows[r] - y) > 0.35 * dy or abs(cols[c] - x) > 0.35 * dx:
            continue
        target = slot if (r, c) in core else probe_reads
        if (r, c) not in target or conf > target[(r, c)][0]:
            target[(r, c)] = (conf, n)
    by_num = {}
    for rc, (conf, n) in slot.items():
        if n not in by_num or conf > slot[by_num[n]][0]:
            by_num[n] = rc
    cells = {rc: n for n, rc in by_num.items()}

    def reread(r, c, probe):
        x0, x1 = int(cols[c] - 0.4 * dx), int(cols[c] + 0.4 * dx)
        y0, y1 = int(rows[r] - 0.4 * dy), int(rows[r] + 0.4 * dy)
        crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if crop.size == 0:
            return None
        got = engine.read_cell(crop, detect_only=probe)
        if not got:
            return None
        text, conf, h = got  # h is already in original image pixels
        digits = re.sub(r"\D", "", text)
        if not digits or int(digits) not in NUMBERS:
            return None
        if probe and (conf < 0.6 or h is None or not size_ok(h)):
            return None
        return int(digits)

    # Re-read every empty core cell on its own, magnified; any digit is
    # accepted there (the spot is known to be a cell).
    for rc in sorted(core):
        if rc not in cells:
            n = reread(*rc, probe=False)
            if n is not None and n not in cells.values():
                cells[rc] = n

    # Only if numbers are still unaccounted for: probe the lines beyond the
    # edges. A probe line counts only with 2+ numbers on it, so a status line
    # or label next to the grid ("Next: 1", "Time Left") is not taken for a row.
    core_total = len({r for r, _ in core}) * len({c for _, c in core})
    if any(n not in cells.values() for n in range(1, core_total + 1)) or not core_total:
        hits = {rc: n for rc, (conf, n) in probe_reads.items()}
        for r in range(nr):
            for c in range(nc):
                if (r, c) not in core and (r, c) not in hits:
                    n = reread(r, c, probe=True)
                    if n is not None:
                        hits[(r, c)] = n
        lines_r = {r for r, _ in hits if sum(1 for rr, _ in hits if rr == r) >= 2 and r not in {q for q, _ in core}}
        lines_c = {c for _, c in hits if sum(1 for _, cc in hits if cc == c) >= 2 and c not in {q for _, q in core}}
        for (r, c), n in sorted(hits.items()):
            if (r in lines_r or c in lines_c) and n not in cells.values():
                cells[(r, c)] = n

    # Drop probe rows/columns that turned out empty.
    used_r = [r for r, _ in cells] + [r for r, _ in core]
    used_c = [c for _, c in cells] + [c for _, c in core]
    r_lo, r_hi, c_lo, c_hi = min(used_r), max(used_r), min(used_c), max(used_c)
    nr, nc = r_hi - r_lo + 1, c_hi - c_lo + 1
    if (GRID_ROWS and nr > GRID_ROWS) or (GRID_COLS and nc > GRID_COLS):
        return None
    cells = {(r - r_lo, c - c_lo): n for (r, c), n in cells.items()}
    rows, cols = rows[r_lo:r_hi + 1], cols[c_lo:c_hi + 1]

    # Numbers run 1..rows*cols; anything larger was a misread.
    total = nr * nc
    cells = {rc: n for rc, n in cells.items() if n <= total}

    # One cell left and one number left -> it must be that number.
    empty = [(r, c) for r in range(nr) for c in range(nc) if (r, c) not in cells]
    missing = [n for n in range(1, total + 1) if n not in cells.values()]
    if len(empty) == 1 and len(missing) == 1:
        cells[empty[0]] = missing[0]

    return {n: (cols[c], rows[r]) for (r, c), n in cells.items()}, (nr, nc)


# ----------------------------------------------------------------------------- tile finder
# Fast path: find the square tiles with plain image processing (a few ms), box
# the digit inside each tile, and run only the OCR recogniser on those boxes in
# one batch. Skips EasyOCR's slow, small-digit-blind detection pass entirely.

def find_tiles(img):
    """Return [(cx, cy, w, h)] for square-ish closed shapes (tile candidates)."""
    import cv2
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 20, 60)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    max_side = 0.5 * min(H, W)
    out = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        if w < 30 or h < 30 or w > max_side or h > max_side or not 0.8 <= w / h <= 1.25:
            continue
        if cv2.contourArea(cv2.convexHull(c)) < 0.85 * w * h:
            continue
        out.append((x + w / 2, y + h / 2, w, h))
    out.sort(key=lambda t: -t[2])  # inner and outer edge of one tile -> keep one
    merged = []
    for t in out:
        if all(abs(t[0] - m[0]) > 0.3 * m[2] or abs(t[1] - m[1]) > 0.3 * m[3] for m in merged):
            merged.append(t)
    return merged


def _tile_lattice(tiles):
    """Biggest group of equal-size tiles in evenly spaced rows -> (rows, cols, size, cells) or None.

    cells are the real tile centres, so rows that are shorter or shifted (the
    game's last row of 2 tiles centred under 3) are handled. A tile the shape
    finder missed in a row that sits on the columns is filled in from them.
    """
    best = None
    for ref in tiles:
        grp = [t for t in tiles if abs(t[2] - ref[2]) <= 0.12 * ref[2] and abs(t[3] - ref[3]) <= 0.12 * ref[3]]
        if len(grp) < 4:
            continue
        size = float(np.median([t[2] for t in grp]))
        fr = _lattice_axis([t[1] for t in grp], tol=0.25 * size, min_spacing=0.9 * size)
        fc = _lattice_axis([t[0] for t in grp], tol=0.25 * size, min_spacing=0.9 * size)
        if not fr or not fc:
            continue
        rows, cols = fr[0], fc[0]
        x_lo, x_hi = cols[0] - 0.6 * size, cols[-1] + 0.6 * size
        cells = []
        for y in rows:
            row = sorted(t[0] for t in grp if abs(t[1] - y) <= 0.25 * size and x_lo <= t[0] <= x_hi)
            if not row:
                continue
            on_cols = all(min(abs(x - c) for c in cols) <= 0.25 * size for x in row)
            if on_cols and len(row) < len(cols) and len(row) >= len(cols) - 1:
                row = list(cols)  # one tile missed in an aligned row: use the column spots
            cells += [(x, y) for x in row]
        if len(cells) < 0.6 * len(rows) * len(cols):
            continue  # a grid of tiles should be mostly filled
        score = (len(cells), size)
        if best is None or score > best[0]:
            best = (score, rows, cols, size, cells)
    return best[1:] if best else None


def _digit_box(img, cx, cy, size):
    """Tight box [x0, x1, y0, y1] around the digit inside a tile, or None if blank."""
    import cv2
    half = size * 0.40
    x0, y0 = max(0, int(cx - half)), max(0, int(cy - half))
    x1, y1 = int(cx + half), int(cy + half)
    crop = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)
    if crop.size == 0:
        return None
    border = np.concatenate([crop[:3].ravel(), crop[-3:].ravel(), crop[:, :3].ravel(), crop[:, -3:].ravel()])
    mask = (np.abs(crop - np.median(border)) > 45).astype(np.uint8)  # pixels unlike the tile colour
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep = [i for i in range(1, n) if stats[i, cv2.CC_STAT_HEIGHT] >= 0.08 * size]
    if not keep:
        return None
    bx0 = min(stats[i, 0] for i in keep)
    bx1 = max(stats[i, 0] + stats[i, 2] for i in keep)
    by0 = min(stats[i, 1] for i in keep)
    by1 = max(stats[i, 1] + stats[i, 3] for i in keep)
    pad = int(0.25 * (by1 - by0)) + 2
    return [x0 + bx0 - pad, x0 + bx1 + pad, y0 + by0 - pad, y0 + by1 + pad]


def tile_lattice(img):
    """(rows, cols, size, cells) of the tile grid on screen, or None (honours GRID_ROWS/GRID_COLS)."""
    lat = _tile_lattice(find_tiles(img))
    if lat is None:
        return None
    rows, cols = lat[0], lat[1]
    if (GRID_ROWS and len(rows) != GRID_ROWS) or (GRID_COLS and len(cols) != GRID_COLS):
        return None
    return lat


def digits_showing(img, lat):
    """How many tiles currently show a digit (cheap, no OCR)."""
    size, cells = lat[2], lat[3]
    return sum(1 for x, y in cells if _digit_box(img, x, y, size))


def pick_tiles(img, engine, lat=None):
    """Fast path. Returns ({number: (x, y)}, (rows, cols), lattice) or None if no tile grid.

    Numbers run 1..number of tiles. Only one batched recognise call - no slow
    per-tile re-reads (on CPU next to a game those took ~10 s each); the
    caller simply reads the next frame again if something was missed.
    """
    lat = lat or tile_lattice(img)
    if lat is None:
        return None
    rows, cols, size, cells = lat
    total = len(cells)
    boxes, where = [], []
    for i, (x, y) in enumerate(cells):
        b = _digit_box(img, x, y, size)
        if b:
            boxes.append(b)
            where.append(i)
    read = engine.recognize_boxes(img, boxes) if boxes else {}
    best = {}  # number -> (conf, tile index)
    for i, (text, conf) in read.items():
        digits = re.sub(r"\D", "", text)
        if digits and 1 <= int(digits) <= total:
            n = int(digits)
            if n not in best or conf > best[n][0]:
                best[n] = (conf, where[i])
    got = {i: n for n, (conf, i) in best.items()}
    empty = [i for i in range(total) if i not in got]
    missing = [n for n in range(1, total + 1) if n not in got.values()]
    if len(empty) == 1 and len(missing) == 1:
        got[empty[0]] = missing[0]
    return {n: cells[i] for i, n in got.items()}, (len(rows), len(cols)), lat


# ----------------------------------------------------------------------------- arm / hide
def watch_for_numbers(engine, deadline):
    """Poll the screen until a tile grid with numbers shows; read it at once.

    Returns (img, offset, found, (rows, cols), lattice, t_seen) or None.
    Keeps watching while a read is incomplete (e.g. tiles still animating in).
    """
    best, t_seen, said = None, None, False
    while not _abort.is_set() and time.perf_counter() < deadline:
        t = time.perf_counter()
        img, off = grab_screen()
        lat = tile_lattice(img)
        if lat:
            cells = len(lat[3])
            if digits_showing(img, lat) >= 0.75 * cells:
                t_seen = t_seen or t
                found, shape, lat = pick_tiles(img, engine, lat)
                if best is None or len(found) > len(best[2]):
                    best = (img, off, found, shape, lat, t_seen)
                if len(found) == cells:
                    return best
            elif not said:
                print("    tiles found but numbers not showing - waiting for them...")
                said = True
        time.sleep(max(0.0, 0.1 - (time.perf_counter() - t)))  # ~10 checks per second
    return best


def wait_until_hidden(lat, deadline):
    """Wait until the tiles stop showing numbers. True if they hid, False on timeout."""
    cells = len(lat[3])
    while not _abort.is_set() and time.perf_counter() < deadline:
        img, _ = grab_screen()
        if digits_showing(img, lat) <= 0.1 * cells:
            return True
        time.sleep(0.05)
    return False


# ----------------------------------------------------------------------------- mouse
def _sleep_ms(ms):
    if ms > 0:
        time.sleep(ms / 1000.0)


def glide_to(x, y, duration_ms=None):
    """Move the cursor to (x, y) over duration_ms (default MOVE_DURATION_MS) with ease-out."""
    sx, sy = _mouse.position
    duration_ms = MOVE_DURATION_MS if duration_ms is None else duration_ms
    steps = max(MOVE_STEPS, int(duration_ms / 15))  # ~60 updates/s on slow glides
    if duration_ms <= 0 or steps <= 1:
        _mouse.position = (x, y)
        return
    step_time = duration_ms / 1000.0 / steps
    for i in range(1, steps + 1):
        t = i / steps
        t = 1 - (1 - t) ** 2  # ease-out: fast start, gentle arrival
        _mouse.position = (round(sx + (x - sx) * t), round(sy + (y - sy) * t))
        time.sleep(step_time)


def click_at(x, y, glide_ms=None):
    j = CLICK_POSITION_JITTER_PX
    if j:
        x += random.randint(-j, j)
        y += random.randint(-j, j)
    glide_to(int(x), int(y), glide_ms)
    _mouse.press(mouse.Button.left)
    _sleep_ms(MOUSE_DOWN_MS)
    _mouse.release(mouse.Button.left)


def click_schedule(n, t0):
    """Plan when each of n clicks lands so the solve ends TARGET_TOTAL_SECONDS after t0.

    Returns [(click_time, glide_ms), ...] in perf_counter seconds, or None for
    unpaced (fast) clicking: pacing off, nothing to click, or not enough time left.
    """
    if not TARGET_TOTAL_SECONDS or n == 0:
        return None
    lo, hi = TARGET_TOTAL_SECONDS
    end = t0 + random.uniform(lo, hi)
    start = time.perf_counter()
    fast_gap = (CLICK_DELAY_MS + MOVE_DURATION_MS + MOUSE_DOWN_MS) / 1000.0
    if end - start < n * fast_gap:
        return None  # OCR ate the budget: click as fast as allowed
    # Uneven gaps (some quick, some hesitant) that add up to the time left.
    weights = [random.uniform(0.6, 1.4) for _ in range(n)]
    scale = (end - start) / sum(weights)
    plan, t = [], start
    for w in weights:
        gap = w * scale
        t += gap
        glide_ms = min(450.0, max(MOVE_DURATION_MS, 0.45 * gap * 1000.0))
        plan.append((t - MOUSE_DOWN_MS / 1000.0, glide_ms))
    return plan


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
    for text, conf, (cx, cy), *_ in reads:  # every raw read in grey
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
        if PARK_MOUSE and MODE == "click":
            with mss.mss() as sct:
                mon = sct.monitors[1]
            _mouse.position = (mon["left"] + mon["width"] - 2, mon["top"] + mon["height"] // 2)
            _sleep_ms(PARK_WAIT_MS)
        print("[armed] watching for the numbered grid (End = quit)")
        t1 = time.perf_counter()

        # Fast path: watch for numbered tiles and read them the moment they show.
        reads, method, lat = [], "tiles", None
        seen = watch_for_numbers(engine, t0 + ARM_TIMEOUT_S)
        if seen:
            img, (ox, oy), found, (nr, nc), lat, t_seen = seen
            grid = (found, (nr, nc))
        else:
            # No numbered grid showed up: give up. (A full-screen OCR fallback
            # used to run here; on a game screen it took ~45 s of CPU and
            # never found the grid, so it only caused lag.)
            if not _abort.is_set():
                print(f"    no numbered grid seen in {ARM_TIMEOUT_S} s - disarmed (press Numpad 7 to try again)")
            return
        if grid is None:
            print("    no number grid found - falling back to plain OCR positions")
            found, total, shape = pick_numbers(reads), None, "?"
        else:
            found, (nr, nc) = grid
            total = len(lat[3]) if lat else nr * nc
            shape = f"{nr}x{nc}" + (f" ({total} tiles)" if total != nr * nc else "")
        t2 = time.perf_counter()

        missing = [n for n in range(1, total + 1) if n not in found] if total else []
        print(f"    numbers seen after {t_seen - t0:.2f} s | read ({method}) {1000*(t2-t_seen):.0f} ms | "
              f"grid {shape} | found {len(found)}/{total or '?'}" + (f" | missing {missing}" if missing else ""))
        if DEBUG_SAVE_IMAGES:
            save_debug(img, found, reads)

        if (missing or total is None) and REQUIRE_ALL:
            print("    not clicking (REQUIRE_ALL = True)")
            return
        if not found:
            print("    nothing to click - press F8 before or while the numbers are showing")
            return

        if MODE == "overlay":
            size = lat[2] if lat else _min_gap(list(found.values()))
            pts = [(ox + found[n][0], oy + found[n][1], n) for n in sorted(found)]
            _overlay_q.put(("show", pts, size))
            print(f"    path shown for {OVERLAY_SECONDS} s - click the tiles yourself (Delete = hide)")
            return

        if WAIT_FOR_HIDE and lat:
            hid = wait_until_hidden(lat, time.perf_counter() + HIDE_TIMEOUT_S)
            if _abort.is_set():
                return
            print(f"    numbers hidden after {time.perf_counter() - t_seen:.2f} s" if hid
                  else f"    numbers still showing after {HIDE_TIMEOUT_S} s - clicking anyway")
            if hid:
                _sleep_ms(HIDE_SETTLE_MS)

        schedule = click_schedule(len(found), t_seen)
        for i, n in enumerate(sorted(found)):
            if _abort.is_set():
                print("    aborted")
                return
            cx, cy = found[n]
            if schedule:
                due, glide_ms = schedule[i]
                # Idle until it is time to start gliding, checking End while waiting.
                while not _abort.is_set() and time.perf_counter() < due - glide_ms / 1000.0:
                    time.sleep(min(0.02, max(0.0, due - glide_ms / 1000.0 - time.perf_counter())))
                if _abort.is_set():
                    continue
                click_at(ox + cx, oy + cy, glide_ms)  # image pixels -> screen pixels
            else:
                click_at(ox + cx, oy + cy)
                _sleep_ms(max(0, CLICK_DELAY_MS + random.randint(-CLICK_DELAY_JITTER_MS, CLICK_DELAY_JITTER_MS)))
        now = time.perf_counter()
        print(f"    clicked {len(found)} numbers | done {now - t_seen:.2f} s after the numbers appeared"
              + (" (paced)" if schedule else ""))
    except Exception as exc:
        print(f"    error: {exc!r}")
    finally:
        _busy.release()


def _min_gap(points):
    """Smallest distance between two points (tile spacing estimate)."""
    gaps = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for i, a in enumerate(points) for b in points[i + 1:]]
    return min(gaps) if gaps else 100.0


# ----------------------------------------------------------------------------- overlay
import queue
_overlay_q = queue.Queue()


class PathOverlay:
    """Transparent, always-on-top, click-through window that draws the 1 -> N path.

    Shows over borderless/windowed games; exclusive-fullscreen games draw over it.
    Runs in the main thread (Tk requirement); other threads talk to it via _overlay_q.
    """
    KEY = "#ff00fe"  # this colour is made fully transparent

    def __init__(self):
        import tkinter as tk
        with mss.mss() as sct:
            mon = sct.monitors[1]
        self.root = root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        root.configure(bg=self.KEY)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", self.KEY)
        self.canvas = tk.Canvas(root, bg=self.KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.origin = (mon["left"], mon["top"])
        self.hide_job = None
        self.styled = False
        self.pts, self.size, self.idx = [], 100.0, 0
        self.mouse_listener = None
        root.after(50, self._poll)

    def _watch_clicks(self, on):
        """Listen to your clicks only while a path is showing (no idle mouse hook)."""
        if on and self.mouse_listener is None:
            def on_click(x, y, button, pressed):
                if pressed and button == mouse.Button.left:
                    _overlay_q.put(("click", x, y))
            self.mouse_listener = mouse.Listener(on_click=on_click)
            self.mouse_listener.start()
        elif not on and self.mouse_listener is not None:
            self.mouse_listener.stop()
            self.mouse_listener = None

    def _make_click_through(self):
        if sys.platform != "win32" or self.styled:
            return
        u32 = ctypes.windll.user32
        hwnd = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        GWL_EXSTYLE = -20
        style = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # layered + transparent (mouse passes through) + tool window (no taskbar) + no-activate
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | 0x80000 | 0x20 | 0x80 | 0x08000000)
        self.styled = True

    def show(self, pts, size):
        ox, oy = self.origin
        self.pts = [(x - ox, y - oy, n) for x, y, n in pts]
        self.size, self.idx = size, 0
        self._draw()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.update_idletasks()
        self._make_click_through()
        self._watch_clicks(True)
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
        self.hide_job = self.root.after(int(OVERLAY_SECONDS * 1000), self.clear)

    def on_click(self, x, y):
        """Your click on the next tile removes that step from the path."""
        if not self.pts or self.idx >= len(self.pts):
            return
        nx, ny, _ = self.pts[self.idx]
        ox, oy = self.origin
        if abs(x - ox - nx) <= 0.5 * self.size and abs(y - oy - ny) <= 0.5 * self.size:
            self.idx += 1
            if self.idx >= len(self.pts):
                self.clear()
            else:
                self._draw()

    def _draw(self):
        c, size = self.canvas, self.size
        c.delete("all")
        pts = self.pts[self.idx:]
        w = max(3, int(size * 0.035))
        if OVERLAY_STYLE == "rings":
            # Faint ring first, so the bright one stays on top if they touch.
            if len(pts) > 1:
                x, y, _ = pts[1]
                r = size * 0.16
                c.create_oval(x - r, y - r, x + r, y + r, outline="#000000", width=w + 3)
                c.create_oval(x - r, y - r, x + r, y + r, outline=OVERLAY_NEXT_COLOR, width=w)
            x, y, _ = pts[0]
            r = size * 0.24
            c.create_oval(x - r, y - r, x + r, y + r, outline="#000000", width=w + 5)
            c.create_oval(x - r, y - r, x + r, y + r, outline=OVERLAY_START_COLOR, width=w + 2)
            return
        arrow = (w * 4, w * 5, w * 2)
        for (x1, y1, _), (x2, y2, _) in zip(pts, pts[1:]):
            c.create_line(x1, y1, x2, y2, fill="#000000", width=w + 4, capstyle="round")
        for (x1, y1, _), (x2, y2, _) in zip(pts, pts[1:]):
            c.create_line(x1, y1, x2, y2, fill=OVERLAY_COLOR, width=w, arrow="last",
                          arrowshape=arrow, capstyle="round")
        x, y, _ = pts[0]
        r = size * 0.22
        c.create_oval(x - r, y - r, x + r, y + r, outline=OVERLAY_START_COLOR, width=w + 1)
        br = max(10, size * 0.12)  # number badge in the tile's top-left corner
        for x, y, n in pts:
            bx, by = x - size * 0.30, y - size * 0.30
            c.create_oval(bx - br, by - br, bx + br, by + br, fill="#000000", outline=OVERLAY_COLOR, width=2)
            c.create_text(bx, by, text=str(n), fill="#ffffff",
                          font=("Segoe UI", -int(br * 1.2), "bold"))

    def clear(self):
        self.pts = []
        self.canvas.delete("all")
        self.root.withdraw()
        self._watch_clicks(False)
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None

    def _poll(self):
        try:
            while True:
                msg = _overlay_q.get_nowait()
                if msg[0] == "show":
                    self.show(msg[1], msg[2])
                elif msg[0] == "clear":
                    self.clear()
                elif msg[0] == "click":
                    self.on_click(msg[1], msg[2])
                elif msg[0] == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(30, self._poll)


def _is_hotkey(key):
    """Match by virtual-key code where possible (tells Numpad 7 apart from the top-row 7)."""
    vk = getattr(key, "vk", None)
    for hk in HOTKEYS:
        hk_vk = getattr(hk, "vk", None)
        if key == hk if hk_vk is None or isinstance(hk, keyboard.Key) else vk == hk_vk:
            return True
    return False


_paused = threading.Event()


def _set_low_priority():
    if LOW_PRIORITY and sys.platform == "win32":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS
        except Exception:
            pass


def _is_key(key, target):
    vk = getattr(target, "vk", None)
    if vk is not None and not isinstance(target, keyboard.Key):
        return getattr(key, "vk", None) == vk
    return key == target


def main():
    _set_low_priority()
    if START_PAUSED:
        _paused.set()
    print(f"Loading OCR engine ({OCR_ENGINE})...")
    engine = make_engine()
    # Warm-up pass so the first real F8 isn't slowed by lazy initialisation.
    engine.read(np.full((64, 64, 3), 255, dtype=np.uint8))
    overlay = PathOverlay() if MODE == "overlay" else None
    what = "draw the path" if overlay else "solve and click"
    print(f"Ready ({MODE} mode). F8 or Numpad 7 = arm + {what} "
          f"(press before or while the numbers show), Delete = hide path, End = quit.")
    print("In FiveM use Numpad 7 (NumLock on) - FiveM keeps F8 for its own console.")

    print("Numpad 9 = pause / resume." + ("  [PAUSED]" if _paused.is_set() else ""))

    def on_press(key):
        if _is_key(key, PAUSE_KEY):
            if _paused.is_set():
                _paused.clear()
                print("[resumed] Numpad 7 / F8 works again.")
            else:
                _paused.set()
                _abort.set()  # stop an armed watch right away
                _overlay_q.put(("clear",))
                print("[paused] trigger keys ignored - Numpad 9 to resume.")
        elif _is_hotkey(key):
            if _paused.is_set():
                print("[paused] press Numpad 9 to resume first.")
                return
            _overlay_q.put(("clear",))
            threading.Thread(target=solve, args=(engine,), daemon=True).start()
        elif key == CLEAR_KEY:
            _overlay_q.put(("clear",))
        elif key == EXIT_KEY:
            _abort.set()
            _overlay_q.put(("quit",))
            print("End pressed - exiting.")
            return False  # stops the listener

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    if overlay:
        overlay.root.mainloop()  # returns after "quit"
    else:
        listener.join()
    os._exit(0)  # make sure no worker thread keeps the process alive


if __name__ == "__main__":
    main()
