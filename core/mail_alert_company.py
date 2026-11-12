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
4. forwarded sender -- a forwarded mail's original sender ("From: ...
               <hr@hcltech.com>" in the body) is an employer's own domain, as in 3;
5. job board relay -- a Naukri recruiter relay address carries the
               recruiter's own domain (base64 in the local part); the sender's
               display name is that domain's company, or the domain is as in 3;
6. subject / email body -- the mail states the company ("Interview with
               Brillio", "HCLTech | Interview Schedule", "on behalf of ...",
               "... job at Accellor"), and that name is corroborated: it is the
               company of a domain the mail itself carries (sender, forwarded
               sender, relay, or a link or address in the body), an employer
               the data already knows, or the subject and the body both state it;
7. sender name -- the sender's display name is, whole, an employer the data
               already knows ("Fractal" via myworkday.com).

Every candidate name passes `clean_company_name`, which rejects placeholders,
meeting titles and the candidate's own name. Nothing is guessed from a
platform's or an agency's domain. Nothing here writes: the screen shows the
result, and `scripts/backfill_alert_companies.py` stores it, with its source,
through this same resolver.
"""
from __future__ import annotations

import base64
import binascii
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
    "workablemail.com", "karat.io", "risebird.io", "zohocalendar.com", "ceipalmail.com", "ripplehire.com",
    "zeko.ai",
})
# A domain as it appears in a mail: "hr@hcltech.com", "https://www.accellor.com/careers".
_DOMAIN = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}"
_BODY_DOMAINS = re.compile(r"(?:@|https?://(?:www\.)?|\bwww\.)(" + _DOMAIN + r")", re.I)
# The original sender of a forwarded mail: "From: Diksha Kumari < diksha-kumari@hcltech.com >".
_FORWARDED_FROM = re.compile(
    r"(?:-{3,}\s*Forwarded message\s*-{3,}|-{3,}\s*Original Message\s*-{3,}|^\s*From:)[\s\S]{0,200}?"
    r"From:[^\n<]{0,80}<\s*[^\s<>@]+@(" + _DOMAIN + r")\s*>", re.I | re.M)
_RELAY_DOMAINS = ("naukri.com",)
_TLD_LABELS = {"com", "co", "in", "net", "org", "io", "ai", "au", "uk", "us", "biz", "info", "tech", "jobs", "mail", "email"}

_PREFIX = r"(?:(?:re|fw|fwd|updated|re-?scheduled|rescheduled|canceled|cancelled|invitation|reminder|accepted|new event)\s*:\s*)*"
# Phrasings that name the employer and nobody else: a person is never "the job
# at X" or "greetings from X". A name that one of these subject phrasings and
# one of these body phrasings both state is the employer, even when nothing
# else knows it ("... job at Accellor" + "Greetings from Accellor!").
_SUBJECT_EMPLOYER = (
    # ATS templates: "Interview for <role> job at Accellor", "Interview Scheduled for <role> at EY"
    re.compile(r"\b(?i:job|role|position|opening)\s+(?i:at)\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)\s*$"),
    re.compile(r"\b(?i:interview\s+scheduled\s+for)\b.+\s(?i:at)\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)\s*$"),
)
_BODY_EMPLOYER = (
    # "Your interview for the post of <role> at Infocepts is cancelled"
    re.compile(r"\bat\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)\s+is\s+(?:cancell?ed|scheduled|confirmed|rescheduled)\b"),
    re.compile(r"\b(?i:greetings)\s+from\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)(?=\s*[!,.;\n])"),
    # FloCareer: "the opportunity '<role>' for 'EY' is making progress"
    re.compile(r"\bopportunity\s+'[^'\n]{1,120}'\s+for\s+'(?P<c>[A-Z][^'\n]{0,40})'"),
)
_SUBJECT_PATTERNS = (
    # "Interview Scheduled with Hexaware Technologies", "Interview with Brillio for ..."
    re.compile(r"\b(?i:interview|discussion|meeting|call|opportunity|scheduled)\s+(?i:with)\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=\s+(?i:for|on|at)\b|\s*(?:\|\||\||[-–—:(@])|\s*$)"),
    # "HCLTech | Interview Schedule", "Capgemini Group - Interview cancellation", "NTT DATA : Teams Meeting"
    re.compile(r"^" + _PREFIX + r"(?P<c>[A-Za-z][\w&.,'’ ]{1,60}?)\s*(?:\|\||\||\s[-–—]\s|[-–—]|:)\s*(?=.*\b(?:interview|discussion|round|screening|level|l[1-4])\b)", re.I),
    # "... - Fullstack Developer - Accionlabs", "...-Birlasoft"
    re.compile(r"(?:\|\||\||[-–—])\s*(?P<c>[A-Za-z][\w&.'’ ]{1,40}?)\s*$"),
    *_SUBJECT_EMPLOYER,
    # "Mphasis India Interview - ...", "KPMG L1 Interview: ...", "Your Infosys Interview for ..."
    re.compile(r"^(?i:" + _PREFIX + r")(?P<c>[A-Z][\w&.'’]*(?:\s+[A-Z][\w&.'’]*){0,3}?)\s+(?:(?i:india|l[1-4]|r[1-4])\s+)*(?i:interview)\b"),
    re.compile(r"\b(?i:your)\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)\s+(?i:interview)\b"),
)
# A subject's segments ("Interview || Accenture || 6/11/2026"): one counts only
# when it is, whole, an employer the data already knows.
_SEGMENTS = re.compile(r"\|\||\||\s[-–—]\s|[_:]")
_BODY_PATTERNS = (
    re.compile(r"\bon behalf of\s+(?P<c>[A-Z][\w&.,'’ ]{1,60}?)(?=[,.;\n]|\s+(?:for|we|is|to)\b)"),
    re.compile(r"\b(?:interview|discussion)\s+with\s+[“\"']?(?P<c>[A-Z][\w&.,'’ ]{1,60}?)[”\"']?(?=[,.;\n]|\s+(?:for|on|at|is)\b)"),
    *_BODY_EMPLOYER,
    # Naukri walk-in reminder: "... Interview in Hyderabad on 31st Oct Capgemini Date & time"
    re.compile(r"\bon\s+\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}\s+(?P<c>[A-Z][\w&.'’ ]{1,40}?)\s+Date\s*&\s*time\b"),
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


def relay_domain(sender: str | None) -> str:
    """The recruiter's own domain a job-board relay address carries, or "".

    Naukri relays a recruiter's mail as `<name>.<base64 of their domain>@naukri.com`
    (vijay.c3RyYXRvc3BoZXJlLmNvLmlu@naukri.com -> stratosphere.co.in).
    """
    sender = str(sender or "").strip()
    domain = sender_domain(sender)
    if not any(domain == d or domain.endswith("." + d) for d in _RELAY_DOMAINS):
        return ""
    token = sender.rsplit("@", 1)[0].rsplit(".", 1)[-1]
    # Some relay addresses put one marker character before the base64
    # (vijay.sc3RyYXRv...); a decoding counts only when it is a whole domain.
    for candidate in (token, token[1:]):
        if len(candidate) < 8:
            continue
        try:
            decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True).decode("ascii").strip().lower()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if re.fullmatch(_DOMAIN, decoded) and not is_platform_domain(decoded):
            return decoded
    return ""


def forwarded_domains(body: str) -> list[str]:
    """The original senders' domains of a forwarded mail, platform domains left out."""
    found: list[str] = []
    for match in _FORWARDED_FROM.finditer(body or ""):
        domain = match.group(1).lower()
        if not is_platform_domain(domain) and domain not in found:
            found.append(domain)
    return found


def body_domains(body: str) -> list[str]:
    """Every non-platform domain the mail's text carries (addresses and links)."""
    found: list[str] = []
    for match in _BODY_DOMAINS.finditer(body or ""):
        domain = match.group(1).lower()
        if not is_platform_domain(domain) and domain not in found:
            found.append(domain)
    return found


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

    def spelled(self, name: str) -> str:
        """The known employer this name is, as the data spells it -- or as the
        mail does, when the data has it only in lower case ("kpmg")."""
        known = self.known.get(company_key(name), "")
        return name if known and known.islower() and not name.islower() else known

    def corroborated(self, name: str, domains: Iterable[str]) -> str:
        """A stated name, kept when a domain the mail carries or a known employer confirms it."""
        if any(name_matches_domain(name, d) for d in domains):
            return self.spelled(name) or name
        return self.spelled(name)


def resolve(row: dict[str, Any], evidence: Evidence, *, booking_company: str = "",
            thread_companies: Iterable[str] = (), body: str = "") -> tuple[str, str]:
    """(company, source) for one alert without a company, or ("", "") when nothing proves one."""
    people = tuple(p for p in (row.get("candidate_name"),) if p)
    domain = sender_domain(row.get("sender_email"))
    subject = row.get("email_subject") or ""
    body = body or ""
    name = _clean(booking_company, people)
    if name:
        return name, "booking"
    thread = {company_key(c): c for c in thread_companies if _clean(c, people)}
    if len(thread) == 1:
        return next(iter(thread.values())), "thread"
    name = evidence.domain_company(domain)
    if name:
        return name, "sender"
    forwarded = forwarded_domains(body)
    named = {company_key(n): n for n in (evidence.domain_company(d) for d in forwarded) if n}
    if len(named) == 1:
        return next(iter(named.values())), "forwarded sender"
    relay = relay_domain(row.get("sender_email"))
    display = _clean(row.get("sender_name") or "", people)
    if relay:
        name = evidence.domain_company(relay)
        if not name and display and name_matches_domain(display, relay):
            name = evidence.spelled(display) or display
        if name:
            return name, "job board relay"
    domains = [d for d in (domain, relay, *forwarded, *body_domains(body)) if d]
    agreed = ({company_key(n) for n in stated_companies(subject, _SUBJECT_EMPLOYER, people)}
              & {company_key(n) for n in stated_companies(body, _BODY_EMPLOYER, people)})
    for source, text, patterns in (("subject", subject, _SUBJECT_PATTERNS), ("email body", body, _BODY_PATTERNS)):
        confirmed = {}
        for stated in stated_companies(text, patterns, people):
            name = evidence.corroborated(stated, domains) or (stated if company_key(stated) in agreed else "")
            if name:
                confirmed[company_key(name)] = name
        if source == "subject" and not confirmed:
            for segment in _SEGMENTS.split(re.sub(r"^" + _PREFIX, "", text, flags=re.I)):
                name = _clean(segment, people)
                known = evidence.spelled(name) if name else ""
                if known:
                    confirmed[company_key(known)] = known
        if len(confirmed) == 1:
            return next(iter(confirmed.values())), source
    name = evidence.spelled(display) if display else ""
    if name:
        return name, "sender name"
    return "", ""
