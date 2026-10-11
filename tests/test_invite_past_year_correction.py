"""A past invite date stays in the past; only a wrong-era year is corrected (11 Oct).

`_fix_past_year` moved any date more than a week past into next year when the
same day this year was past too: a ValueLabs invite for 16 Sep 2026, uploaded
on 11 Oct 2026, was booked for 16 Sep 2027. Three confirmed slots sat in 2027.
Next year is now only for the first weeks of January read in late December.
"""
from __future__ import annotations

from datetime import date

import pytest

from features.ollama_invite_extract import _fix_past_year


@pytest.mark.parametrize("read, today, expected", [
    ("2026-09-16", date(2026, 10, 11), "2026-09-16"),  # recorded late: stays past (was 2027-09-16)
    ("2026-06-19", date(2026, 10, 9), "2026-06-19"),   # stays past (was 2027-06-19)
    ("2023-09-16", date(2026, 10, 11), "2026-09-16"),  # model's old year -> this year
    ("2023-10-20", date(2026, 10, 11), "2026-10-20"),  # old year, upcoming this year
    ("2026-10-08", date(2026, 10, 11), "2026-10-08"),  # within a week: as read
    ("2026-10-20", date(2026, 10, 11), "2026-10-20"),  # upcoming: as read
    ("2026-01-05", date(2026, 12, 28), "2027-01-05"),  # "5 Jan" read in late December
    ("2025-02-10", date(2026, 12, 28), "2027-02-10"),  # within 60 days of next year
    ("2026-03-15", date(2026, 12, 28), "2026-03-15"),  # far from next year: stays past
    ("2024-02-29", date(2026, 10, 11), "2024-02-29"),  # no 29 Feb this year: left alone
    ("", date(2026, 10, 11), ""),
    ("16/09/2026", date(2026, 10, 11), "16/09/2026"),
])
def test_year_correction(read, today, expected):
    assert _fix_past_year(read, today=today) == expected
