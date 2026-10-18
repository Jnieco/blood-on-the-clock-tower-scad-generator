# Blood on the Clocktower SCAD Generator

Generates 3D-printable **character tokens** and **reminder tokens** for *Blood on
the Clocktower*, as OpenSCAD (`.scad`) and mesh (`.stl`) files.

Each token is produced two ways so you can choose how to print it:

- a ready-to-print **two-colour `.3mf`** per token (the disc plus a flush,
  colour-inlaid art/text region - smooth on top so tokens can't be identified
  by touch in the bag, and printed art-side-down for the cleanest colour
  boundary), and
- the individual parts as **`.stl`** files (a shared base disc plus per-token
  overlays) if you'd rather assemble or print single-colour yourself.

| Token | Size | Text |
| --- | --- | --- |
| Character | 45 mm | Role name curved around the rim |
| Reminder | 25 mm | Reminder label, straight, auto-wrapped to fit |

Reminder text comes from the same community data used by the online script tool,
so every role's reminders (e.g. Poisoner -> `POISONED`, Fortune Teller ->
`RED HERRING`) are generated automatically.

### Adding reminders the data misses

Some roles need a reminder the community data doesn't list (the Harpy is a known
case), and homebrew roles may want their own. Put extra labels in
`extra_reminders.json`, keyed by role name:

```json
{
  "Harpy": ["Mad", "2nd Mad"],
  "My Homebrew Role": ["Cursed"]
}
```

These are added on top of whatever the role already has, for official and custom
roles alike. Names are matched loosely (`Fortune Teller` = `fortuneteller`), and
a label that already exists is not duplicated. Copy `extra_reminders.example.json`
to get started, or - easier - use the **Add reminder...** button on the GUI's
Checklist tab, which writes the file for you. Whatever you add flows through the
tokens and the checklist.

### "Choose a ..." draft tokens

For draft formats like Whale's Buffet, where players pick any role of a type from
the script, there are four full-size (45 mm) tokens: **Choose a Townsfolk**,
**Choose an Outsider**, **Choose a Minion** and **Choose a Demon**, in the
standard good-team blue and evil-team red. They belong to no script and are
reusable across all of them, so they are generated only on request:

```powershell
uv run python solid_maker.py --choose-tokens      # alongside a normal run
uv run python solid_maker.py --choose-only         # just these four
uv run python make_plates.py --choose-only --generate   # make + lay them out
```

In the GUI, tick **Also "Choose a..." draft tokens** on the Tokens tab; the same
box also includes them when you press Make plates.

---

## Requirements

- **Python 3.10+** - managed for you by uv below.
- **[uv](https://docs.astral.sh/uv/)** - Python environment/dependency manager.
- **[OpenSCAD](https://openscad.org/downloads.html)** - renders `.scad` files to
  `.stl`. Only needed for STL output; you can generate `.scad` files without it.

You no longer need ImageMagick or the potrace binary. Image tracing now runs in
pure Python, which is the main change that makes this work on Windows.

---

## Setup on Windows 11 (PowerShell)

Run these in **Windows PowerShell** (or the VS Code terminal).

### 1. Install uv

```powershell
winget install --id=astral-sh.uv -e
```

Close and reopen the terminal afterwards so `uv` is on your `PATH`. Check it:

```powershell
uv --version
```

### 2. Install OpenSCAD

```powershell
winget install --id=OpenSCAD.OpenSCAD -e
```

The installer does **not** add OpenSCAD to your `PATH`, but the scripts look in
the standard install folder automatically, so this usually just works. If you
installed it somewhere custom, point the scripts at it for the current session:

```powershell
$env:OPENSCAD_PATH = "C:\Program Files\OpenSCAD\openscad.exe"
```

(To make that permanent, add it via *System Properties -> Environment
Variables*, or your PowerShell `$PROFILE`.)

### 3. Get the code and create the environment

```powershell
git clone https://github.com/Jnieco/blood-on-the-clock-tower-scad-generator.git
cd blood-on-the-clock-tower-scad-generator
uv sync
```

`uv sync` reads `pyproject.toml`/`uv.lock` and builds a `.venv` with the exact
pinned dependencies. You do not activate it manually - prefix commands with
`uv run` and uv uses the right environment.

---

## Usage

### The window (easiest)

On Windows, **double-click `run_gui.bat`**. It opens the same window as:

```powershell
uv run python botc_gui.py
```

A small desktop window that runs everything below without typing commands: pick
a script (or import one), tick the options, and press **Generate tokens** or
**Update checklist**. Output appears in the log pane as it runs, the progress bar and status line
track it token by token ("12 of 22: Poisoner"), and there are buttons to open
the `3mf` folder and the checklist.

It is only a launcher - it runs exactly the same scripts documented below, so
anything you can do here you can also do from the command line, and vice versa.
Two things it does for you:

- The checklist always merges, so using the window can never discard ticks.
- Choosing a script writes a separate `checklist_<script>.xlsx` and leaves your
  master `print_checklist.xlsx` alone.

The window has three tabs: **Tokens** (generate, plate, proof),
**Checklist** and **Settings**. The output pane sits below them, so you can
watch a run from whichever tab started it.

The **Checklist tab** shows your tokens in a table you can tick off directly,
without opening Excel: filter to a script, click the **Done** column to mark a
token printed (double-click a multi-copy reminder to set an exact count), and
watch the "N of M token types printed" counter. **Unprinted only** hides what is
finished. **Save to Excel** writes the same workbook the command line uses, so
the two stay interchangeable - edit in either. A script filter saves to its own
`checklist_<script>.xlsx`, matching the command-line behaviour.

Tkinter ships with Python, so this needs no extra dependencies. On Linux you may
need `sudo apt install python3-tk`.

### A standalone app (no Python needed)

To get a single double-click executable - useful on a machine without Python, or
to hand to another player:

```powershell
uv run --with pyinstaller python build_exe.py
```

The result appears in `dist/` and runs on its own. Two things to know:

- **OpenSCAD is not bundled.** It is a separate program this calls, so it still
  has to be installed on whatever machine runs the app.
- Your data (`roles.json`, `settings.json`, `custom_roles.json`, `scripts.json`)
  and the output folders live **next to the executable**, not inside it, so you
  can edit them and they survive rebuilding the app.

---

## Tuning sizes without editing code

Every dimension worth adjusting lives in `settings.json`, so your tuning is not
lost when you drop in a new `solid_maker.py`. Write a starter file containing
every setting at its current value:

```powershell
uv run python solid_maker.py --write-settings
```

Then edit what you want and re-run; deleting a line falls back to the built-in
default:

```json
{
  "TEXT_SIZE": 5.2,
  "ROLE_ICON_DIAMETER": 30.5,
  "ROLE_EXTRUDE_DEPTH": 0.3,
  "REMINDER_ICON_DILATE": 0.25,
  "OVERLAY_COLOR_BY_NAME": { "red": "#FF0000" }
}
```

Useful ones, from tuning against real prints:

| Setting | What it does |
|---|---|
| `TEXT_SIZE` | Size of the curved role name |
| `ROLE_ICON_DIAMETER` | Cap on the character art; it is fitted below this to clear the name |
| `ROLE_ICON_SHIFT_Y` | Nudges the art up the face, away from the name |
| `ROLE_EXTRUDE_DEPTH` | Depth of the colour inlay. 0.3-0.4 mm gives better colour coverage on a 0.2 mm nozzle |
| `REMINDER_ICON_DILATE` | Fattens thin reminder art so it survives at small size |
| `REMINDER_ICON_MAX_FRACTION` | How much of a reminder token the art may fill |
| `OVERLAY_COLOR_BY_NAME` | Team colours, if your filaments differ. Merged, so you can override just one |

Values are range-checked and unknown names are reported (with a suggestion for
typos), so a mistake is caught rather than silently ignored:

```
! settings.json: TEXT_SIZ: not a setting. Did you mean 'TEXT_SIZE'?
! settings.json: ROLE_EXTRUDE_DEPTH: 99.0 is outside the sensible range 0.05 to 2.0
```

Changing a setting invalidates the build cache for whatever it affects, so the
next run rebuilds only the tokens it actually changed.

---

### The command line

Two steps: build the role data, then generate the tokens.

```powershell
# 1. Scrape role art + colours and merge in reminder text -> roles.json
uv run python get_all_roles.py

# 2. Generate every character and reminder token (.scad + .stl)
uv run python solid_maker.py
```

Output lands in these folders (all git-ignored):

| Folder | Contents |
| --- | --- |
| `pngs/`, `grey_pngs/` | Downloaded and greyscaled role art |
| `svgs/` | Traced vector art |
| `scads/` | OpenSCAD source per token |
| `stls/` | Individual meshes - a shared base disc plus one overlay per token |
| `3mf/` | **Ready-to-print two-colour tokens** - one file per token |

### Printing in BambuStudio

The `3mf/` files are the easy path. Each one contains the disc and a flush,
colour-inlaid art/text region, tagged with **face colours** (the colour format
BambuStudio's importer actually reads), so:

1. Drag a `.3mf` from `3mf/` straight onto the BambuStudio window (or
   *File -> Import -> Import 3MF/STL...*).
2. BambuStudio shows its **Standard 3MF Color Parsing** dialog and matches the
   two colours to your loaded filaments - adjust the mapping if you like and
   click OK.
3. Slice and print.

Two deliberate choices make these print cleanly as two colours:

- **The art is inlaid flush**, not raised: the printed face is smooth, so tokens
  can't be told apart by touch when drawn from a bag. The disc carries a shallow
  art-shaped pocket that the coloured inlay fills exactly (both finish level at
  2.0 mm).
- **The art faces down by default**, so the two-colour face prints against the
  build plate where the finish is cleanest and the boundary between the two
  filaments is least noticeable. Pass `--art-up` if you'd rather it face up.

Filenames carry the team colour, e.g. `red_Poisoner_coin.3mf`,
`blue_Empath_coin.3mf`, `red_Poisoner_Poisoned_reminder.3mf`.

Prefer to drive colours yourself, or print single-colour? Use the `stls/`
instead: one shared base disc (`000_coin_base...`, `000_reminder_base...`) plus
a per-token overlay.

### Handy options

```powershell
# Only write .scad files (fast; does NOT require OpenSCAD)
uv run python solid_maker.py --skip-stl

# Skip the combined 3MF files and keep only STLs
uv run python solid_maker.py --no-3mf

# Orient 3MF tokens art-side up (default is art-side down for a cleaner face)
uv run python solid_maker.py --art-up

# Character tokens only, skip reminders
uv run python solid_maker.py --no-reminders

# Just one role - great for testing your setup
uv run python solid_maker.py --only Poisoner

# Only one script/edition (see below)
uv run python solid_maker.py --script "Trouble Brewing"

# Straight from a downloaded script-creator .json
uv run python solid_maker.py --script-file Catfishing.json

# Rebuild everything, ignoring the cache of unchanged files
uv run python solid_maker.py --force

# Custom/homebrew tokens (see below)
uv run python solid_maker.py --custom my_script.json
uv run python solid_maker.py --custom-only     # skip the official roles
uv run python solid_maker.py --no-custom       # ignore custom_roles.json
```

A good first smoke test (needs `roles.json`, so run `get_all_roles.py` first):

```powershell
uv run python solid_maker.py --only Poisoner
```

### Only what changed gets rebuilt

Rendering with OpenSCAD is the slow part of a run, and most runs change only a
role or two, so finished work is reused. Each STL and 3MF records a fingerprint
of what it was built from in `.build_cache.json`; if that still matches and the
file is on disk, it is skipped:

```
Done: 22 character token(s), 22 reminder token(s).
Reused 86 unchanged file(s); built 4. Use --force to rebuild everything.
```

The fingerprint is the generated `.scad` text, which captures everything that
determines the geometry - artwork, names, sizes, and any change to the geometry
code - because the art is written into it as coordinates. The 3MF fingerprint
adds the colour and orientation, so recolouring a role or switching `--art-up`
rewrites the 3MF without re-rendering the STL.

So a rebuild happens exactly when you'd expect: you change art, rename a role,
adjust a size constant, change a colour, or delete an output. Everything else is
reused. `--force` rebuilds regardless, which you only need if the outputs were
edited outside this tool.

Roles that list the same reminder several times (the Imp's three `Dead` tokens)
build one file rather than three; the checklist already tracks how many copies
to print.

---

## Custom and homebrew tokens

Copy `custom_roles.example.json` to `custom_roles.json` and edit it. It is
picked up automatically, uses the same schema as `roles.json`, and is merged
**last** - so an entry can add a brand-new character or override an official one
(handy for swapping in your own art for a role you don't like).

```json
{
  "Beacon": {
    "image": "custom_art/beacon.png",
    "color": "blue",
    "reminders": ["Watched"],
    "remindersGlobal": []
  },
  "Night Order": {
    "color": "#FF8800",
    "reminders": ["First night", "Other nights"]
  }
}
```

- **`image`** - a URL, or a path to a local PNG resolved relative to the JSON
  file. **Omit it entirely for a text-only token** (useful for utility tokens
  like night-order or table markers).
- **`color`** - `blue` / `red` / `purple` / `yellow` / `green`, or any
  `#RRGGBB` hex if you're printing in a filament of your own.
- **`reminders` / `remindersGlobal`** - default to empty if left out.

Everything downstream is unchanged: custom tokens get the same flush two-colour
`.3mf`, printed art-side-down, and land in the same output folders.

Keys beginning with `_` are ignored, so you can keep `"_comment"` notes in the
file (JSON has no comment syntax).

### Getting artwork that prints well

Trace quality is the main thing that decides whether a small token reads
clearly. Solid, bold, dark shapes on a transparent (or white) background work
best; fine outlines and grey shading do not survive at reminder-token size.

The generator checks each image and warns without stopping, e.g.

```
  ! Hairline.png: 100% of the art is finer than 0.4 mm at reminder-token size.
    Thin strokes tend to disappear when printed; consider bolder artwork.
```

It also flags art that is nearly blank (usually the wrong file, or artwork lost
when transparency was flattened) and art that is nearly solid (a dark background
that will trace as one big blob). If you hit the thin-stroke warning, thicken
the artwork, or raise `REMINDER_ICON_DILATE` near the top of `solid_maker.py`.

If you use a custom `#RRGGBB` colour, BambuStudio still maps it to whichever
filament you choose at import; the hex only sets what you see in the preview.

---

## Scripts and editions

The quickest way is to import a script straight from the official script tool.
Download the script's `.json` (from `script.bloodontheclocktower.com` or a
script-hosting site) and run:

```powershell
uv run python import_script.py Catfishing.json
uv run python solid_maker.py --script "Catfishing"
uv run python make_print_checklist.py --script "Catfishing"
```

Both export formats are accepted - the plain list of character ids, and the list
of `{"id": ...}` objects - and the script's own name and author are read from
its `_meta` entry. Several files can be imported at once, and re-importing a
script updates it in place. Character ids are matched loosely, so `fortuneteller`
finds `Fortune Teller` and `pithag` finds `Pit-Hag` without any lookup table.

If a script file defines **homebrew characters inline** (with their own name,
team and reminders), those are written into `custom_roles.json` so their tokens
get generated too. Their colour is taken from whatever colour your `roles.json`
already uses for that team. Pass `--no-homebrew` to skip that.

To use a downloaded script once without saving it, skip the import:

```powershell
uv run python solid_maker.py --script-file Catfishing.json
uv run python make_print_checklist.py --script-file Catfishing.json
```

Characters with no definition yet (a brand-new role, or homebrew you haven't
added) are named and skipped rather than failing the run.

### Defining scripts by hand

Alternatively, copy `scripts.example.json` to `scripts.json` and list the roles
yourself:

```json
{
  "Trouble Brewing": ["Washerwoman", "Librarian", "Imp", "..."],
  "House Homebrew": ["Beacon", "Night Order"]
}
```

Role names match loosely here too, and a role may appear on several scripts.

---

## Checking your art before a big run

Rendering already warns about weak art as you hit each token. To see it all at
once, run the pre-flight report:

```powershell
uv run python art_preflight.py
uv run python art_preflight.py --script "Trouble Brewing"
```

It downloads or copies each role's art, flattens it to greyscale, and lists
every role with a problem - nearly blank, nearly solid, or too fine to print at
reminder size - in one report, without any of the slow SCAD/STL work. In the
GUI, press **Check art**. Exit status is non-zero if anything needs attention.

## A proof sheet of every token

```powershell
uv run python make_contact_sheet.py
uv run python make_contact_sheet.py --script "Trouble Brewing"
```

This writes `contact_sheet.png`: every token drawn as its coloured disc with the
traced art and name (reminders too), on one page. It is a fast way to catch a
wrong image, an off-centre or faint icon, or a mis-scaled name across the whole
set before you commit filament. In the GUI, press **Contact sheet**. Run the
generator (or `--skip-stl`) first so the traced art exists.

## Tuning sizes in the window

The **Settings** tab exposes the sizes from `settings.json` as fields - disc
diameter, name size, art size and lift, inlay depth, and so on. Edit a value and
press **Save settings**; it is range-checked the same way the file is, and
applies on the next Generate. Blank a field to fall back to the built-in default,
or press **Reset to defaults** to clear the file. This is the same
`settings.json` documented below, so you can still edit it by hand if you prefer.

## Laying out build plates

Rather than dragging tokens into the slicer one at a time, pack them onto plates:

```powershell
uv run python make_plates.py
uv run python make_plates.py --script "Trouble Brewing"
uv run python make_plates.py --bed 256x256 --gap 4
```

This writes `plates/plate_01_red.3mf`, `plate_02_blue.3mf`, and so on - each a
grid of tokens **grouped by colour**. A plate therefore needs only two filaments,
the dark disc plus that one colour, and one AMS swap between plates. Since the
colour is only the top 0.3 mm, grouping this way is most of the filament saving.
In the GUI, press **Make plates**.

The tokens must already be generated (run the generator first). `--generate`
does both in one step. Options: `--bed WIDTHxDEPTH` (default 256x256), `--gap`
between tokens, `--margin` around the plate edge, `--script` / `--script-file`
to limit which tokens, and `--art-up` to match an art-up generate.

Each plate is centred on the bed origin, so it loads ready to slice. If a colour
has more tokens than fit, it spills onto another plate of the same colour.

### Skipping what you've already printed

By default the plate builder reads your checklist and leaves off any token that
is fully printed, so as you work through a collection each run only plates
what's left:

```
Skipping 12 already-printed token(s) from print_checklist.xlsx. Use --all to include them.
```

It uses the checklist for the current selection (`checklist_<script>.xlsx` for a
script, otherwise `print_checklist.xlsx`); point it elsewhere with `--checklist
FILE`. A reminder you've only partly printed (2 of 3) still counts as unprinted,
so it keeps appearing until finished. Overrides:

- `--all` plates everything, printed or not.
- `--reprint ROLE` forces one role's tokens back on even if printed (repeatable).

In the checklist tab, right-click a row (or select it and press **Reprint
selected**) to clear its printed count, so it returns to the next plate. The
Generate tab has a **Plate all tokens (ignore printed)** checkbox for `--all`.

---

## Tracking what you've printed

`make_print_checklist.py` writes an Excel workbook with one row per physical
token, so you can tick off what's already printed:

```powershell
uv run python make_print_checklist.py
uv run python make_print_checklist.py --script "Trouble Brewing"
uv run python make_print_checklist.py --merge      # keep existing ticks
```

Rebuilding writes a fresh sheet, so anything already ticked off in the output
file would be lost. If the file records progress, the script refuses to run
unless you pass `--merge` (keep it) or `--overwrite` (start fresh).
The **Tokens** sheet lists every character and reminder token with its script,
team colour, source (official or custom), quantity needed, and the `.3mf`
filename the generator produces - so a row points straight at the file to slice.
Fill in the two shaded columns (**Qty printed** and **Notes**); everything else
is generated or calculated. **Status** updates itself to `Printed` or `Partial`,
and printed rows turn green.

A character that needs several copies of one reminder (the Imp's three `Dead`
tokens, say) is a single row with a quantity, not repeated rows.

The **Summary** sheet totals progress overall and by token type, team colour,
source and script, all with live formulas. **How to use** holds a short legend
and an example row.

Re-running rebuilds the workbook, so pass `--merge` to carry your progress over
to the new version after adding roles or homebrew.

### Keeping your progress

`--merge` reads the progress out of the workbook it is about to overwrite. That
is what you want for the full sheet:

```powershell
uv run python make_print_checklist.py --merge
```

`--script` narrows the sheet to one script, so combining it with `--merge` on
your **master** file replaces that file with the smaller one - and the roles
outside the script are no longer in it. The script warns when this happens, but
the safe pattern is to keep the master intact and write the script sheet
somewhere else, seeding it from the master with `--merge-from`:

```powershell
uv run python make_print_checklist.py `
  --script "My Script" `
  --merge-from print_checklist.xlsx `
  --output my_script.xlsx
```

That leaves `print_checklist.xlsx` untouched and gives you a focused sheet for
the script, already showing what you've printed. Ticking things off in the
smaller sheet does not flow back to the master, so treat one file as the
source of truth - usually the full one.

---

## Using it in VS Code

Open the folder in VS Code. It will offer to install the recommended
extensions (Python + Ruff) and will pick up the `.venv` uv created. If the
interpreter is not selected automatically, run **Python: Select Interpreter**
from the Command Palette and choose the one inside `.\.venv`.

Format-on-save and the test runner are pre-configured in `.vscode/settings.json`.

---

## Development

```powershell
uv run pytest                 # run the tests
uv run ruff check .           # lint
uv run ruff format .          # auto-format
```

CI runs the tests on **both Ubuntu and Windows**, plus a render smoke test that
installs OpenSCAD and confirms a token actually exports to STL.

---

## Troubleshooting

**`Could not find OpenSCAD`** - install it (step 2) and either restart the
terminal or set `$env:OPENSCAD_PATH` to the full path of `openscad.exe`.

**Text looks wrong / plain on the tokens** - the tokens embed the bundled
`assets/Dumbledor1_fixed.ttf` directly, so this should not happen. If you edited
the SCAD header, make sure the `use <...Dumbledor1_fixed.ttf>;` line is intact;
without it OpenSCAD silently substitutes a default font.

**A role has no reminder tokens** - a few brand-new experimental roles may not
be in the reminder dataset yet. `get_all_roles.py` lists any it could not match;
their character tokens are still generated normally.

**`ModuleNotFoundError: pkg_resources`** - you are on a setuptools build that
removed it. `uv sync` pins a compatible version; re-run `uv sync` to fix the
environment.
