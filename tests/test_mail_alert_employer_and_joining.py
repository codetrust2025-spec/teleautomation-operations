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
    assert store.verified_status("joining_confirmed", "Joining Confirmed", ["BGV", BGV]) == ("Background Verification", "different step")
    assert store.verified_status("joining_confirmed", "Joining Confirmed", ["Welcome!", WELCOME]) == ("Needs Review", "not stated")


def test_a_withdrawal_is_not_an_offer_declined():
    # Production wording (Accenture, 6 Nov 2026), name invented.
    text = ("Hi Ravi, We regret to know that you are no longer interested in pursuing your application for "
            "Data Engineer with Acme. Please note: If there was any offer released to you, it stands withdrawn.")
    assert store.verified_status("offer_declined", "Offer Declined", ["Update on your Application", text]) == ("Application Withdrawn", "not a selection")


def test_an_extended_offer_is_an_offer():
    text = "Dear Ravi, Congratulations! We are delighted to extend an offer of employment to you."
    assert store.verified_status("offer_received", "Offer Received", ["Acme offer has been released", text]) == ("Offer Received", "stated")


def test_an_unproven_selection_label_needs_review():
    assert store.verified_status("job_selection_confirmed", "Selected", ["A Task Awaits You: Review Documents", "Log in to Workday."]) == ("Needs Review", "not stated")


def test_interview_statuses_are_shown_as_classified():
    assert store.verified_status("interview_confirmed", "Interview Confirmed", [WELCOME]) == ("Interview Confirmed", "classified")
