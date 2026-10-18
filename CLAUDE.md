# BotC 3D Token Generator — project context

> **How to use this file:** Save it as `CLAUDE.md` in the root of your local
> clone and Claude Code will load it automatically every session. You can also
> paste it as your first message to kick off a session. The "Start here" block
> just below is what a fresh session should do first.

---

## Start here (first actions for a new session)

1. Confirm you're in the repo root (you should see `solid_maker.py`, `botc_gui.py`, `tests/`).
2. Run the test suite and report the count: `uv run pytest -q`.
   - All tests should pass. If any **error on import** (e.g. `trimesh`, `networkx`, `lxml`, `matplotlib` not found), those are test-only deps — add them with `uv add --dev trimesh networkx lxml matplotlib` and re-run.
3. Confirm the tools run: `uv run python solid_maker.py --help` and `uv run python botc_gui.py` (the window should open).
4. Then wait for the task. Don't start changing things until asked.

Everything below is reference.

---

## What this is

A fork that turns *Blood on the Clocktower* role data into 3D-printable tokens.
It scrapes/loads role art and reminder text, traces the art to vector geometry,
and emits OpenSCAD → STL → two-colour 3MF files ready to drag into BambuStudio.
Around that core it has a checklist, build-plate layout, a proof/contact
sheet, an art pre-flight check, a Tkinter GUI, and a
standalone-exe build.

- **Fork:** https://github.com/Jnieco/blood-on-the-clock-tower-scad-generator
- **Language:** Python, run through **uv**. Geometry via **SolidPython → OpenSCAD**.

---

## My environment & hardware (read before suggesting anything physical)

- **OS:** Windows 11, PowerShell, VS Code.
- **Always run through `uv`** (e.g. `uv run python solid_maker.py`, `uv run pytest`). Don't call `python` directly.
- **OpenSCAD** is installed separately and is **not** on PATH by default on Windows — the code probes standard install locations, or set the `OPENSCAD_PATH` env var to override. A recent OpenSCAD (2023+) has the **Manifold backend**, which the code auto-detects and uses (much faster for boolean-heavy models); it falls back safely if the flag is rejected.
- **Printer:** Bambu Lab X1/P1, 256×256 bed.
- **Nozzle:** **0.2 mm for the tokens** (fine art/text).
- **Foam:** I add **~1 mm of foam to the back of each token**.

---

## Conventions & workflow (important)

Every change follows this loop:

1. Implement.
2. `uv run ruff format` then `uv run ruff check .` — **must pass**.
3. `uv run pytest -q` — **must pass**. Add/adjust tests for new behaviour.
4. Keep comments explaining *why*, not *what* (match the existing style — the code is heavily commented with rationale).

Other norms we've kept:

- **Prefer editing the real `.py` files**, not shell hacks.
- New dependencies are rare. If you add one, say so explicitly — a normal update should **not** need `uv sync`.
- **Validate geometry numerically**, not just by eye: watertightness, bounds, no non-manifold edges (edges shared by >2 faces), no open edges. We use `trimesh` for this. (Rendering screenshots has historically been unreliable; numbers are the source of truth.)
- Friendly failure over crashes: tools print clear messages and exit non-zero rather than dumping tracebacks. The GUI degrades gracefully if a companion module is out of date (see `missing_features` in `botc_gui.py`).
- The GUI is a **thin launcher** — it builds the same commands you'd run by hand and shells out. Never reimplement tool logic inside the GUI.

**Note on tests + tkinter:** on Windows, `tkinter` ships with Python, so the GUI tests run in the same env as everything else. (In Anthropic's Linux sandbox they had to be split across two Python builds — ignore any history of that; it's not relevant on your machine.)

---

## The files

Generators / logic:
- **`solid_maker.py`** — the heart. Art pipeline (PNG→greyscale→SVG via pure-Python `potracer`, which **imports as `import potrace`**, then parsed into an explicit OpenSCAD `polygon()` — so the `.scad` text is a complete geometry fingerprint, which the build cache keys on). Character tokens (45 mm, curved role name at the bottom rim, art fitted per-token), reminder tokens (25 mm, art on top + straight wrapped text), two-colour flush 3MF export, the build cache, `settings.json` machinery, `extra_reminders.json`, and the four "Choose a…" draft tokens. CLI has many flags (`--script`, `--script-file`, `--only`, `--custom`, `--choose-tokens`/`--choose-only`, `--art-up`, `--no-reminders`, `--no-3mf`, `--skip-stl`, `--force`, `--write-settings`). Emits `[progress] N/M name` lines the GUI parses for its bar.
- **`make_print_checklist.py`** — Excel checklist (openpyxl). `build_inventory`, `load_inventory`, `write_checklist`, progress/quantity read-back. `Qty needed` is user-editable and survives rebuilds (`--merge`); a plain rebuild refuses rather than discarding edits or progress. Scoped runs write `checklist_<script>.xlsx`, not the master.
- **`import_script.py`** — imports official script-creator JSON (both formats) → `scripts.json`, inline homebrew → `custom_roles.json`.
- **`make_plates.py`** — build-plate layout, one `.3mf` per plate **grouped by colour** (dark disc + one colour each). Reads the checklist and **skips already-printed tokens by default** (`--all`, `--reprint ROLE`, `--checklist FILE` override). Also `--choose-tokens`/`--choose-only`.
- **`make_contact_sheet.py`** — one PNG proof of every token, drawn from the traced SVGs (no OpenSCAD needed).
- **`art_preflight.py`** — batch art check; lists problem roles up front, exit 1 if any.
- **`get_all_roles.py`** — scrapes role art + colours + reminder text → `roles.json`.

App / packaging:
- **`botc_gui.py`** — Tkinter GUI. Tabs: **Tokens / Checklist / Settings**. Output pane + progress bar live at window level so any tab's run is visible. Frozen-aware (`--run-tool` dispatch for the packaged exe). Checklist tab has an editable table, reprint, and an **Add reminder…** button.
- **`build_exe.py`** — PyInstaller `--onefile --windowed` build. OpenSCAD is **not** bundled; must build **on Windows** for a Windows exe.
- **`run_gui.bat`** — double-click launcher (checks for uv).

Config (gitignored; `*.example.json` are tracked templates):
- `roles.json`, `custom_roles.json`, `scripts.json`, `settings.json`, `extra_reminders.json`, `print_checklist.xlsx` / `checklist_*.xlsx`, `.build_cache.json`.

Tests: `tests/` — ~232 tests across all modules. Keep them green.

---

## Key technical facts (so you don't relearn them the hard way)

- **Settings load at import**, *before* the function definitions that take tunables as default arguments (Python evaluates defaults at def-time). Don't move that call.
- **3MF export:** two independently watertight bodies (disc + inlay). The inlay drops into a flush 0.3 mm pocket (`manifold3d` boolean), so the printed face is smooth — tokens are drawn blind from a bag, so **no raised relief**. Art prints **face-down by default** (`--art-up` opts out) for the cleanest colour boundary. Face colouring is per-triangle via `<m:colorgroup>` (BambuStudio reads this).
- **Colours:** base/felt `#2E2E2EFF`; team colours — **townsfolk & outsider = blue, minion & demon = red** (good/evil). Custom roles may use `#RRGGBB`.
- **`normalise_role_name`** squashes to lowercase alphanumeric for loose matching ("fortuneteller" ↔ "Fortune Teller").
- **Glyph slivers:** short/large engraved words can leave zero-volume slivers or a single non-manifold edge where a glyph meets a rim after a boolean. The fix pattern we use: a hair of `offset()` to merge touching contours, and clip text to a circle *inside* the rim. (Seen on the "Choose a… DEMON/MINION" tokens.)

---

## What's already built (so you don't rebuild it)

3MF flush two-colour export · custom/homebrew tokens · scripts/editions · Excel
checklist (with quantity-edit persistence) · official script import · Tkinter
GUI (3 tabs) · build cache · `settings.json` + GUI Settings tab · launchers +
standalone exe · build-plate layout (colour-grouped, skip-printed/reprint) · GUI
checklist tab · real per-token progress bar · art pre-flight report · contact
sheet · `extra_reminders.json` + GUI "Add
reminder" (for reminders the wiki misses, e.g. the Harpy) · four "Choose a…"
draft tokens for pick-any-role formats (Whale's Buffet).

---

## Open ideas (only if I ask)

- Parallel rendering for a faster cold build (the `[progress]` groundwork is already there).
- Incremental art re-scraping (ETags) in `get_all_roles.py`.
- Thread the "Choose a…" tokens through the checklist (currently generate + plates only).
- A GitHub Action to build the Windows exe on release.

---

## If the local repo might be behind

I've been applying changes file-by-file as we worked, so the clone may already be
current. If you're unsure it matches the latest state, the authoritative snapshot
is the full patch from upstream base `e130c03`:

```powershell
git clone https://github.com/Jnieco/blood-on-the-clock-tower-scad-generator.git
cd blood-on-the-clock-tower-scad-generator
git reset --hard e130c03
git apply botc-windows-reminders-3mf-full.patch   # the latest full patch
uv sync
uv run pytest -q
```

Then delete the patch file. (Keep your own `roles.json`, `custom_roles.json`,
`settings.json`, `extra_reminders.json`, and checklists — those are gitignored
and yours.)
