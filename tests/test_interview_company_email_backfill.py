"""The email company backfill: what counts as corroboration, and that it never writes on its own.

Everything that decides is pure and pinned here. The container commands read Postgres in read-only transactions;
writes go through the screenshot backfill's guarded `apply`, which these plans feed unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from features.ollama_invite_extract import clean_company_name

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "interview_company_email_backfill.py"
spec = importlib.util.spec_from_file_location("interview_company_email_backfill", SCRIPT)
bfm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bfm)


class TestDomains:
    @pytest.mark.parametrize("address, domain", [
        ("ntt.s_h@mercedes-benz.com", "mercedes-benz.com"), ("Asha <asha@Example.CO.IN>", "example.co.in"), ("", ""), ("nobody", ""),
    ])
    def test_the_domain_of_an_address(self, address, domain):
        assert bfm.domain_of(address) == domain

    @pytest.mark.parametrize("domain, label", [
        ("mercedes-benz.com", "mercedes-benz"), ("primepathway.co.in", "primepathway"), ("fractal.ai", "fractal"),
        ("mail.example.co.uk", "example"), ("careers.infosys.com", "infosys"),
    ])
    def test_the_organisation_part_of_a_domain(self, domain, label):
        assert bfm.org_label(domain) == label

    @pytest.mark.parametrize("domain, employer", [
        ("infosys.com", True), ("mercedes-benz.com", True), ("gmail.com", False), ("googlemail.com", False),
        ("flocareer.com", False), ("mail.hirepro.in", False), ("kekamail.com", False), ("", False),
    ])
    def test_personal_mail_and_platforms_are_never_the_employer(self, domain, employer):
        assert bfm.is_employer_domain(domain) is employer

    @pytest.mark.parametrize("company, domain, supports", [
        ("Tiger Analytics", "tigeranalytics.com", True),
        ("DXC Technology", "dxc.com", True),
        ("Fractal Analytics", "fractal.ai", True),
        ("Mercedes-Benz Research and Development India", "mercedes-benz.com", True),
        ("Infosys", "infosys.com", True),
        ("T-Mobile", "protivitiglobal.in", False),        # a vendor's domain does not confirm its client
        ("Zemoso Labs", "flocareer.com", False),          # nor does a platform's
        ("Capgemini", "gmail.com", False),
        ("IT", "itc.com", False),                         # too short to mean anything
    ])
    def test_a_domain_supports_only_its_own_organisation(self, company, domain, supports):
        assert bfm.domain_supports(company, domain) is supports


class TestTheEmailText:
    def test_addresses_and_links_are_taken_out_so_no_company_is_read_off_them(self):
        text = bfm.email_text("Technical Discussion || Asha", "Write to ntt.s_h@mercedes-benz.com or see https://mercedes-benz.com/x and www.mercedes-benz.com", "")
        assert "mercedes" not in text.lower() and text.startswith("Subject: Technical Discussion")

    def test_the_plain_body_is_read_unless_the_html_holds_much_more(self):
        assert "Plain body" in bfm.email_text("S", "Plain body", "<p>Html body</p>")
        long_html = "<div>" + "Signature Infosys Limited " * 20 + "</div><style>.x{}</style>"
        text = bfm.email_text("S", "short", long_html)
        assert "Infosys Limited" in text and ".x{}" not in text

    def test_it_is_bounded(self):
        assert len(bfm.email_text("S", "x " * 10000, "")) <= bfm.MAX_EMAIL_CHARS

    @pytest.mark.parametrize("name, quote, ok", [
        ("Infosys", "Regards, Infosys Limited", True),
        ("Infosys", "Regards, INFOSYS  LIMITED", True),
        ("Infosys", "Regards, Wipro", False),          # the quote does not name it
        ("Infosys", "Infosys Limited, Pune", False),   # the quote is not in the email
        ("Infosys", "", False),
        ("AB", "AB", False),
    ])
    def test_a_quote_must_be_in_the_email_and_name_the_company(self, name, quote, ok):
        assert bfm.quoted_in(name, quote, "Hello Asha. Regards, Infosys Limited") is ok

    def test_known_companies_are_found_as_whole_words(self):
        text = "Your request was revoked. Interview with Bahwan Cybertek for TMO. Navikenz India Pvt Ltd"
        known = ["Evoke Technologies", "BahwanCybertek", "NAVIKENZ INDIA PRIVATE LIMITED", "Tenarai", "Bahwan Cybertek"]
        # "Evoke" is not in "revoked"; spaces and legal suffixes do not matter.
        assert bfm.companies_named_in(text, known) == ["BahwanCybertek", "NAVIKENZ INDIA PRIVATE LIMITED", "Bahwan Cybertek"]
        assert bfm.companies_named_in("NAVIKENZ INDIA PRIVATE LIMITED", ["NAVIKENZ INDIA PRIVATE LIMITED"]) == ["NAVIKENZ INDIA PRIVATE LIMITED"]
        assert bfm.companies_named_in("Evoke Technologies", ["Evoke"]) == ["Evoke"]


def asker(answer=None, *, error="", unavailable=False, node="jagadeesh"):
    calls = []

    def ask(model, b64, prompt, order):
        calls.append({"model": model, "b64": b64, "prompt": prompt, "order": order})
        if unavailable:
            return {"answer": None, "node": "", "error": "jagadeesh: unreachable", "unavailable": True, "tried": []}
        if error:
            return {"answer": None, "node": node, "error": error, "unavailable": False, "tried": []}
        return {"answer": answer, "node": node, "error": "", "unavailable": False, "tried": []}
    ask.calls = calls
    return ask


TEXT = "Subject: L1 Interview || Asha\n\nHello Asha, your interview with Infosys is at 3 PM. Regards, Ravi, Infosys Limited"


def read(answer=None, **kw):
    ask = asker(answer, **kw)
    return bfm.read_email(TEXT, model="qwen2.5:7b", order=["jagadeesh", "rtx4060"], ask=ask, clean=clean_company_name), ask


class TestTheRead:
    def test_a_quoted_employer_is_kept_and_only_text_is_sent(self):
        out, ask = read({"is_interview_invitation": True, "employer": "Infosys", "employer_quote": "Infosys Limited"})
        assert out["status"] == "ok" and out["company"] == "Infosys" and out["verified"] is True and out["node"] == "jagadeesh"
        assert ask.calls[0]["b64"] is None and TEXT in ask.calls[0]["prompt"]
        assert "Asha" not in json.dumps(out) and "Ravi" not in json.dumps(out)

    def test_an_employer_the_model_cannot_quote_from_the_email_is_not_verified(self):
        out, _ = read({"is_interview_invitation": True, "employer": "Mercedes-Benz", "employer_quote": "mercedes-benz.com"})
        assert out["company"] == "Mercedes-Benz" and out["verified"] is False

    def test_a_placeholder_or_platform_is_no_employer(self):
        for claim in ("Unknown company", "Microsoft Teams", "Not mentioned"):
            out, _ = read({"is_interview_invitation": True, "employer": claim, "employer_quote": claim})
            assert out["company"] == "" and out["verified"] is None

    def test_what_is_not_an_invitation_names_nobody(self):
        out, _ = read({"is_interview_invitation": False, "employer": "Quest Global", "employer_quote": "Quest Global"})
        assert out["status"] == "not_an_invite" and out["company"] == ""

    def test_an_outage_and_a_failure_are_told_apart(self):
        assert read(unavailable=True)[0]["status"] == "unavailable"
        assert read(error="no parseable answer")[0]["status"] == "failed"

    def test_the_prompt_says_what_an_employer_is_not(self):
        for words in ("never take it from an email address", "employer is the client", "Do not guess", "delivery failures"):
            assert words in bfm.EMAIL_PROMPT


def reading(company="", verified=True, status="ok", failures=0):
    return {"status": status, "company": company, "verified": verified if company else None, "failures": failures}


def row(*links, before=""):
    return {"before": before, "links": [{"key": key, "domains": list(domains)} for key, domains in links]}


def decide(r, primary, audit=None, **kw):
    return bfm.decide_row(r, primary, audit or {}, clean=lambda v: clean_company_name(v, people=("Asha Rao",)), **kw)


class TestTheDecision:
    def test_the_sender_domain_alone_is_left_for_review(self):
        """The 12 Oct case: a mercedes-benz.com sender, and no organisation named anywhere in the email."""
        out = decide(row(("m1", ["mercedes-benz.com"]), ("m2", ["gmail.com"])), {"m1": reading(), "m2": reading()})
        assert out["action"] == "review_sender_only" and out["after"] == "" and out["suggestion"] == "mercedes-benz.com"

    def test_named_in_the_email_by_its_own_domain_and_read_by_both_models_is_written(self):
        out = decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys Limited")}, {"m1": reading("Infosys")})
        assert out["action"] == "fill" and out["after"] == "Infosys Limited"
        assert out["basis"] == "email_text+second_model+sender_domain"

    def test_a_value_that_is_not_a_company_is_corrected_on_the_same_terms(self):
        out = decide(row(("m1", ["infosys.com"]), before="Contract"), {"m1": reading("Infosys")}, {"m1": reading("Infosys")})
        assert out["action"] == "correct_invalid" and out["after"] == "Infosys"

    def test_a_vendor_naming_its_client_is_left_for_review(self):
        out = decide(row(("m1", ["protivitiglobal.in"])), {"m1": reading("T-Mobile")}, {"m1": reading("T-Mobile")})
        assert out["action"] == "review_named_in_email_only" and out["suggestion"] == "T-Mobile"

    def test_a_platform_email_confirmed_by_the_invite_screenshot_is_written(self):
        out = decide(row(("m1", ["flocareer.com"])), {"m1": reading("Zemoso Labs")}, {"m1": reading("Zemoso Labs")},
                     screenshot_names=["Zemoso Labs"])
        assert out["action"] == "fill" and out["basis"] == "email_text+second_model+screenshot"

    def test_a_screenshot_naming_another_company_stops_it(self):
        out = decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys")}, {"m1": reading("Infosys")},
                     screenshot_names=["Wipro"])
        assert out["action"] == "review_conflicts_with_screenshot"

    def test_the_second_model_must_name_the_same_company(self):
        assert decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys")}, {"m1": reading("Wipro")})["action"] == "review_models_disagree"
        assert decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys")}, {"m1": reading("")})["action"] == "review_second_model_not_confirmed"
        assert decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys")},
                      {"m1": reading("Infosys", verified=False)})["action"] == "review_second_model_not_confirmed"

    def test_an_unquoted_claim_is_no_claim(self):
        out = decide(row(("m1", ["infosys.com"])), {"m1": reading("Infosys", verified=False)})
        assert out["action"] == "review_sender_only"

    def test_two_emails_naming_different_companies_are_left_for_review(self):
        out = decide(row(("m1", ["infosys.com"]), ("m2", ["wipro.com"])),
                     {"m1": reading("Infosys"), "m2": reading("Wipro")}, {"m1": reading("Infosys"), "m2": reading("Wipro")})
        assert out["action"] == "review_emails_disagree"

    def test_an_email_that_also_names_another_known_company_is_left_for_review(self):
        out = decide(row(("m1", ["insightglobal.com"])), {"m1": reading("Insight Global")}, {"m1": reading("Insight Global")},
                     other_companies_in={"m1": ["Insight Global", "T-Mobile"]})
        assert out["action"] == "review_other_company_named"

    def test_the_candidates_own_name_is_never_a_company(self):
        out = decide(row(("m1", ["asharao.com"])), {"m1": reading("Asha Rao")}, {"m1": reading("Asha Rao")})
        assert out["action"] == "review_sender_only" and out["after"] == ""

    @pytest.mark.parametrize("primary, audit, action", [
        ({}, {}, "pending"),
        ({"m1": reading("Infosys")}, {}, "pending"),                                   # its second reading is still due
        ({"m1": reading(status="failed", failures=1)}, {}, "pending"),
        ({"m1": reading(status="failed", failures=3)}, {}, "unreadable"),
        ({"m1": reading(status="not_an_invite")}, {}, "not_an_invite"),
        ({"m1": reading()}, {}, "review_sender_only"),
    ])
    def test_what_is_not_yet_known_is_not_decided(self, primary, audit, action):
        assert decide(row(("m1", ["infosys.com"])), primary, audit)["action"] == action

    def test_personal_mail_alone_is_no_evidence_at_all(self):
        assert decide(row(("m1", ["gmail.com"])), {"m1": reading()})["action"] == "no_company_in_email"

    def test_a_record_without_email_is_not_eligible(self):
        assert decide(row(), {})["action"] == "no_email"


class TestThePlan:
    def test_every_decision_lands_in_exactly_one_bucket(self):
        actions = [a for actions in bfm.BUCKETS.values() for a in actions]
        assert len(actions) == len(set(actions))
        returned = set(re.findall(r'"(review_[a-z_]+|fill|correct_invalid|pending|unreadable|not_an_invite|no_email|no_company_in_email)"',
                                  SCRIPT.read_text(encoding="utf-8")))
        assert returned <= set(actions), returned - set(actions)
        assert set(bfm.REVIEW_REASONS) >= set(bfm.BUCKETS["review"])

    def test_the_writes_are_the_ones_the_guarded_apply_makes(self):
        assert tuple(bfm.BUCKETS["to_update"]) == tuple(bfm.core.ACTIONS_THAT_WRITE)

    def test_the_totals(self):
        plan = {"items": [{"action": a} for a in ("fill", "review_sender_only", "review_named_in_email_only", "no_company_in_email",
                                                  "not_an_invite", "unreadable", "pending", "no_email")]}
        assert bfm.summarise_plan(plan) == {"records_without_company": 8, "with_linked_email": 7, "scanned": 6, "to_update": 1,
                                            "skipped": 2, "review": 2, "failed": 1, "pending": 1, "not_eligible": 1}

    def test_minutes_of_the_times_bookings_and_analyses_use(self):
        assert bfm.minutes_of("15:00") == bfm.minutes_of("03:00 PM") == 900
        assert bfm.minutes_of("") is None and bfm.minutes_of("soon") is None


def test_the_tool_never_writes_and_reads_the_database_read_only():
    body = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("_patch_row_fields", "_save(", "update_candidate", "INSERT ", "UPDATE ", "DELETE ", "os.remove", "shutil."):
        assert forbidden not in body, forbidden
    connections = body.count("with get_connection() as conn, conn.cursor() as cur:")
    assert connections == 4 and len(re.findall(r"\n\s+_read_only\(cur\)\n", body)) == connections
