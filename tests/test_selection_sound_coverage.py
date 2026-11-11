"""Every Selection Related alert must be able to make a sound.

The backend's selection group and the dashboard's sound list were maintained
separately and drifted: `final_round_cleared` and `hr_confirmation` were in
core/recruitment_mail_store.py but in neither list in mailAlertSound.js, so
isTrackedMailAlert() returned false and those alerts landed on the Selection
Related tab in silence. Nothing failed, nothing logged - the alert was simply
only visible to someone already looking at the screen.

The lists live in two languages, so nothing but a test can hold them together.
"""

from __future__ import annotations

import pathlib
import re

from core import recruitment_mail_store as store

SOUND_JS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "dashboard" / "src" / "utils" / "mailAlertSound.js"
)


def js_list(name: str) -> set[str]:
    """Read one exported string array out of the dashboard source."""
    source = SOUND_JS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\]", source, re.S)
    assert match, f"{name} not found in {SOUND_JS.name}"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_dashboard_sounds_every_backend_selection_status():
    missing = store.SELECTION_RELATED_CLASSIFICATIONS - js_list("SELECTION_CLASSIFICATIONS")
    assert not missing, (
        "these reach the Selection Related tab with no sound: " + ", ".join(sorted(missing))
    )


def test_the_dashboard_sounds_every_backend_interview_status():
    missing = store.INTERVIEW_RELATED_CLASSIFICATIONS - js_list("INTERVIEW_BOOKING_CLASSIFICATIONS")
    assert not missing, (
        "these reach the Interview Related tab with no sound: " + ", ".join(sorted(missing))
    )


def test_the_dashboard_invents_no_status_the_backend_does_not_have():
    """A sound for a classification that cannot occur is dead configuration."""
    known = (
        store.SELECTION_RELATED_CLASSIFICATIONS
        | store.INTERVIEW_RELATED_CLASSIFICATIONS
    )
    extra = (js_list("SELECTION_CLASSIFICATIONS") | js_list("INTERVIEW_BOOKING_CLASSIFICATIONS")) - known
    assert not extra, "unknown classifications in the sound lists: " + ", ".join(sorted(extra))


def test_the_two_specific_statuses_that_were_silent():
    """Named so a future edit that drops them again fails for the right reason."""
    selection = js_list("SELECTION_CLASSIFICATIONS")
    assert "final_round_cleared" in js_list("INTERVIEW_BOOKING_CLASSIFICATIONS")
    assert "final_round_cleared" not in selection
    assert "hr_confirmation" in selection
