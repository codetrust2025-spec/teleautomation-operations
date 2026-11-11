"""Mail Alerts groups by person and actual employer, and checks Joining Confirmed.

Live, 12 Nov 2026: one candidate's Fractal hiring sat in two cards ("Fractal",
10 mails via Workday, and "Fractal Analytics", 8 via fractal.ai), HCLTech in
two ("HCLTech", "HCL Technologies Limited", both hcltech.com), and of 13
visible "Joining Confirmed" alerts only one mail said joining was confirmed;
the rest were background checks, onboarding tasks and welcome mails. Names
and companies below are invented; the wording is production's.
"""
from __future__ import annotations

import pytest

from core import recruitment_mail_store as store


def variant(key, *domains, n=1):
    return {"key": key, "n": n, "domains": set(domains), "name": key}


@pytest.mark.parametrize("a,b", [
    (variant("acme"), variant("acme analytics")),                       # name is a word prefix
    (variant("northwind services"), variant("northwind services private limited tcs")),
    (variant("glob", "globtech.com"), variant("globtech", "globtech.com")),  # stem + employer's own domain
])
def test_variants_of_one_employer_merge(a, b):
    assert store._same_employer(a, b)


@pytest.mark.parametrize("a,b", [
    (variant("accenture", "aerial.co.in"), variant("aerial telecom solutions", "aerial.co.in")),  # agency domain
    (variant("citi", "myworkday.com"), variant("trintech", "myworkday.com")),                    # shared ATS
    (variant("glob", "myworkday.com"), variant("globtech", "myworkday.com")),                    # stem, ATS only
    (variant("tata consultancy services"), variant("tata elxsi")),                              # different employers
])
def test_different_employers_stay_apart(a, b):
    assert not store._same_employer(a, b)


JOINING = "Dear Ravi, Your date of joining is 1 December 2026. Please report for joining on that day."
BGV = ("Dear Ravi, Congratulations on your acceptance of a position at Acme! "
       "We will be overseeing the background verification process.")
WELCOME = "Welcome! Here are your Workday login credentials. Countdown: 1 day to go!"


def test_joining_confirmed_needs_its_mail_to_say_so():
    assert store.verified_status("joining_confirmed", "Joining Confirmed", ["Joining details", JOINING]) == ("Joining Confirmed", "stated")
    assert store.verified_status("joining_confirmed", "Joining Confirmed", ["BGV", BGV]) == ("Background Verification", "joining not stated")
    assert store.verified_status("joining_confirmed", "Joining Confirmed", ["Welcome!", WELCOME]) == ("Onboarding", "joining not stated")


def test_other_statuses_are_shown_as_classified():
    assert store.verified_status("offer_received", "Offer Received", [WELCOME]) == ("Offer Received", "classified")
