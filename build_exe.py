"""Build a standalone, double-click executable of the token generator.

    uv run --with pyinstaller python build_exe.py

The result lands in dist/ and needs no Python installed to run. OpenSCAD is NOT
bundled: it is a separate program the executable calls, so it still has to be
installed on the machine that runs this.

Your data (roles.json, settings.json, custom_roles.json, scripts.json) and the
output folders live next to the executable, not inside it, so you can edit them
and they survive rebuilding.
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "BOTC-Token-Generator"

# Imported by name at runtime (see tool_command in botc_gui.py), so PyInstaller
# cannot discover them by following imports.
HIDDEN_IMPORTS = [
    "solid_maker",
    "make_print_checklist",
    "import_script",
    "make_plates",
    "make_contact_sheet",
    "art_preflight",
    "openpyxl",
    "potrace",
    "manifold3d",
    "solid",
    "PIL.Image",
    "PIL.ImageFilter",
    "PIL.ImageFont",
]


def build():
    if shutil.which("pyinstaller") is None:
        sys.exit(
            "PyInstaller not found. Run:\n"
            "  uv run --with pyinstaller python build_exe.py"
        )

    # ';' on Windows, ':' elsewhere - PyInstaller's own convention.
    separator = ";" if sys.platform.startswith("win") else ":"
    command = [
        "pyinstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        # No console window behind the GUI on Windows.
        "--windowed",
        "--name",
        APP_NAME,
        # The tools are local modules imported by name at runtime, so PyInstaller
        # needs to be told where to look for them as well as to include them.
        "--paths",
        str(PROJECT_ROOT),
        "--add-data",
        f"{PROJECT_ROOT / 'assets'}{separator}assets",
    ]
    for module in HIDDEN_IMPORTS:
        command += ["--hidden-import", module]
    command.append(str(PROJECT_ROOT / "botc_gui.py"))

    print(" ".join(command))
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)

    built = (
        PROJECT_ROOT
        / "dist"
        / (f"{APP_NAME}.exe" if sys.platform.startswith("win") else APP_NAME)
    )
    print(f"\nBuilt {built}")
    print(
        "Copy it wherever you like. On first run it will create its folders "
        "beside itself.\nRemember that OpenSCAD must be installed separately."
    )


if __name__ == "__main__":
    build()
