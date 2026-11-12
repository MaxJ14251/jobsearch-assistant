# Number Grid Solver (F8 hotkey)

Standalone helper script, not part of the `jsa` package. Press **F8** while a
numbered grid (1–16, 1–12, …) is on screen: it screenshots the primary monitor, reads the
numbers with OCR, and left-clicks them in ascending order (or, in overlay mode, just shows you the path). **End** quits.

> Automating input can break a game's terms of service, especially online.
> Your call where you use it.

## Install (Windows, Python 3.9–3.12)

```powershell
cd extras\number_grid_solver
python -m pip install --upgrade pip
python -m pip install mss numpy pynput easyocr
```

Have an NVIDIA GPU? Install CUDA PyTorch **first** for much faster OCR
(roughly 0.1–0.3 s on GPU vs 0.5–2 s on CPU for a full 1080p screen):

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
python -m pip install mss numpy pynput easyocr
```

Optional lighter engine (Tesseract):

```powershell
winget install UB-Mannheim.TesseractOCR
python -m pip install pytesseract
```
then set `OCR_ENGINE = "tesseract"` in `grid_solver.py`.

## Run

```powershell
python grid_solver.py
```

Wait for `Ready.` (the first launch downloads the EasyOCR model, ~100 MB, once).
Then switch to the game (Borderless Windowed), press F8 when the grid shows.
Each F8 prints timings, e.g. `capture 12 ms | OCR 240 ms | found 16/16`.

## Overlay mode (default): it shows the path, you click

`MODE = "overlay"` sends **no mouse input at all**. Press **Numpad 7** (or F8)
before or while the numbers show; the moment they appear it reads them and
draws over the screen: a bright green ring on the tile to click now and a
faint, smaller ring on the one after it (`OVERLAY_STYLE = "rings"`, the
default; `"path"` draws arrows 1 → 2 → … with number badges instead). The
overlay is click-through, so you click the tiles yourself; each correct click
moves the rings on. It hides after `OVERLAY_SECONDS` (15), after the
last tile, or on **Delete**. **End** quits.

Works over borderless/windowed games; an exclusive-fullscreen game draws over
it. `MODE = "click"` restores automatic clicking.

## Pausing and game performance

Press **Numpad 9** (NumLock on) to pause; press it again to resume. While
paused the trigger keys do nothing and no screenshots or reads happen, but
the text reader stays loaded, so resuming is instant (no reload wait).

Even unpaused, it does almost nothing until you press the trigger key: the
overlay window is hidden and your mouse isn't watched. The real work (about 10
screenshots and reads a second) only runs while it is armed, which ends once
the grid is read or after `ARM_TIMEOUT_S`. To keep that from costing frames:

- `LOW_PRIORITY = True` (default) runs it below the game's CPU priority.
- `OCR_THREADS = 2` (default) caps the CPU cores the reader uses. Lower it to 1
  if you still see stutter during the mini-game; raise it (or `None`) for
  faster reads on a strong CPU.
- The loaded reader holds about 1–2 GB of RAM (and some VRAM with a CUDA GPU)
  the whole time it's running. Only quitting (**End**) frees that.

## Reveal-then-hide grids (memory / chimp-test style)

F8 **arms** the solver. It watches the screen about 10 times a second until a
grid of numbered tiles appears, reads it the moment it shows (~0.3 s), waits
until the numbers are hidden (tiles go blank/white), then clicks from memory.
Press F8 any time before or while the numbers are showing — not after.

Settings: `ARM_TIMEOUT_S` (give up watching), `WAIT_FOR_HIDE` (don't click
during the reveal), `HIDE_TIMEOUT_S` (grid that never hides: click after this),
`HIDE_SETTLE_MS`. Pacing (`TARGET_TOTAL_SECONDS`) counts from when the numbers
first appeared.

## How it reads the grid

1. **Tiles (fast, ~0.3 s):** finds the square tiles by shape, boxes the digit
   in each one and recognises all digits in a single batch. It reads the real
   tiles, so uneven grids work too (the game's 11-tile version has a last row of
   2 tiles centred under 3); numbers run 1..number of tiles. No slow OCR
   detection pass, and it handles faded/hovered tiles and lone 1s and 7s.
If no numbered tile grid shows up within `ARM_TIMEOUT_S` (30 s) the solver
simply disarms - press the hotkey again. (It used to fall back to a full-screen
OCR pass, but on a game screen that took ~45 s of CPU and caused lag.)

Each F8 prints which method was used, e.g. `read (tiles) 280 ms | grid 4x3 | found 12/12`.

## Practice pages

- `test_game.html`: copies the in-game look: 3 columns × 4 rows (1–12), dark
  tiles, a 15 s "Time Left" bar, numbers shown for 2 s then every tile turns
  white, light-grey hover, blurry background. A wrong click, a click during the
  reveal, or the timer running out ends the round. **R** = new round after a 3 s
  blank (press F8 then), **F11** = fullscreen. URL options:
  `?cols=4&rows=4&time=15&tile=12&show=2&early=fail` (`show=0` never hides,
  `early=ignore` ignores clicks during the reveal).
- `test_grid.html`: simple 4×4 (1–16) grid.

## Total solve time (pacing)

`TARGET_TOTAL_SECONDS = (8.0, 10.0)` makes each solve finish at a random time
8–10 s after you press F8, screenshot and OCR included. The clicks are spread
over the time left after OCR, with uneven gaps and slower glides. If OCR alone
used up the time, it clicks at full speed instead, so it never runs much past
the target. Keep the max below the game's limit (15 s). `None` turns pacing off.

## Adjusting click speed

All settings are at the top of `grid_solver.py`:

| Setting | Meaning |
|---|---|
| `CLICK_DELAY_MS` | Pause after each click. Lower = faster. |
| `CLICK_DELAY_JITTER_MS` | Random ± on each pause (0 = perfectly fixed). |
| `MOVE_DURATION_MS` | Time the cursor glides to each number (0 = instant jump). |
| `MOUSE_DOWN_MS` | How long the button is held per click. |
| `CLICK_POSITION_JITTER_PX` | Random offset from each number's centre. |

Time per number ≈ `CLICK_DELAY_MS + MOVE_DURATION_MS + MOUSE_DOWN_MS`
(defaults: 60 + 40 + 25 ≈ 125 ms → about 2 s for all 16).

## Speed / accuracy tips

- **Set `CAPTURE_REGION`** to just the grid. This is the biggest speed-up and
  stops HUD numbers (ammo, timer) being read as grid numbers.
- Turn on `DEBUG_SAVE_IMAGES = True`, press F8 once, and open
  `debug_*_annotated.png`: green circles are accepted numbers, grey labels are
  everything OCR saw. Use the raw image to read off the grid's pixel position
  for `CAPTURE_REGION`. Turn it off again afterwards.
- Small digits missed → `UPSCALE = 1.5` or `2.0`. Wrong reads → raise `MIN_CONFIDENCE`.
- The solver locates the 4×4 grid from the confident reads (same digit size,
  evenly spaced rows/columns), ignores numbers off the grid (URL bar, hint text,
  HUD), re-reads any empty cell on its own at 2× zoom, and fills a single last
  gap by elimination. Clicks go to the cell centre. The grid size (4×4, 4 rows × 3 columns…) is
  detected on every F8; set `GRID_ROWS` and `GRID_COLS` to force one. A row or
  column OCR missed entirely is still found (gaps and edges are probed).
- `PARK_MOUSE = True` moves the cursor to the screen edge before the
  screenshot so no tile is in its faded hover colour.
  If no grid is found it prints a notice and falls back to raw OCR positions.
- Set `REQUIRE_ALL = True` if a partial solve is worse than none.

## Troubleshooting: clicks land in the wrong place

1. **Windows display scaling (125%, 150%…)** is the usual cause. The script
   declares itself DPI-aware so screenshot pixels and mouse pixels match. If
   clicks are still off by a constant factor, right-click `python.exe` →
   Properties → Compatibility → *Change high DPI settings* → tick *Override
   high DPI scaling behavior*, set to **Application**. Or set Windows scaling
   to 100% to test.
2. **Use Borderless Windowed / Windowed, not exclusive Fullscreen.** Exclusive
   fullscreen can give black screenshots and can change the desktop resolution
   so coordinates no longer match.
3. **Game resolution ≠ desktop resolution** (with the game stretched to
   borderless) is fine — OCR reads the actual screen. Just don't change it
   between setting `CAPTURE_REGION` and playing.
4. **Multiple monitors:** the game must be on the *primary* monitor
   (Windows Settings → Display → *Make this my main display*).
5. **`CAPTURE_REGION` offsets** are relative to the primary monitor's top-left.
   If you changed resolution, redo them with a debug screenshot.
6. **Clicks don't register at all** (cursor moves but nothing happens): increase
   `MOUSE_DOWN_MS` to 40–60. If the game is running as Administrator, run the
   terminal as Administrator too — Windows blocks input from lower-privilege
   programs.
7. **F8 does nothing:** some games swallow keys; run the terminal as
   Administrator, or change `HOTKEYS` (e.g. `keyboard.Key.f9`).
   **FiveM:** F8 opens FiveM's console and FiveM swallows it, so the solver
   never sees it — use **Numpad 7** (the second default hotkey, NumLock on) in game.
