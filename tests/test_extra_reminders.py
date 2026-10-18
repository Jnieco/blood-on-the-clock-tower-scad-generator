"""Tests for adding reminder tokens beyond what a role defines."""

import json

import pytest

import solid_maker as sm


def _roles():
    return {
        "Harpy": {"color": "red", "reminders": [], "remindersGlobal": []},
        "Fortune Teller": {"color": "blue", "reminders": ["Red herring"]},
    }


# -- applying extras --------------------------------------------------------
def test_extra_reminders_are_added_to_a_role(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"Harpy": ["Mad", "2nd"]}))
    roles = _roles()

    unmatched = sm.apply_extra_reminders(roles, path)

    assert unmatched == []
    assert roles["Harpy"]["reminders"] == ["Mad", "2nd"]


def test_role_names_match_loosely(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"fortuneteller": ["Used"]}))
    roles = _roles()

    sm.apply_extra_reminders(roles, path)

    assert "Used" in roles["Fortune Teller"]["reminders"]


def test_existing_reminders_are_not_duplicated(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"Fortune Teller": ["Red herring", "New"]}))
    roles = _roles()

    sm.apply_extra_reminders(roles, path)

    # "Red herring" was already there; it must appear once, with "New" added.
    assert roles["Fortune Teller"]["reminders"] == ["Red herring", "New"]


def test_unmatched_role_is_reported_not_raised(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"Nonexistent": ["X"]}))
    roles = _roles()

    unmatched = sm.apply_extra_reminders(roles, path)

    assert unmatched == ["Nonexistent"]


def test_comment_keys_are_ignored(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"_comment": "notes", "Harpy": ["Mad"]}))

    assert sm.apply_extra_reminders(_roles(), path) == []


def test_missing_file_is_a_no_op(tmp_path):
    assert sm.apply_extra_reminders(_roles(), tmp_path / "nope.json") == []


def test_bad_reminder_type_raises(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps({"Harpy": "Mad"}))  # should be a list

    with pytest.raises(ValueError, match="list of strings"):
        sm.apply_extra_reminders(_roles(), path)


# -- writing extras ---------------------------------------------------------
def test_add_extra_reminder_creates_the_file(tmp_path):
    path = tmp_path / "extra.json"

    added = sm.add_extra_reminder("Harpy", "Mad", path)

    assert added is True
    assert json.loads(path.read_text()) == {"Harpy": ["Mad"]}


def test_add_extra_reminder_dedupes(tmp_path):
    path = tmp_path / "extra.json"
    sm.add_extra_reminder("Harpy", "Mad", path)

    assert sm.add_extra_reminder("Harpy", "Mad", path) is False


def test_add_extra_reminder_reuses_a_loose_match(tmp_path):
    path = tmp_path / "extra.json"
    sm.add_extra_reminder("Fortune Teller", "A", path)
    sm.add_extra_reminder("fortuneteller", "B", path)

    data = json.loads(path.read_text())
    assert list(data) == ["Fortune Teller"]  # one key, not two spellings
    assert data["Fortune Teller"] == ["A", "B"]


def test_add_extra_reminder_rejects_empty(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        sm.add_extra_reminder("Harpy", "   ", tmp_path / "extra.json")


# -- integration: the whole loader path -------------------------------------
def test_load_role_definitions_applies_extras(tmp_path, monkeypatch):
    roles_json = tmp_path / "roles.json"
    roles_json.write_text(json.dumps(_roles()))
    extra = tmp_path / "extra_reminders.json"
    extra.write_text(json.dumps({"Harpy": ["Mad"]}))
    monkeypatch.setattr(sm, "EXTRA_REMINDERS_JSON", extra)

    roles = sm.load_role_definitions(roles_json, [])

    assert roles["Harpy"]["reminders"] == ["Mad"]
