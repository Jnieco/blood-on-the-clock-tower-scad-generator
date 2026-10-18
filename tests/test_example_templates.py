"""The *.example.json templates must load cleanly through their real loaders.

The README tells people to copy these files as a starting point, so a template
that the loader rejects would fail on someone's very first run.
"""

from pathlib import Path

import pytest

import solid_maker as sm

REPO_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_ROLES_EXAMPLE = REPO_ROOT / "custom_roles.example.json"
SCRIPTS_EXAMPLE = REPO_ROOT / "scripts.example.json"
EXTRA_REMINDERS_EXAMPLE = REPO_ROOT / "extra_reminders.example.json"


@pytest.fixture
def no_user_extras(monkeypatch, tmp_path):
    # load_role_definitions also applies the user's own extra_reminders.json;
    # point it somewhere empty so only the template is under test.
    monkeypatch.setattr(sm, "EXTRA_REMINDERS_JSON", tmp_path / "absent.json")


def _custom_roles():
    return sm.load_role_definitions(
        roles_json=None, custom_jsons=[CUSTOM_ROLES_EXAMPLE]
    )


def test_custom_roles_example_loads_without_problems(no_user_extras):
    roles = _custom_roles()

    # The "_Beacon" sample is documentation only, like "_comment".
    assert sorted(roles) == ["Lantern Keeper", "Night Order"]
    assert roles["Night Order"]["image"] is None  # text-only token
    assert roles["Night Order"]["color"] == "#FF8800"
    for name, entry in roles.items():
        assert sm.validate_role_entry(name, entry) == []


def test_scripts_example_loads_and_names_known_custom_roles(no_user_extras):
    scripts = sm.load_scripts(SCRIPTS_EXAMPLE)

    assert len(scripts["Trouble Brewing"]) == 22
    custom = {sm.normalise_role_name(name) for name in _custom_roles()}
    for role in scripts["House Homebrew"]:
        assert sm.normalise_role_name(role) in custom


def test_extra_reminders_example_matches_every_role(no_user_extras):
    roles = _custom_roles()
    roles["Harpy"] = {"color": "red", "reminders": [], "remindersGlobal": []}

    unmatched = sm.apply_extra_reminders(roles, EXTRA_REMINDERS_EXAMPLE)

    assert unmatched == []
    assert roles["Harpy"]["reminders"] == ["Mad", "2nd Mad"]
    assert "Snuffed" in roles["Lantern Keeper"]["reminders"]
