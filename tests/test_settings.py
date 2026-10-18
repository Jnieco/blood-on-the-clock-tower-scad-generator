"""Tests for settings.json, which keeps tuning out of the Python source."""

import json

import pytest

import solid_maker as sm


def _namespace(**overrides):
    """A stand-in module namespace, so tests never mutate the real module."""
    base = {name: sm.__dict__[name] for name in sm.TUNABLE_SETTINGS}
    base["OVERLAY_COLOR_BY_NAME"] = dict(sm.OVERLAY_COLOR_BY_NAME)
    base.update(overrides)
    return base


def test_valid_settings_are_applied():
    namespace = _namespace()

    problems = sm.apply_settings({"TEXT_SIZE": 6.0, "REMINDER_MAX_LINES": 3}, namespace)

    assert problems == []
    assert namespace["TEXT_SIZE"] == 6.0
    assert namespace["REMINDER_MAX_LINES"] == 3


def test_unknown_setting_is_reported_with_a_suggestion():
    problems = sm.apply_settings({"TEXT_SIZ": 6.0}, _namespace())

    assert len(problems) == 1
    assert "not a setting" in problems[0] and "TEXT_SIZE" in problems[0]


def test_out_of_range_values_are_rejected():
    namespace = _namespace()

    problems = sm.apply_settings({"ROLE_EXTRUDE_DEPTH": 99}, namespace)

    assert "outside the sensible range" in problems[0]
    assert namespace["ROLE_EXTRUDE_DEPTH"] != 99  # left at the default


def test_wrong_types_are_rejected():
    problems = sm.apply_settings(
        {"REMINDER_MAX_LINES": "two", "FONT": ""}, _namespace()
    )

    assert len(problems) == 2


def test_one_bad_setting_does_not_discard_the_good_ones():
    namespace = _namespace()

    problems = sm.apply_settings({"TEXT_SIZE": 6.0, "NOPE": 1}, namespace)

    assert len(problems) == 1
    assert namespace["TEXT_SIZE"] == 6.0


def test_comment_keys_are_ignored():
    assert sm.apply_settings({"_comment": "notes"}, _namespace()) == []


def test_team_colours_merge_rather_than_replace():
    namespace = _namespace()

    problems = sm.apply_settings(
        {"OVERLAY_COLOR_BY_NAME": {"red": "#FF0000"}}, namespace
    )

    assert problems == []
    assert namespace["OVERLAY_COLOR_BY_NAME"]["red"] == "#FF0000FF"  # alpha added
    # Colours you did not mention keep their defaults.
    assert (
        namespace["OVERLAY_COLOR_BY_NAME"]["blue"] == sm.OVERLAY_COLOR_BY_NAME["blue"]
    )


def test_bad_team_colour_is_reported():
    problems = sm.apply_settings(
        {"OVERLAY_COLOR_BY_NAME": {"red": "crimson"}}, _namespace()
    )

    assert "#RRGGBB" in problems[0]


def test_load_settings_returns_empty_when_absent(tmp_path):
    assert sm.load_settings(tmp_path / "nope.json") == {}


def test_load_settings_rejects_a_non_object(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps([1, 2, 3]))

    with pytest.raises(ValueError, match="object of setting"):
        sm.load_settings(path)


def test_current_settings_round_trips_through_apply():
    """--write-settings must produce a file that applies cleanly."""
    namespace = _namespace()

    assert sm.apply_settings(sm.current_settings(), namespace) == []


def test_every_tunable_has_a_current_value():
    values = sm.current_settings()

    assert set(values) == set(sm.TUNABLE_SETTINGS) | {sm.SETTINGS_COLOUR_KEY}
