"""The company an Interview Related alert is with, when the alert itself names none.

Some alerts were stored without a company (the model left it empty), so the
page showed "Company not identified". This module names the company only from
evidence the alert already has, in this order, and otherwise leaves it empty:

1. booking  -- the booking the alert made records the company
               (`interview_company`, as Daily Ops shows it);
2. thread   -- another alert in the same Gmail thread names exactly one company;
3. sender   -- the sender's own domain: every named alert from that domain
               names one employer, and that employer's name is the domain's
               (hcltech.com -> HCLTech). Shared mail, ATS and interview-platform
               domains never count;
4. subject / email body -- the mail states the company ("Interview with
               Brillio", "HCLTech | Interview Schedule", "on behalf of ..."),
               and that name is corroborated: it is the sender domain's
               company, or an employer the data already knows.

Every candidate name passes `clean_company_name`, which rejects placeholders,
meeting titles and the candidate's own name. Nothing is guessed from a
platform's or an agency's domain, and nothing is written: the stored rows are
untouched, this only decides what the screen shows.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from features.ollama_invite_extract import clean_company_name

# Mail, applicant-tracking, scheduling and interview-platform domains: they
# carry mail for many employers, so their domain names nobody.
PLATFORM_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.co.in",
    "icloud.com", "rediffmail.com", "myworkday.com", "successfactors.com", "adobesign.com", "docusign.net",
    "hirepro.in", "candidates.workablemail.com", "ashbyhq.com", "kekamail.com", "naukri.com",
    "messages.naukri.com", "linkedin.com", "indeed.com", "greenhouse.io", "lever.co", "icims.com",
    "smartrecruiters.com", "glider.ai", "veriklick.com", "flocareer.com", "barraiser.com", "busybots.in",
    "calendly.com", "zoom.us", "google.com", "microsoft.com", "teams.microsoft.com", "hackerrank.com",
    "hackerearth.com", "mettl.com", "imocha.io", "interviewvector.com", "intervue.io", "zohorecruit.com",
    "zohorecruit.in", "freshteam.com", "keka.com", "darwinbox.in", "darwinbox.com", "superset.com",
})
_TLD_LABELS = {"com", "co", "in", "net", "org", "io", "ai", "au", "uk", "us", "biz", "info", "tech", "jobs", "mail", "email"}

_PREFIX = r"(?:(?:re|fw|fwd|updated|re-?scheduled|rescheduled|canceled|cancelled|invitation|reminder|accepted|new event)\s*:\s*)*"
_SUBJECT_PATTERNS = (
    # "Interview Scheduled with Hexaware Technologies", "Interview with Brillio for ..."
    re.compile(r"\b(?i:interview|discussion|meeting|call|opportunity|scheduled)\s+(?i:with)\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=\s+(?i:for|on|at)\b|\s*(?:\|\||\||[-–—:(@])|\s*$)"),
    # "HCLTech | Interview Schedule", "Capgemini Group - Interview cancellation", "NTT DATA : Teams Meeting"
    re.compile(r"^" + _PREFIX + r"(?P<c>[A-Za-z][\w&.,'’ ]{1,60}?)\s*(?:\|\||\||\s[-–—]\s|[-–—]|:)\s*(?=.*\b(?:interview|discussion|round|screening|level|l[1-4])\b)", re.I),
    # "... - Fullstack Developer - Accionlabs", "...-Birlasoft"
    re.compile(r"(?:\|\||\||[-–—])\s*(?P<c>[A-Za-z][\w&.'’ ]{1,40}?)\s*$"),
)
# A subject's segments ("Interview || Accenture || 6/11/2026"): one counts only
# when it is, whole, an employer the data already knows.
_SEGMENTS = re.compile(r"\|\||\||\s[-–—]\s|[_:]")
_BODY_PATTERNS = (
    re.compile(r"\bon behalf of\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=[,.;\n]|\s+(?:for|we|is|to)\b)"),
    re.compile(r"\b(?:interview|discussion)\s+with\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=[,.;\n]|\s+(?:for|on|at|is)\b)"),
    re.compile(r"\b(?:position|role|opening|opportunity)\s+(?:at|with)\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=[,.;\n]|\s+(?:for|on|in|is)\b)"),
)


def company_key(value: str | None) -> str:
    """Case, punctuation and trailing legal suffixes ignored, as Mail Alerts groups companies."""
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split()
    while words and words[-1] in {"pvt", "private", "ltd", "limited", "llp", "inc", "incorporated", "co", "corp", "corporation", "india"}:
        words.pop()
    return " ".join(words)


def sender_domain(sender: str | None) -> str:
    return str(sender or "").rsplit("@", 1)[-1].strip().lower() if "@" in str(sender or "") else ""


def _domain_labels(domain: str) -> list[str]:
    return [label for label in domain.split(".") if label and label not in _TLD_LABELS]


def is_platform_domain(domain: str) -> bool:
    return not domain or any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)


def name_matches_domain(name: str, domain: str) -> bool:
    """The company's name is the domain's: "HCLTech"/hcltech.com, "Capgemini Group"/jobs.capgemini.com,
    "NTT DATA"/nttdata.com, "Aspire System"/aspiresys.com. Never for a platform domain."""
    if is_platform_domain(domain):
        return False
    words = company_key(name).split()
    if not words:
        return False
    compact = "".join(words)
    for label in _domain_labels(domain):
        label = re.sub(r"[^a-z0-9]", "", label)
        if len(label) < 3:
            continue
        if len(words[0]) >= 3 and words[0] in label:
            return True
        if len(label) >= 4 and (compact.startswith(label) or label.startswith(compact)):
            return True
    return False


def _clean(value: str, people: tuple[str, ...]) -> str:
    return clean_company_name((value or "").strip(" .,'’-"), people=people)


def stated_companies(text: str, patterns: Iterable[re.Pattern], people: tuple[str, ...]) -> list[str]:
    found = []
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            name = _clean(match.group("c"), people)
            if name and name not in found:
                found.append(name)
    return found


class Evidence:
    """What the data already knows: employers by key, and each sender domain's named companies."""

    def __init__(self, named_rows: Iterable[tuple[str | None, str | None]], booking_companies: Iterable[str] = ()):
        self.known: dict[str, str] = {}
        counts: dict[str, Counter] = defaultdict(Counter)
        names: Counter = Counter()
        self.by_domain: dict[str, dict[str, str]] = defaultdict(dict)
        for sender, company in named_rows:
            company = " ".join(str(company or "").split())
            key = company_key(company)
            if not key:
                continue
            names[(key, company)] += 1
            domain = sender_domain(sender)
            if domain:
                counts[domain][key] += 1
                self.by_domain[domain].setdefault(key, company)
        for company in booking_companies:
            company = " ".join(str(company or "").split())
            key = company_key(company)
            if key:
                names[(key, company)] += 1
        for (key, company), _n in names.most_common():
            self.known.setdefault(key, company)

    def domain_company(self, domain: str) -> str:
        """The one employer a sender domain names, when that employer's name is the domain's."""
        if is_platform_domain(domain):
            return ""
        named = self.by_domain.get(domain) or {}
        if not named:
            return ""
        keys = list(named)
        # Variants of one name ("HCLTech", "HCL Technologies Limited") are one
        # employer only when each matches the domain; otherwise it is ambiguous.
        matching = [k for k in keys if name_matches_domain(named[k], domain)]
        if not matching or len(matching) != len(keys):
            return ""
        best = max(matching, key=lambda k: (k in self.known, -len(k)))
        return self.known.get(best) or named[best]

    def corroborated(self, name: str, domain: str) -> str:
        """A stated name, kept when the sender's domain or a known employer confirms it."""
        if name_matches_domain(name, domain):
            return self.known.get(company_key(name)) or name
        return self.known.get(company_key(name), "")


def resolve(row: dict[str, Any], evidence: Evidence, *, booking_company: str = "",
            thread_companies: Iterable[str] = (), body: str = "") -> tuple[str, str]:
    """(company, source) for one alert without a company, or ("", "") when nothing proves one."""
    people = tuple(p for p in (row.get("candidate_name"),) if p)
    domain = sender_domain(row.get("sender_email"))
    name = _clean(booking_company, people)
    if name:
        return name, "booking"
    thread = {company_key(c): c for c in thread_companies if _clean(c, people)}
    if len(thread) == 1:
        return next(iter(thread.values())), "thread"
    name = evidence.domain_company(domain)
    if name:
        return name, "sender"
    for source, text, patterns in (("subject", row.get("email_subject") or "", _SUBJECT_PATTERNS),
                                   ("email body", body or "", _BODY_PATTERNS)):
        confirmed = {company_key(n): n for n in (evidence.corroborated(s, domain) for s in stated_companies(text, patterns, people)) if n}
        if source == "subject" and not confirmed:
            for segment in _SEGMENTS.split(re.sub(r"^" + _PREFIX, "", text, flags=re.I)):
                name = _clean(segment, people)
                known = evidence.known.get(company_key(name)) if name else ""
                if known:
                    confirmed[company_key(known)] = known
        if len(confirmed) == 1:
            return next(iter(confirmed.values())), source
    return "", ""
