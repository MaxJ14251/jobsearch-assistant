# Number Grid Solver (F8 hotkey)

Standalone helper script, not part of the `jsa` package. Press **F8** while a
1–16 number grid is on screen: it screenshots the primary monitor, reads the
numbers with OCR, and left-clicks them in order 1 → 16. **Esc** quits.

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
  gap by elimination. Clicks go to the cell centre. Change `GRID_SIZE` for 5×5 etc.
  If no grid is found it prints a notice and falls back to raw OCR positions.
- Set `REQUIRE_ALL_16 = True` if a partial solve is worse than none.

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
   Administrator, or change `HOTKEY` (e.g. `keyboard.Key.f9`).
