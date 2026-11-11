"""
Number Grid Solver - press F8 to read a numbered grid (1..16, 1..12, ...) off the screen and
click the numbers in ascending order. Press Esc to quit.

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

HOTKEY = keyboard.Key.f8          # trigger key
EXIT_KEY = keyboard.Key.esc       # quits the program (also aborts a running click sequence)

# --- Total solve time ------------------------------------------------------
# TARGET_TOTAL_SECONDS = (min, max): the whole solve, counted from the moment
#   you press F8 (screenshot + OCR included), finishes at a random time in this
#   range. The clicks are spread over whatever time is left after OCR, with
#   uneven gaps and slower glides so it looks hand-paced. If OCR alone already
#   used the time, it falls back to the fast settings below.
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

    # Assign each confident read to its cell; best confidence wins per cell and per number.
    slot = {}  # (r, c) -> (conf, n)
    for n, conf, x, y, _ in cands:
        r = min(range(nr), key=lambda i: abs(rows[i] - y))
        c = min(range(nc), key=lambda i: abs(cols[i] - x))
        if abs(rows[r] - y) > 0.35 * dy or abs(cols[c] - x) > 0.35 * dx:
            continue
        if (r, c) not in slot or conf > slot[(r, c)][0]:
            slot[(r, c)] = (conf, n)
    by_num = {}
    for rc, (conf, n) in slot.items():
        if n not in by_num or conf > slot[by_num[n]][0]:
            by_num[n] = rc
    cells = {rc: n for n, rc in by_num.items()}

    # Re-read every empty cell on its own, magnified. Inside the lattice any
    # digit is accepted (the spot is known to be a cell); on the probe lines it
    # must be a confident read of the grid's digit size, so nearby status text
    # or background is not mistaken for a cell.
    for r in range(nr):
        for c in range(nc):
            if (r, c) in cells:
                continue
            x0, x1 = int(cols[c] - 0.4 * dx), int(cols[c] + 0.4 * dx)
            y0, y1 = int(rows[r] - 0.4 * dy), int(rows[r] + 0.4 * dy)
            crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            if crop.size == 0:
                continue
            probe = (r, c) not in core
            got = engine.read_cell(crop, detect_only=probe)
            if not got:
                continue
            text, conf, h = got  # h is already in original image pixels
            digits = re.sub(r"\D", "", text)
            if not digits or int(digits) not in NUMBERS or int(digits) in cells.values():
                continue
            if probe and (conf < 0.6 or h is None or not size_ok(h)):
                continue
            cells[(r, c)] = int(digits)

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
        if PARK_MOUSE:
            with mss.mss() as sct:
                mon = sct.monitors[1]
            _mouse.position = (mon["left"] + mon["width"] - 2, mon["top"] + mon["height"] // 2)
            _sleep_ms(PARK_WAIT_MS)
        img, (ox, oy) = grab_screen()
        t1 = time.perf_counter()

        reads = engine.read(resize(img, UPSCALE))
        if UPSCALE != 1.0:
            reads = [(t, c, (x / UPSCALE, y / UPSCALE), h / UPSCALE) for t, c, (x, y), h in reads]
        grid = pick_grid(reads, img, engine)
        if grid is None:
            print("    no number grid found - falling back to plain OCR positions")
            found, total, shape = pick_numbers(reads), None, "?"
        else:
            found, (nr, nc) = grid
            total, shape = nr * nc, f"{nr}x{nc}"
        t2 = time.perf_counter()

        missing = [n for n in range(1, total + 1) if n not in found] if total else []
        print(f"[F8] capture {1000*(t1-t0):.0f} ms | OCR {1000*(t2-t1):.0f} ms | grid {shape} | "
              f"found {len(found)}/{total or '?'}" + (f" | missing {missing}" if missing else ""))
        if DEBUG_SAVE_IMAGES:
            save_debug(img, found, reads)

        if (missing or total is None) and REQUIRE_ALL:
            print("    not clicking (REQUIRE_ALL = True)")
            return

        schedule = click_schedule(len(found), t0)
        for i, n in enumerate(sorted(found)):
            if _abort.is_set():
                print("    aborted")
                return
            cx, cy = found[n]
            if schedule:
                due, glide_ms = schedule[i]
                # Idle until it's time to start gliding, checking Esc while waiting.
                while not _abort.is_set() and time.perf_counter() < due - glide_ms / 1000.0:
                    time.sleep(min(0.02, max(0.0, due - glide_ms / 1000.0 - time.perf_counter())))
                if _abort.is_set():
                    continue
                click_at(ox + cx, oy + cy, glide_ms)  # image pixels -> screen pixels
            else:
                click_at(ox + cx, oy + cy)
                _sleep_ms(max(0, CLICK_DELAY_MS + random.randint(-CLICK_DELAY_JITTER_MS, CLICK_DELAY_JITTER_MS)))
        now = time.perf_counter()
        print(f"    clicked {len(found)} numbers in {1000*(now-t2):.0f} ms | "
              f"total since F8 {now - t0:.2f} s" + (" (paced)" if schedule else ""))
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
