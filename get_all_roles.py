"""Build roles.json: role art + team colour + reminder tokens.

Art and colour are scraped from the official wiki. Reminder-token text is not
published there in a machine-readable form, so it is merged in from the
community `townsquare` role data, which every custom-script tool already uses.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent
ROLES_JSON = PROJECT_ROOT / "roles.json"

WIKI_BASE = "https://wiki.bloodontheclocktower.com/"

# Pages to scrape for role names, art and colour.
URLS_TO_PARSE = [
    "https://wiki.bloodontheclocktower.com/Experimental",
    "https://wiki.bloodontheclocktower.com/Trouble_Brewing",
    "https://wiki.bloodontheclocktower.com/Sects_%26_Violets",
    "https://wiki.bloodontheclocktower.com/Bad_Moon_Rising",
    "https://wiki.bloodontheclocktower.com/Travellers",
    "https://wiki.bloodontheclocktower.com/Fabled",
    "https://wiki.bloodontheclocktower.com/Loric",
]

# Reminder-token text, keyed by role. Same data the official script tool uses.
REMINDERS_URL = (
    "https://raw.githubusercontent.com/bra1n/townsquare/develop/src/roles.json"
)

COLOR_BY_HEX = {
    "#800080": "purple",
    "#D4AF37": "yellow",
    "#3297F4": "blue",
    "#8C0E12": "red",
    "#3f9651": "green",
}


def normalise(name: str) -> str:
    """Key for matching role names across sources.

    The wiki and the reminder data punctuate differently, e.g. "Devil's
    Advocate", "Pit-Hag", "Lil' Monsta".
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def get_color_from_style(style: str) -> str:
    """Map an inline CSS `color:` declaration to a team colour name."""
    color_hex = None
    for declaration in (style or "").split(";"):
        parts = declaration.split(":", 1)
        if len(parts) == 2 and parts[0].strip() == "color":
            color_hex = parts[1].strip()

    # The original assumed a `color:` declaration was always present and blew
    # up with UnboundLocalError when it was not.
    if color_hex is None:
        return "unknown"

    color = COLOR_BY_HEX.get(color_hex)
    if color is None:
        print(f"Unknown color: {color_hex}")
        return "unknown"
    return color


def scrape_roles() -> dict:
    """Scrape role name -> {image, color} from the wiki."""
    roles: dict[str, dict] = {}
    for url in URLS_TO_PARSE:
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to load page {url}")

        soup = BeautifulSoup(response.text, "html.parser")

        for container in soup.select("div.small-6.medium-6.large-2.columns"):
            role_span = container.select_one("span[data-role]")
            image_tag = container.select_one("img.thumbimage")
            if not (role_span and image_tag):
                continue

            role_name = role_span.get_text(strip=True)
            roles[role_name] = {
                "image": WIKI_BASE + image_tag.get("src"),
                "color": get_color_from_style(role_span.get("style")),
            }
        print(f"Scraped {url}")
    return roles


def fetch_reminders() -> dict:
    """Fetch reminder-token text, keyed by normalised role name."""
    response = requests.get(REMINDERS_URL, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to load reminder data from {REMINDERS_URL}")

    reminders = {}
    for entry in response.json():
        name = entry.get("name")
        if not name:
            continue
        reminders[normalise(name)] = {
            "reminders": entry.get("reminders", []),
            # Global reminders (e.g. Drunk) are not tied to a seated player,
            # but they are still physical tokens the Storyteller needs.
            "remindersGlobal": entry.get("remindersGlobal", []),
            "team": entry.get("team", ""),
        }
    return reminders


def main():
    roles = scrape_roles()
    reminders = fetch_reminders()

    matched = 0
    unmatched = []
    token_count = 0

    for role_name, data in roles.items():
        extra = reminders.get(normalise(role_name))
        if extra is None:
            unmatched.append(role_name)
            data["reminders"] = []
            data["remindersGlobal"] = []
            continue

        matched += 1
        data["reminders"] = extra["reminders"]
        data["remindersGlobal"] = extra["remindersGlobal"]
        if extra["team"]:
            data["team"] = extra["team"]
        token_count += len(extra["reminders"]) + len(extra["remindersGlobal"])

    print(f"\nRoles scraped:            {len(roles)}")
    print(f"Matched to reminder data: {matched}")
    print(f"Reminder tokens to make:  {token_count}")
    if unmatched:
        # Usually brand-new experimental roles the reminder data has not caught
        # up with. Their character tokens still generate fine.
        print(f"\nNo reminder data for {len(unmatched)} role(s):")
        for name in unmatched:
            print(f"  - {name}")

    if not roles:
        print("No roles found - the wiki markup may have changed.", file=sys.stderr)
        raise SystemExit(1)

    with open(ROLES_JSON, "w", encoding="utf-8") as handle:
        json.dump(roles, handle, indent=4, ensure_ascii=False)
    print(f"\nWrote {ROLES_JSON}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
