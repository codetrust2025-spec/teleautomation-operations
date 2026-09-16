"""Deterministic safety checks for recruitment lifecycle classifications.

The model supplies semantic intent, but high-impact lifecycle events are only
accepted after these context checks reject questionnaires, advertisements,
historical employment documents, questions, and other non-outcomes.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any


EMAIL_INTENTS = {
    "JOB_ADVERTISEMENT", "JOB_REQUIREMENT", "RECRUITER_QUESTIONNAIRE",
    "CANDIDATE_DETAILS_REQUEST", "JOB_APPLICATION_UPDATE",
    "INTERVIEW_INVITATION", "INTERVIEW_CONFIRMATION", "INTERVIEW_RESCHEDULE",
    "INTERVIEW_CANCELLATION", "SELECTION_CONFIRMATION", "OFFER_LETTER",
    "OFFER_ACCEPTANCE", "JOINING_CONFIRMATION",
    "ACTUAL_JOINING_CONFIRMATION", "REJECTION", "DOCUMENT_SUBMISSION",
    "EMPLOYMENT_DOCUMENT", "GENERAL", "UNKNOWN",
    "FINAL_ROUND_CLEARED", "HR_CONFIRMATION", "DOCUMENT_VERIFICATION",
    "BACKGROUND_VERIFICATION", "COMPENSATION_CONFIRMATION",
}

DOCUMENT_TYPES = {
    "NONE", "PAYSLIP", "OFFER_LETTER", "APPOINTMENT_LETTER",
    "JOINING_LETTER", "EXPERIENCE_LETTER", "RELIEVING_LETTER",
    "EMPLOYMENT_VERIFICATION", "BACKGROUND_VERIFICATION_DOCUMENT",
    "RESUME", "BANK_STATEMENT", "ID_DOCUMENT", "EDUCATION_DOCUMENT",
    "CANDIDATE_FORM", "INTERVIEW_INVITATION_DOCUMENT", "OTHER",
}

LIFECYCLE_EVENTS = {
    "NONE", "SELECTED", "FINAL_SELECTION_CONFIRMED", "FINAL_ROUND_CLEARED",
    "OFFER_INDICATION", "OFFER_IN_PROGRESS", "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED", "APPOINTMENT_LETTER_RECEIVED", "OFFER_ACCEPTED",
    "OFFER_RECEIVED", "JOINING_CONFIRMED", "JOINED", "POST_SELECTION_ONBOARDING",
    "BACKGROUND_VERIFICATION", "DOCUMENT_VERIFICATION", "HR_CONFIRMATION",
    "COMPENSATION_CONFIRMATION", "OFFER_DECLINED", "OFFER_REVOKED",
    "JOINING_DATE_UPDATED", "INTERVIEW_SHORTLISTED", "INTERVIEW_UPDATE",
    "CANDIDATE_REJECTED",
}

INTERVIEW_EVENTS = {
    "NONE", "INTERVIEW_CONFIRMED", "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
}

# A closed set of positive assertions that can entail a tracked transition.
# These are not routing or negative-keyword rules: they are the backend proof
# obligations for a status the models have already proposed.  A public event's
# date, duration, meeting link, greeting, or sender domain satisfies none of
# these obligations by itself.
_TRANSITION_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "SELECTED": (
        r"\b(?:you (?:have been|are)|your candidature has been) selected\b",
        r"\bselected for (?:the )?(?:role|position|post)\b",
        r"\bcongratulations.{0,50}\bselected\b",
    ),
    "FINAL_SELECTION_CONFIRMED": (
        r"\bfinal selection (?:is |has been )?confirmed\b",
        r"\bselection has been confirmed\b",
        r"\bfinally selected\b",
    ),
    "FINAL_ROUND_CLEARED": (
        r"\b(?:you (?:have )?|successfully )?cleared (?:the )?(?:final|technical|hr|l[1-5]|all) (?:interview )?rounds?\b",
        r"\bfinal round cleared\b",
    ),
    "OFFER_INDICATION": (
        r"\b(?:we (?:are|would be) (?:pleased|delighted)|we would like) to offer you\b",
        r"\bintent to offer\b",
        r"\bplanning to release your offer\b",
    ),
    "OFFER_IN_PROGRESS": (
        r"\b(?:your )?offer (?:is|is currently|has been) (?:being )?(?:processed|prepared)\b",
        r"\boffer (?:is )?under preparation\b",
    ),
    "OFFER_APPROVED": (r"\b(?:your )?offer (?:has been|is) approved\b",),
    "OFFER_LETTER_RECEIVED": (
        r"\b(?:your (?:official )?|official )?offer letter (?:is |has been )?(?:attached|released|enclosed)\b",
        r"\b(?:find|review).{0,35}\b(?:your |the )?(?:official )?offer letter\b",
        r"\byour (?:formal )?offer of employment\b",
    ),
    "OFFER_RECEIVED": (
        r"\b(?:we (?:are|would be) (?:pleased|delighted)|we would like) to offer you\b",
        r"\b(?:your (?:official )?|official )?offer letter (?:is |has been )?(?:attached|released|enclosed)\b",
        r"\byour (?:formal )?offer of employment\b",
    ),
    "APPOINTMENT_LETTER_RECEIVED": (
        r"\b(?:your )?appointment letter (?:is |has been )?(?:attached|released|enclosed)\b",
        r"\bletter of appointment\b",
    ),
    "OFFER_ACCEPTED": (
        r"\b(?:your )?offer acceptance (?:is |has been )?confirmed\b",
        r"\b(?:we have received|thank you for|thanks for).{0,35}\b(?:your )?(?:offer )?acceptance\b",
        r"\b(?:you have |you've )?accepted (?:our |the )?offer\b",
    ),
    "OFFER_DECLINED": (
        r"\b(?:you have |you've )?declined (?:our |the )?offer\b",
        r"\boffer has been declined\b",
    ),
    "OFFER_REVOKED": (
        r"\boffer (?:has been|is) (?:revoked|rescinded|withdrawn)\b",
        r"\boffer stands withdrawn\b",
    ),
    "JOINING_CONFIRMED": (
        r"\byour (?:date of joining|joining date) (?:is|will be|has been confirmed)\b",
        r"\bjoining date\s*:\s*[0-3]?\d[-/ ]?[a-z0-9]+[-/ ][0-9]{2,4}\b",
        r"\b(?:please join|report for joining) on\b",
        r"\bjoining (?:is )?confirmed\b",
    ),
    "JOINING_DATE_UPDATED": (
        r"\b(?:revised|updated|new) (?:date of joining|joining date)\b",
        r"\bjoining date has (?:changed|moved)\b",
    ),
    "JOINED": (
        r"\b(?:you have |you've )?(?:officially )?joined (?:the company|[a-z0-9 &.'-]+)\b",
        r"\bemployment (?:has )?commenced\b",
        r"\breported for (?:duty|joining)\b",
    ),
    "POST_SELECTION_ONBOARDING": (
        r"\b(?:you (?:are|have been) (?:invited|requested) to |please )?complete (?:the )?(?:pre[- ]?onboarding|pre[- ]?joining|onboarding) formalities\b",
        r"\b(?:your )?(?:employee |post-selection )?onboarding (?:has )?(?:started|begun)\b",
    ),
    "BACKGROUND_VERIFICATION": (
        r"\byou (?:have been|are) (?:invited|requested) to complete.{0,100}\b(?:background|digital employment (?:background )?|pre-employment) verification\b",
        r"\bplease (?:complete|initiate|submit).{0,100}\b(?:background verification|background check|bgv)\b",
        r"\byour (?:background verification|background check|bgv) (?:has |is )?(?:started|initiated|in progress)\b",
    ),
    "DOCUMENT_VERIFICATION": (
        r"\b(?:please|you (?:must|need to)) (?:upload|submit|provide|share).{0,100}\bdocuments?\b.{0,80}\b(?:for )?verification\b",
        r"\bdocuments? (?:are )?required for (?:candidate |employment |pre-employment )?verification\b",
        r"\byour documents? (?:are being|have been submitted for) verification\b",
    ),
    "HR_CONFIRMATION": (
        r"\b(?:selected|shortlisted).{0,100}\b(?:final )?hr (?:process|discussion|round)\b",
        r"\bpre-offer documents?\b.{0,120}\b(?:selection|final hr|offer)\b",
        r"\b(?:final hr|salary|ctc) discussion (?:is |has been )?(?:confirmed|completed)\b",
    ),
    "COMPENSATION_CONFIRMATION": (
        r"\b(?:your )?compensation (?:is |has been )?confirmed\b",
        r"\b(?:your )?(?:annual ctc|salary package) is\b",
    ),
    "INTERVIEW_UPDATE": (
        r"\b(?:your interview invitation|you (?:have been|are) invited (?:for|to) (?:an )?interview)\b",
        r"\b(?:update|next steps?) (?:on|for|regarding) your interview\b",
        r"\bshortlisted for (?:the )?(?:next |technical |hr )?interview\b",
    ),
    "INTERVIEW_SHORTLISTED": (
        r"\b(?:you (?:have been|are|got)|your (?:profile|candidature) (?:has been|is|got)) shortlisted.{0,100}\b(?:interview|next (?:stage|round)|hr discussion)\b",
        r"\bshortlisted for (?:the )?(?:next |technical |hr )?interview\b",
        r"\byou (?:have been|are) invited (?:for|to) (?:an )?interview\b",
    ),
    # An assessment invitation asks the candidate to sit a test, usually inside
    # a window rather than at an hour. It is recruitment activity about this
    # person, so it needs an assertion of its own: without one the model could
    # only express it as an interview, and the interview vocabulary below --
    # which requires the word "interview" -- rejected every such mail, forever.
    #
    # Each phrasing names the test and addresses this candidate. A job advert
    # that merely mentions an assessment round matches none of them.
    "ASSESSMENT_INVITED": (
        r"\b(?:complete|attempt|take|finish|start)\s+(?:the\s+|your\s+|this\s+)?(?:pending\s+|online\s+|below\s+)*"
        r"(?:assessment|coding\s+test|aptitude\s+test|technical\s+test|online\s+test)\b",
        r"\b(?:invitation|invited)\b[^.!?\n]{0,60}\b(?:assessment|coding\s+test|online\s+test)\b",
        r"\bassessment\s*(?:name|link|invite|invitation)\s*[:\-]",
        r"\byour\s+(?:online\s+)?(?:assessment|coding\s+test|online\s+test)\b[^.!?\n]{0,60}"
        r"\b(?:is\s+)?(?:scheduled|ready|available|pending|due|activated)\b",
        r"\b(?:assessment|coding\s+test|online\s+test)\b[^.!?\n]{0,40}\b(?:start\s*time|end\s*time|time\s*limit)\b",
    ),
    "INTERVIEW_CONFIRMED": (
        r"\byour (?:l[1-5] )?(?:technical |managerial |hr |virtual |client )?interview.{0,120}\b(?:is |has been )?(?:scheduled|confirmed|arranged|booked)\b",
        r"\b(?:scheduled|confirmed|arranged|booked).{0,120}\byour (?:l[1-5] )?(?:technical |managerial |hr |virtual |client )?interview\b",
        r"\binvitation.{0,100}\bl[1-5]\s+(?:discussion|round)\b",
        r"\bl[1-5]\s+(?:discussion|round)\b.{0,100}\b(?:candidate|interview for|discussion with)\b",
        # A reminder asserts the interview exists as firmly as the mail that
        # arranged it. Interview platforms send "Your ... Interview ... Is
        # Coming Up!" carrying the date and time and never write "scheduled",
        # so the vocabulary above matched nothing and every proposed status
        # failed as PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT.
        #
        # Still an assertion about *this* candidate's own interview: each of
        # these needs "your interview" or an explicit reminder framing, never
        # the bare word, so a job advert or a rejection cannot match.
        # The lookahead keeps this about the interview itself. Without it,
        # "your interview preparation guide is coming up" asserted a booking:
        # the subject of "is coming up" was the guide, not the interview.
        r"\byour (?:[\w.-]+ )?(?:l[1-5] )?(?:technical |managerial |hr |virtual )?interview\b(?!\s+(?:preparation|prep|guide|tips|advice|newsletter|checklist|process|questions?)\b).{0,160}\bis coming up\b",
        r"\breminder\b.{0,160}\byour (?:[\w.-]+ )?(?:l[1-5] )?(?:technical |managerial |hr |virtual )?interview\b(?!\s+(?:preparation|prep|guide|tips|advice|newsletter|checklist|process|questions?)\b)",
        r"\byour upcoming (?:[\w.-]+ )?(?:l[1-5] )?(?:technical |managerial |hr |virtual )?interview\b(?!\s+(?:preparation|prep|guide|tips|advice|newsletter|checklist|process|questions?)\b)",
    ),
    "INTERVIEW_RESCHEDULED": (
        r"\b(?:your |the )?interview.{0,100}\b(?:rescheduled|moved|postponed)\b",
        r"\b(?:rescheduled|moved|postponed).{0,100}\b(?:your |the )?interview\b",
    ),
    "INTERVIEW_CANCELLED": (
        r"\b(?:your |the )?interview.{0,80}\b(?:cancelled|canceled|called off)\b",
        r"\b(?:cancelled|canceled|called off).{0,80}\b(?:your |the )?interview\b",
    ),
    "CANDIDATE_REJECTED": (
        r"\b(?:your application|your candidature|you)\b.{0,100}\b(?:was unsuccessful|not selected|not moving forward|will not be moving forward)\b",
        r"\bregret to inform you.{0,160}\b(?:not selected|not moving forward|unsuccessful)\b",
    ),
}


def evidence_entails_transition(status: str, text: str) -> bool:
    """Return whether a source excerpt actually states the proposed transition."""
    proposed = str(status or "").upper()
    source = str(text or "")
    for pattern in _TRANSITION_ASSERTIONS.get(proposed, ()):
        for match in re.finditer(pattern, source, re.I | re.S):
            before = source[max(0, match.start() - 45):match.start()]
            if re.search(r"\b(?:not|never|no)\b[^.!?\n]{0,35}$", before, re.I):
                continue
            # The schedule assertion spans the interview and its predicate;
            # negation can be inside that span ("your client interview is not
            # scheduled"), not just before it.
            if proposed == "INTERVIEW_CONFIRMED" and re.search(
                r"\b(?:not|never|no)\b", match.group(), re.I,
            ):
                continue
            if proposed.startswith("OFFER_"):
                sentence = source[max(0, match.start() - 180):min(len(source), match.end() + 180)]
                if re.search(
                    r"\b(?:does not charge|never charges?|not (?:an |a )?(?:offer|commitment)|"
                    r"not be (?:assumed|treated)|fake|fraudulent)\b",
                    sentence,
                    re.I,
                ):
                    continue
            return True
    return False


def asserted_transitions(text: str) -> set[str]:
    """Extract only transitions backed by an explicit positive assertion."""
    return {
        status for status in _TRANSITION_ASSERTIONS
        if evidence_entails_transition(status, text)
    }

_QUESTIONNAIRE_FIELDS = (
    "full name", "contact no", "contact number", "email id", "date of birth",
    "dob", "total experience", "relevant exp", "current company",
    "notice period", "current ctc", "expected ctc", "offer in hand",
    "offered ctc", "date of joining", "company name", "10th", "12th",
    "graduation", "grades",
)
_QUESTION_PHRASES = (
    "date of joining?", "joining date?", "please provide your joining date",
    "please share your date of joining", "please confirm your date of joining",
    "when can you join", "offer in hand?", "do you have an offer",
    "do you currently have an offer", "current company?",
)
_JOB_AD_PATTERNS = (
    r"^\s*(?:\N{ENVELOPE}|job)\s*\|", r"\bjob description\b", r"\bapply now\b",
    # A vacancy, not the act of opening something. `\bopen(?:ing|ings)\b`
    # matched the gerund just as readily, and an AI-interview platform's own
    # anti-cheating wording -- "do not switch devices/browsers after opening",
    # "opening or switching to other applications is not permitted" -- read as
    # a job advertisement. That marked eleven genuine Zealogics interview
    # mails promotional, which makes validate_interview_event refuse the
    # transition with JOB_ADVERTISEMENT, and each was silently ignored.
    #
    # The vacancy sense is a plural, a qualifier in front, or one of the
    # separators an advert uses: "Opening for Automation Testing", "opening at
    # Aon", "Automation Testing Opening -Tech Mahindra", "Openings: ServiceNow".
    # Sentence punctuation is deliberately excluded, which is what keeps
    # "after opening." and "opening or switching" out.
    r"\bopenings\b",
    r"\b(?:job|current|new|urgent|immediate|multiple|several|various|hiring)\s+opening\b",
    r"\bopening\s*(?:[-–—:]|\b(?:for|at|in|with)\b)",
    r"\bwe are hiring\b", r"\bjob requirement\b",
)
_PAYSLIP_PATTERNS = (
    r"\bpayslip\b", r"\bsalary slip\b", r"pay slip for the month",
)
# Bulk job-portal marketing that mentions "recruiter(s)" or similar in
# passing but carries no candidate-specific outcome. Combined with a portal
# sender so a genuine recruiter's own email is never caught by this alone.
_JOB_PORTAL_NOTIFICATION_PATTERNS = (
    r"\bsent by recruiters?\b", r"\bnoticed by recruiters?\b",
    r"\bnew jobs? in your inbox\b", r"\bjob search saf(?:er|e|ety)\b",
    r"\bjob scams?\b", r"\bfraud jobs?\b",
    r"\bkeep your profile updated\b", r"\bprofile (?:viewed|visibility)\b",
    r"\b(?:you're|you are) now open to work\b", r"\bget noticed\b",
    r"\bshare their thoughts on linkedin\b", r"\bpeople you may know\b",
    r"\bgrow your network\b",
)
_NON_OUTCOME_RECRUITMENT_PATTERNS = (
    r"\bjob application (?:was )?successful\b",
    r"\bapplication (?:was )?(?:received|submitted|successful|under review)\b",
    r"\bthank you for (?:applying|your application)\b",
    r"\byou applied for \d+ jobs?\b",
    r"\b(?:you're|you are) now open to work\b",
    r"\b(?:help you )?get noticed\b",
    r"\bshare their thoughts on linkedin\b",
    r"\bpeople you may know\b",
    r"\bgrow your network\b",
    r"\badd [a-z][a-z .'-]{1,60} (?:as a )?contact\b",
)
# Bank/payment transaction alerts and OTP messages that occasionally land in
# a monitored candidate mailbox but are never recruitment-related. These
# often contain isolated words (e.g. "offer" inside an RBI fraud-warning
# footer) that would otherwise trip the ambiguous-recruitment fallback.
_TRANSACTIONAL_PATTERNS = (
    r"\bamount (?:debited|credited)\b", r"\bwas debited from your\b",
    r"\bwas credited to your\b", r"\bavailable balance\b",
    r"\btransaction (?:info|id|alert)\b", r"\bupi[/-]", r"\bblock upi\b",
    r"\bone[\s-]time password\b", r"\byour otp is\b", r"\bsavings account\b",
)
_SENSITIVE_PATTERNS = (
    (r"(?i)\b(?:bank\s*(?:a/?c|account)(?:\s*(?:no|number))?|account\s*(?:no|number))\s*[:#-]?\s*\d(?:[ -]?\d){5,}", "Bank account: [REDACTED]"),
    (r"(?i)\bPAN\s*(?:no|number)?\s*[:#-]?\s*[A-Z]{5}[0-9]{4}[A-Z]\b", "PAN: [REDACTED]"),
    (r"(?i)\b(?:Aadhaar|Aadhar)\s*(?:no|number)?\s*[:#-]?\s*(?:\d[ -]?){12}\b", "Aadhaar: [REDACTED]"),
    (r"(?i)\bUAN\s*(?:no|number)?\s*[:#-]?\s*\d{8,}\b", "UAN: [REDACTED]"),
    (r"(?i)\b(?:PF|EPF)\s*(?:no|number)?\s*[:#-]?\s*[A-Z0-9/-]{6,}\b", "PF: [REDACTED]"),
)


def redact_sensitive_text(value: str, *, limit: int = 500) -> str:
    """Return a short evidence excerpt with financial/government IDs removed."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text[:limit]


def classify_document(filename: str, text: str = "", declared_type: str = "") -> str:
    blob = f"{filename} {text[:12000]}".casefold()
    declared = str(declared_type or "").upper()
    if declared == "BACKGROUND_VERIFICATION_FORM":
        declared = "BACKGROUND_VERIFICATION_DOCUMENT"
    if declared == "OTHER_RECRUITMENT_DOCUMENT":
        declared = "OTHER"
    rules = (
        ("PAYSLIP", _PAYSLIP_PATTERNS),
        ("EXPERIENCE_LETTER", (r"experience\s+letter", r"certificate of experience")),
        ("RELIEVING_LETTER", (r"relieving\s+letter", r"relieved from")),
        ("BANK_STATEMENT", (r"bank\s+statement",)),
        ("RESUME", (r"\bresume\b", r"curriculum vitae", r"\bcv\b")),
        ("ID_DOCUMENT", (r"\baadhaar\b", r"\baadhar\b", r"\bpassport\b", r"\bpan card\b")),
        ("EDUCATION_DOCUMENT", (r"degree certificate", r"marksheet", r"transcript")),
        ("BACKGROUND_VERIFICATION_DOCUMENT", (r"background verification", r"\bbgv\b")),
        ("APPOINTMENT_LETTER", (r"appointment\s+letter", r"letter of appointment")),
        ("OFFER_LETTER", (r"offer\s+letter", r"offer of employment")),
        ("JOINING_LETTER", (r"joining\s+letter", r"joining confirmation")),
        ("EMPLOYMENT_VERIFICATION", (r"employment verification", r"employment certificate")),
        ("CANDIDATE_FORM", (r"candidate information form", r"candidate details form")),
        ("INTERVIEW_INVITATION_DOCUMENT", (r"interview invitation", r"interview schedule")),
    )
    for label, patterns in rules:
        if any(re.search(pattern, blob) for pattern in patterns):
            return label
    return declared if declared in DOCUMENT_TYPES else "OTHER"


def _questionnaire(text: str) -> bool:
    lowered = text.casefold()
    field_count = sum(1 for field in _QUESTIONNAIRE_FIELDS if field in lowered)
    colon_count = len(re.findall(r"(?im)^\s*[a-z][a-z /().-]{2,35}\s*[:?]", text))
    return field_count >= 4 or (field_count >= 3 and colon_count >= 3)


def _is_question(text: str) -> bool:
    lowered = text.casefold()
    return any(phrase in lowered for phrase in _QUESTION_PHRASES)


def _is_job_ad(subject: str, body: str, sender_email: str) -> bool:
    combined = f"{subject}\n{body[:8000]}".casefold()
    portal = any(token in sender_email.casefold() for token in ("naukri", "foundit", "monster", "indeed", "shine", "timesjobs"))
    # A job-description link is also part of a candidate-specific ATS invite.
    # Exempt only that phrase, and only with a concrete schedule and meeting
    # invitation. All other vacancy/portal/marketing guards remain in force.
    concrete_invitation = _is_assertive_interview_invitation(subject, body)
    ad_language = any(
        re.search(pattern, combined, re.I)
        for pattern in _JOB_AD_PATTERNS
        if not (pattern == r"\bjob description\b" and concrete_invitation)
    )
    many_requirements = sum(token in combined for token in ("experience", "skills", "location", "notice period", "ctc", "job description")) >= 3
    return ad_language or (portal and many_requirements) or _is_job_portal_notification(combined, sender_email)


def _is_job_portal_notification(combined: str, sender_email: str) -> bool:
    portal = any(token in sender_email.casefold() for token in ("naukri", "foundit", "monster", "indeed", "shine", "timesjobs", "linkedin"))
    return portal and any(re.search(pattern, combined, re.I) for pattern in _JOB_PORTAL_NOTIFICATION_PATTERNS)


def _is_non_outcome_recruitment_notice(subject: str, body: str) -> bool:
    combined = f"{subject}\n{body[:8000]}".casefold()
    return any(re.search(pattern, combined, re.I) for pattern in _NON_OUTCOME_RECRUITMENT_PATTERNS)


def _is_transactional_alert(subject: str, body: str) -> bool:
    combined = f"{subject}\n{body[:4000]}".casefold()
    return any(re.search(pattern, combined, re.I) for pattern in _TRANSACTIONAL_PATTERNS)


def _is_assertive_interview_invitation(subject: str, body: str) -> bool:
    """Recognize concrete interview invites without relying on one phrase.

    Calendar providers and recruiters often say "please join the virtual
    interview" instead of "your interview is scheduled".  Require a real
    schedule plus an invitation/meeting signal so generic interview content
    and preparation webinars do not become candidate events.
    """
    title = str(subject or "").casefold()
    direct = f"{subject}\n{body[:12000]}".casefold()
    # Recruiters frequently title calendar invites as "L1 Discussion" or
    # "L2 Round" without the word interview.  Treat that wording as an
    # interview signal only inside this schedule+invitation gate.
    interview = r"(?:virtual\s+)?interview|technical\s+round|managerial\s+round|hr\s+round|l[1-5]\s+(?:discussion|round)"
    if not re.search(rf"\b(?:{interview})\b", direct):
        return False
    if re.search(r"\b(?:webinar|workshop|training|preparation|tips|career fair|mock interview)\b", title):
        return False
    has_date = any(re.search(pattern, direct, re.I) for pattern in (
        r"\b(?:today|tomorrow)\b",
        r"\b[0-3]?\d(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s*,?\s*\d{2,4})?\b",
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+[0-3]?\d(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?\b",
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
        r"\b[0-3]?\d[-/][01]?\d(?:[-/]\d{2,4})?\b",
    ))
    has_time = bool(re.search(
        r"\b(?:(?:0?[1-9]|1[0-2])[:.]?[0-5]\d\s*(?:a\.?m\.?|p\.?m\.?)|(?:[01]?\d|2[0-3]):[0-5]\d)\b",
        direct,
        re.I,
    ))
    if not (has_date and has_time):
        return False
    invitation = bool(re.search(
        rf"(?:\bplease\s+join\b.{{0,100}}\b(?:{interview})\b|"
        rf"\b(?:delighted|pleased)\s+to\s+invite\s+you\b|"
        rf"\b(?:you are|you're|you have been)\s+invited\b.{{0,120}}\b(?:{interview})\b|"
        rf"\bjoin\b.{{0,60}}\b(?:{interview})\b|"
        rf"\b(?:{interview})\b.{{0,80}}\b(?:will be held|is set|is arranged|is booked)\b)",
        direct,
        re.I,
    ))
    calendar_round_invitation = bool(
        re.search(r"\binvitation from an unknown sender\b", title, re.I)
        and re.search(r"\bl[1-5]\s+(?:discussion|round)\b", title, re.I)
    )
    meeting_details = bool(re.search(
        r"\b(?:microsoft teams|teams meeting|google meet|zoom meeting|meeting id|passcode)\b|https?://(?:teams\.microsoft\.com|meet\.google\.com|[^\s/]*zoom\.us)/|\binterview meeting link\s*:\s*https://portal\.risebird\.io/candidate/interviewdetails\?",
        direct,
        re.I,
    ))
    subject_is_interview = bool(re.search(rf"\b(?:{interview})\b", title, re.I))
    # Recruiters also use generic subjects such as "JD and Invite". A positive
    # source assertion ("Your Client interview scheduled ...") plus this
    # function's date/time and meeting gates is stronger evidence than an
    # incidental job-description section. Reuse the negation-aware assertion
    # vocabulary; do not turn a JD, proposed time, or Teams link alone into an
    # interview. Public-event title and other advertising guards still apply.
    asserted_schedule = meeting_details and evidence_entails_transition("INTERVIEW_CONFIRMED", body)
    return invitation or calendar_round_invitation or (subject_is_interview and meeting_details) or asserted_schedule


def extract_interview_schedule(subject: str, body: str, *, sent_at: Any = None) -> dict[str, Any]:
    """Conservatively extract schedule fields for outage-time visibility.

    These fields never authorize automatic booking; that still requires a
    validated AI result.  They let a strongly evidenced invite remain visible
    and actionable while the model service is unavailable.
    """
    direct = f"{subject}\n{body[:12000]}"
    day: date | None = None
    match = re.search(
        r"\b([0-3]?\d)(?:st|nd|rd|th)?\s+"
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\s*,?\s*['’]?\s*(\d{2,4})\b",
        direct,
        re.I,
    )
    if match:
        year = int(match.group(3)) + (2000 if len(match.group(3)) == 2 else 0)
        value = f"{match.group(1)} {match.group(2)} {year}"
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                day = datetime.strptime(value, fmt).date();break
            except ValueError:
                pass
    if day is None:
        match = re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+([0-3]?\d)(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b",
            direct,
            re.I,
        )
        if match:
            value = f"{match.group(2)} {match.group(1)} {match.group(3)}"
            for fmt in ("%d %B %Y", "%d %b %Y"):
                try:
                    day = datetime.strptime(value, fmt).date();break
                except ValueError:
                    pass
    if day is None:
        match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", direct)
        if match:
            try:day = date(int(match.group(1)),int(match.group(2)),int(match.group(3)))
            except ValueError:pass
    if day is None:
        # Recruitment mails received by this India-based service commonly use
        # day-first numeric dates. Four-digit years keep this conservative and
        # avoid silently interpreting ambiguous short dates.
        match = re.search(r"\b([0-3]?\d)[-/]([01]?\d)[-/](\d{4})\b", direct)
        if match:
            try:day = date(int(match.group(3)),int(match.group(2)),int(match.group(1)))
            except ValueError:pass
    if day is None:
        relative = re.search(r"\b(today|tomorrow)\b", direct, re.I)
        if relative:
            day = _event_date(sent_at) + timedelta(days=1 if relative.group(1).casefold()=="tomorrow" else 0)

    def normalized_12h(hour: str, minute: str, meridiem: str) -> str:
        suffix = "AM" if meridiem.casefold().startswith("a") else "PM"
        return f"{int(hour):02d}:{minute} {suffix}"

    time_value: str | None = None
    end_time_value: str | None = None
    duration_minutes: int | None = None
    range_match = re.search(
        r"\b(0?[1-9]|1[0-2])[:.]([0-5]\d)\s*(a\.?m\.?|p\.?m\.?)"
        r"\s*(?:-|–|—|to)\s*"
        r"(0?[1-9]|1[0-2])[:.]([0-5]\d)\s*(a\.?m\.?|p\.?m\.?)\b",
        direct,
        re.I,
    )
    if range_match:
        time_value = normalized_12h(
            range_match.group(1), range_match.group(2), range_match.group(3),
        )
        end_time_value = normalized_12h(
            range_match.group(4), range_match.group(5), range_match.group(6),
        )
        start_clock = datetime.strptime(time_value, "%I:%M %p")
        end_clock = datetime.strptime(end_time_value, "%I:%M %p")
        delta = int((end_clock - start_clock).total_seconds() // 60)
        if 5 <= delta <= 12 * 60:
            duration_minutes = delta
        else:
            end_time_value = None
    if time_value is None:
        match = re.search(r"\b(0?[1-9]|1[0-2])[:.]([0-5]\d)\s*(a\.?m\.?|p\.?m\.?)\b", direct, re.I)
        if match:
            time_value = normalized_12h(match.group(1), match.group(2), match.group(3))
    if time_value is None:
        match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", direct)
        if match:
            parsed = datetime.strptime(f"{int(match.group(1)):02d}:{match.group(2)}", "%H:%M")
            time_value = parsed.strftime("%I:%M %p")

    link_match = re.search(r"https?://(?:teams\.microsoft\.com|meet\.google\.com|[^\s/]*zoom\.us)/[^\s<>]+", direct, re.I)
    lowered = direct.casefold()
    mode = (
        "Microsoft Teams" if "microsoft teams" in lowered or "teams.microsoft.com" in lowered
        else "Google Meet" if "google meet" in lowered or "meet.google.com" in lowered
        else "Zoom" if "zoom" in lowered
        else "Online" if link_match else None
    )
    timezone_name = "Asia/Kolkata" if re.search(r"\bIST\b|GMT\s*\+\s*5(?::30)?", direct, re.I) else None
    return {
        "date": day.isoformat() if day else None,
        "time": time_value,
        "end_time": end_time_value,
        "duration_minutes": duration_minutes,
        "timezone": timezone_name,
        "mode": mode,
        "round": None,
        "location": None,
        "meeting_link": link_match.group(0).rstrip(".,);]") if link_match else None,
    }


def _event_date(sent_at: Any) -> date:
    if isinstance(sent_at, datetime):
        return sent_at.date()
    if isinstance(sent_at, date):
        return sent_at
    try:
        return datetime.fromisoformat(str(sent_at or "").replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()


def classify_context(
    subject: str,
    body: str,
    *,
    sender_email: str = "",
    sent_at: Any = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Classify message/document context without promoting candidate truth."""
    attachment_rows = attachments or []
    documents = [
        classify_document(
            str(item.get("filename") or ""), str(item.get("text") or ""),
            str(item.get("document_type") or item.get("attachment_type") or ""),
        )
        for item in attachment_rows
    ]
    document_type = next((item for item in documents if item != "OTHER"), "NONE" if not documents else "OTHER")
    direct = f"{subject}\n{body}"
    all_text = " ".join([direct] + [str(item.get("text") or "") for item in attachment_rows])
    assertions = asserted_transitions(all_text)
    questionnaire = _questionnaire(direct)
    question = _is_question(direct)
    job_ad = _is_job_ad(subject, body, sender_email)
    non_outcome_notice = _is_non_outcome_recruitment_notice(subject, body)
    transactional = _is_transactional_alert(subject, body)
    # A checklist that asks for prior payslips is not itself a payslip. Only
    # an extracted/declared attachment may establish historical document type.
    attachment_text = " ".join(str(item.get("text") or "") for item in attachment_rows)
    payslip = document_type == "PAYSLIP" or bool(attachment_rows) and any(
        re.search(pattern, attachment_text, re.I) for pattern in _PAYSLIP_PATTERNS
    )
    historical = payslip or document_type in {"EXPERIENCE_LETTER", "RELIEVING_LETTER", "EMPLOYMENT_VERIFICATION"}

    lowered = direct.casefold()

    # Fraud / disclaimer / marketing noise guard
    has_fraud_disclaimer = any(phrase in lowered for phrase in (
        "fake job offer", "beware of fake", "does not charge any fee", "never asks for fees",
        "never charges any fee", "never charges a fee", "does not charge fee", "no fees are charged",
        "fraudulent job", "caution against fraudulent", "recruitment disclaimer",
        "fraud alert", "job scam",
    ))
    is_visa_or_tender_spam = any(phrase in lowered for phrase in (
        "work permit abroad", "pr visa", "visa assistance", "government tender", "emd-free",
        "career gap", "experience letters from pvt", "introducing gemini",
    ))

    actual_joined = any(re.search(pattern, lowered) for pattern in (
        r"\bofficially joined\b", r"\bjoined (?:the company|[a-z0-9 &.-]+) today\b",
        r"\bemployment commenced\b", r"\bstarted (?:employment|working) (?:today|on)\b",
        r"\breported for (?:duty|joining)\b",
    ))
    joining_confirmed = any(re.search(pattern, lowered) for pattern in (
        r"\bjoining date (?:is|has been) confirmed\b", r"\byour (?:date of joining|joining date) (?:is|will be)\b",
        r"\bplease join on\b", r"\breport for joining on\b", r"\bjoining is confirmed\b", r"\bjoining confirmed\b",
        r"\bdigital employment\b", r"\bdigiverifier\b", r"\bbgv[_\s-]", r"\bbackground verification\b",
        r"\bloa accepetence\b", r"\bloa acceptance\b", r"\bwelcome aboard\b", r"\bwelcome to the organization\b",
        r"\bpost-selection onboarding\b", r"\bwelcome to kaivale technologies - bgv\b",
    )) and not is_visa_or_tender_spam
    offer_received = (any(phrase in lowered for phrase in (
        "we are pleased to offer you", "we are delighted to offer you", "pleased to offer you",
        "pleased to extend an offer", "pleased to extend our offer", "offer letter attached",
        "offer letter inside", "offer letter has been", "appointment letter attached",
        "letter of appointment", "formal offer of employment", "intent offer letter",
        "intent to offer", "offer released", "offer has been released",
        "offer has been successfully released", "congratulations – you have been selected",
        "interview result – selected", "extending an offer of employment",
    )) or any(re.search(pattern, lowered) for pattern in (
        r"\boffer letter\b", r"\bappointment letter\b", r"\bcongratulations, you're in!\b",
        r"\bpleased to extend (?:an|our) offer\b",
        r"\bformal (?:job )?offer\b",
    ))) and not has_fraud_disclaimer and not is_visa_or_tender_spam
    offer_accepted = any(phrase in lowered for phrase in (
        "we have received your acceptance", "your offer acceptance is confirmed",
        "accepted the offer", "offer has been accepted", "accepted your offer",
        "offer acceptance -",
    ))
    final_round_cleared = (any(phrase in lowered for phrase in (
        "cleared the final round", "cleared all rounds", "cleared the technical round",
        "successfully cleared the l1", "successfully cleared the l2",
        "cleared the l1 round", "cleared the l2 round",
        "cleared the l1", "cleared the l2", "cleared l1", "cleared l2", "final round cleared",
    )) or bool(re.search(r"\bsuccessfully cleared the (?:l[1-5]|technical|final|hr) round\b", lowered))) and not is_visa_or_tender_spam
    hr_confirmation = (any(phrase in lowered for phrase in (
        "minimal documents", "capgemini documenation", "capgemini documentation",
        "documents required for offer", "documents required - ey", "documents required for onboarding",
        "pre-offer documents", "pre-offer document", "uan number and updated cv",
        "post selection document", "ltimindtree selection process - pre-offer",
    )) or (
        any(phrase in lowered for phrase in ("salary discussion", "ctc discussion", "salary negotiation", "ctc breakdown", "hr discussion", "compensation discussion"))
        and any(token in lowered for token in ("selected", "selection", "offer", "shortlisted for offer", "cleared the round", "cleared l1", "cleared l2"))
    )) and not is_visa_or_tender_spam
    selected = (any(phrase in lowered for phrase in (
        "you have been selected", "you are selected", "selected for the role", "selected for the position",
        "selected for the post", "selection has been confirmed", "final selection confirmed",
        "congratulations on your selection", "selection confirmation", "shortlisted for offer",
        "shortlisted for the offer", "congratulations – you have been selected",
    )) or bool(re.search(r"\bcongratulations.{0,40}\bselected\b", lowered))) and not is_visa_or_tender_spam
    interview_cancelled = bool(re.search(
        r"(?:\binterview\b.{0,80}\b(?:cancelled|canceled|called off)\b|\b(?:cancelled|canceled|called off)\b.{0,80}\binterview\b)",
        lowered,
    ))
    # A slot the candidate did not attend. The booking it refers to is spent
    # whatever happens next, so it must not keep standing as an upcoming
    # interview -- and it must never be read as scheduling a new one.
    #
    # Pujitha's Persistent Systems interview was announced missed at 22:40 and
    # the confirmed slot stayed. Nothing here had vocabulary for it: the mail
    # says "reschedule your interview" while the pattern below requires
    # "rescheduled", so the model's INTERVIEW_RESCHEDULED reading had nothing
    # to corroborate it and the mail was parked, then ignored. The same thing
    # happened on 19 August and 27 August.
    #
    # Deliberately narrow. "Don't miss your interview" is a reminder and
    # "you missed our webinar" is not this candidate's interview, so the
    # subject must say the slot itself was missed or abandoned.
    interview_missed = bool(re.search(
        r"(?:\byou (?:have )?missed your interview\b"
        r"|\bmissed your interview slot\b"
        r"|\binterview slot missed\b"
        r"|\byou left your interview mid-?way\b"
        r"|\byou did not (?:attend|join) your interview\b)",
        lowered,
    ))
    interview_rescheduled = bool(re.search(
        r"(?:\binterview\b.{0,100}\b(?:rescheduled|moved|postponed)\b|\b(?:rescheduled|moved|postponed)\b.{0,100}\binterview\b)",
        lowered,
    ))
    interview_confirmed = bool(re.search(
        r"(?:\b(?:interview|technical round|managerial round|hr round)\b.{0,120}\b(?:confirmed|scheduled)\b|"
        r"\b(?:confirmed|scheduled)\b.{0,120}\b(?:interview|technical round|managerial round|hr round)\b)",
        lowered,
    )) or _is_assertive_interview_invitation(subject, body) or (
        # One vocabulary, not two. This clause and _TRANSITION_ASSERTIONS both
        # decide "is this a confirmed interview", and they had drifted: the
        # assertion set learned reminder phrasing while this kept requiring
        # "confirmed" or "scheduled". validate_interview_event needs *both* to
        # agree, so a reminder asserted the transition and then failed for want
        # of the matching interview_event. Deferring to the assertions keeps
        # them from disagreeing again.
        "INTERVIEW_CONFIRMED" in assertions
    )

    # Preserve the stronger structured calendar interpretation. It already
    # requires interview/round language plus an invitation and schedule; the
    # schedule never establishes relevance on its own.
    if interview_missed:
        # The times such a mail carries are the slot that was missed and the
        # deadline to rebook by. Neither schedules anything, so this must not
        # read as a confirmation or a reschedule however the sentence around
        # them is phrased -- the booking it refers to is released, and only a
        # genuinely new invitation books again.
        interview_confirmed = False
        interview_rescheduled = False
        interview_cancelled = True
    if interview_confirmed:
        assertions.add("INTERVIEW_CONFIRMED")
        assertions.add("INTERVIEW_UPDATE")
    if interview_rescheduled:
        assertions.add("INTERVIEW_RESCHEDULED")
    if interview_cancelled:
        assertions.add("INTERVIEW_CANCELLED")
        if interview_missed:
            assertions.discard("INTERVIEW_CONFIRMED")
            assertions.discard("INTERVIEW_UPDATE")
            assertions.discard("INTERVIEW_RESCHEDULED")

    # Explicit invitation + schedule semantics or post-selection outcome take precedence over document
    # checklist fields embedded in the same recruiter message.
    if actual_joined or joining_confirmed or offer_received or offer_accepted or selected or final_round_cleared or hr_confirmation or interview_confirmed or interview_rescheduled or interview_cancelled:
        questionnaire = False
        question = False

    if questionnaire:
        intent, summary = "RECRUITER_QUESTIONNAIRE", "Recruiter is requesting candidate information. No employment outcome is confirmed."
    elif transactional:
        intent, summary = "GENERAL", "This is an unrelated transactional/account notification, not a recruitment email."
    elif non_outcome_notice:
        intent, summary = "JOB_APPLICATION_UPDATE", "This is an application, profile, or networking notification; no candidate outcome is confirmed."
    elif job_ad:
        intent, summary = "JOB_ADVERTISEMENT", "This is a job advertisement or recruiter requirement, not a candidate employment outcome."
    elif payslip:
        intent, summary = "EMPLOYMENT_DOCUMENT", "Payslip contains historical employee metadata. No current joining event was found."
    elif question:
        intent, summary = "CANDIDATE_DETAILS_REQUEST", "The message asks for candidate information; it does not assert an employment outcome."
    elif interview_cancelled:
        intent, summary = "INTERVIEW_CANCELLATION", "The message explicitly cancels a candidate interview."
    elif interview_rescheduled:
        intent, summary = "INTERVIEW_RESCHEDULE", "The message explicitly changes an existing candidate interview schedule."
    elif interview_confirmed:
        intent, summary = "INTERVIEW_CONFIRMATION", "The message explicitly confirms a candidate interview schedule."
    elif "INTERVIEW_SHORTLISTED" in assertions:
        intent, summary = "INTERVIEW_INVITATION", "The message explicitly shortlists this candidate for an interview process."
    elif actual_joined:
        intent, summary = "ACTUAL_JOINING_CONFIRMATION", "The message explicitly confirms that employment has started."
    elif "BACKGROUND_VERIFICATION" in assertions:
        intent, summary = "BACKGROUND_VERIFICATION", "The message explicitly starts or requests this candidate's background verification."
    elif "DOCUMENT_VERIFICATION" in assertions:
        intent, summary = "DOCUMENT_VERIFICATION", "The message explicitly requests or confirms this candidate's document verification."
    elif "POST_SELECTION_ONBOARDING" in assertions:
        intent, summary = "JOINING_CONFIRMATION", "The message explicitly starts this candidate's pre-joining or onboarding process."
    elif "CANDIDATE_REJECTED" in assertions:
        intent, summary = "REJECTION", "The message explicitly states a negative outcome for this candidate."
    elif joining_confirmed:
        intent, summary = "JOINING_CONFIRMATION", "The message confirms a joining arrangement, background verification, or onboarding."
    elif offer_accepted:
        intent, summary = "OFFER_ACCEPTANCE", "The message explicitly confirms acceptance of an employment offer."
    elif offer_received or document_type in {"OFFER_LETTER", "APPOINTMENT_LETTER"}:
        intent, summary = "OFFER_LETTER", "The message contains a candidate-specific employment offer."
    elif final_round_cleared:
        intent, summary = "FINAL_ROUND_CLEARED", "The message explicitly confirms that the candidate has cleared an interview round."
    elif hr_confirmation:
        intent, summary = "HR_CONFIRMATION", "The message confirms HR discussion, salary/CTC confirmation, or post-selection document verification."
    elif selected:
        intent, summary = "SELECTION_CONFIRMATION", "The message explicitly confirms candidate selection."
    else:
        intent, summary = "UNKNOWN", "No validated candidate employment outcome was found."

    lifecycle = "NONE"
    if not (questionnaire or job_ad or non_outcome_notice or transactional or question or historical):
        if actual_joined:
            lifecycle = "JOINED"
        elif "JOINING_DATE_UPDATED" in assertions:
            lifecycle = "JOINING_DATE_UPDATED"
        elif "POST_SELECTION_ONBOARDING" in assertions:
            lifecycle = "POST_SELECTION_ONBOARDING"
        elif "BACKGROUND_VERIFICATION" in assertions:
            lifecycle = "BACKGROUND_VERIFICATION"
        elif "DOCUMENT_VERIFICATION" in assertions:
            lifecycle = "DOCUMENT_VERIFICATION"
        elif "APPOINTMENT_LETTER_RECEIVED" in assertions:
            lifecycle = "APPOINTMENT_LETTER_RECEIVED"
        elif "OFFER_REVOKED" in assertions:
            lifecycle = "OFFER_REVOKED"
        elif "OFFER_DECLINED" in assertions:
            lifecycle = "OFFER_DECLINED"
        elif "CANDIDATE_REJECTED" in assertions:
            lifecycle = "CANDIDATE_REJECTED"
        elif "INTERVIEW_SHORTLISTED" in assertions:
            lifecycle = "INTERVIEW_SHORTLISTED"
        elif joining_confirmed:
            lifecycle = "JOINING_CONFIRMED"
        elif offer_accepted:
            lifecycle = "OFFER_ACCEPTED"
        elif offer_received:
            lifecycle = "OFFER_LETTER_RECEIVED"
        elif selected:
            lifecycle = "SELECTED"
        elif final_round_cleared:
            lifecycle = "FINAL_ROUND_CLEARED"
        elif hr_confirmation:
            lifecycle = "HR_CONFIRMATION"
        else:
            for asserted in (
                "FINAL_SELECTION_CONFIRMED", "OFFER_APPROVED", "OFFER_IN_PROGRESS",
                "COMPENSATION_CONFIRMATION", "OFFER_INDICATION",
            ):
                if asserted in assertions:
                    lifecycle = asserted
                    break

    interview_event = "NONE"
    if not (questionnaire or job_ad or non_outcome_notice or transactional or question or historical):
        if interview_cancelled:
            interview_event = "INTERVIEW_CANCELLED"
        elif interview_rescheduled:
            interview_event = "INTERVIEW_RESCHEDULED"
        elif interview_confirmed:
            interview_event = "INTERVIEW_CONFIRMED"
    interview_asserted = bool(assertions.intersection({
        "INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED", "INTERVIEW_CONFIRMED",
        "INTERVIEW_RESCHEDULED", "INTERVIEW_CANCELLED",
    }))
    non_outcome_context = bool(
        questionnaire or job_ad or non_outcome_notice or transactional or question or historical
    )
    business_domain = (
        "INTERVIEW_TRACKING" if interview_event != "NONE" or (interview_asserted and not non_outcome_context)
        else "SELECTION_TRACKING" if lifecycle != "NONE"
        else "NONE"
    )

    return {
        "email_intent": intent,
        "document_type": document_type,
        "is_candidate_specific": not (job_ad or transactional),
        "is_job_outcome": lifecycle != "NONE",
        "is_current_event": lifecycle != "NONE" and not historical,
        "is_questionnaire": questionnaire,
        "is_question": question,
        "is_promotional_or_job_ad": job_ad or non_outcome_notice or transactional,
        "is_historical_information": historical,
        "historical_employment_evidence": historical,
        "lifecycle_event": lifecycle,
        "interview_event": interview_event,
        "business_domain": business_domain,
        "recruitment_relevance": "ESTABLISHED" if (
            not non_outcome_context and (lifecycle != "NONE" or interview_event != "NONE" or bool(assertions))
        ) else "NOT_ESTABLISHED",
        "asserted_transitions": sorted(assertions),
        "event_reference_date": _event_date(sent_at).isoformat(),
        "evidence_summary": summary,
    }


_LIFECYCLE_VALIDATION_REQUIREMENTS: dict[str, set[str]] = {
    "SELECTED": {"SELECTED"},
    "FINAL_SELECTION_CONFIRMED": {"FINAL_SELECTION_CONFIRMED"},
    "FINAL_ROUND_CLEARED": {"FINAL_ROUND_CLEARED"},
    "OFFER_INDICATION": {"OFFER_INDICATION", "OFFER_RECEIVED", "OFFER_LETTER_RECEIVED"},
    "OFFER_IN_PROGRESS": {"OFFER_IN_PROGRESS"},
    "OFFER_APPROVED": {"OFFER_APPROVED"},
    "OFFER_LETTER_RECEIVED": {"OFFER_LETTER_RECEIVED"},
    "OFFER_RECEIVED": {"OFFER_RECEIVED", "OFFER_LETTER_RECEIVED", "OFFER_INDICATION"},
    "APPOINTMENT_LETTER_RECEIVED": {"APPOINTMENT_LETTER_RECEIVED"},
    "OFFER_ACCEPTED": {"OFFER_ACCEPTED"},
    "OFFER_DECLINED": {"OFFER_DECLINED"},
    "OFFER_REVOKED": {"OFFER_REVOKED"},
    "JOINING_CONFIRMED": {"JOINING_CONFIRMED"},
    "JOINING_DATE_UPDATED": {"JOINING_DATE_UPDATED"},
    "JOINED": {"JOINED"},
    "POST_SELECTION_ONBOARDING": {"POST_SELECTION_ONBOARDING"},
    "BACKGROUND_VERIFICATION": {"BACKGROUND_VERIFICATION"},
    "DOCUMENT_VERIFICATION": {"DOCUMENT_VERIFICATION"},
    "HR_CONFIRMATION": {"HR_CONFIRMATION"},
    "COMPENSATION_CONFIRMATION": {"COMPENSATION_CONFIRMATION"},
    "INTERVIEW_UPDATE": {"INTERVIEW_UPDATE", "INTERVIEW_SHORTLISTED", "INTERVIEW_CONFIRMED"},
    "INTERVIEW_SHORTLISTED": {"INTERVIEW_SHORTLISTED"},
    # An assessment is proved by its own words only. An interview assertion
    # must never satisfy it, or a mail about an interview would become a test.
    "ASSESSMENT_INVITED": {"ASSESSMENT_INVITED"},
    "CANDIDATE_REJECTED": {"CANDIDATE_REJECTED"},
}


def validate_lifecycle_event(proposed: str, context: dict[str, Any]) -> tuple[str, str | None]:
    """Fail closed unless an explicit validator proves the proposed transition."""
    status = str(proposed or "NONE").upper()
    if status == "NONE":
        return status, None
    requirements = _LIFECYCLE_VALIDATION_REQUIREMENTS.get(status)
    if requirements is None:
        return "NONE", "UNHANDLED_TRACKED_STATUS"
    if any(context.get(key) for key in (
        "is_questionnaire", "is_question", "is_promotional_or_job_ad",
        "is_historical_information",
    )):
        return "NONE", str(context.get("email_intent") or "NON_OUTCOME_CONTEXT")
    asserted = {str(item).upper() for item in context.get("asserted_transitions") or []}
    if not asserted.intersection(requirements):
        return "NONE", "PROPOSED_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"
    return status, None


def validate_interview_event(proposed: str, context: dict[str, Any]) -> tuple[str, str | None]:
    """Require deterministic, assertive support before routing an interview mutation."""
    status = str(proposed or "NONE").upper()
    if status not in INTERVIEW_EVENTS:
        return "NONE", "NOT_AN_INTERVIEW_EVENT"
    if status == "NONE":
        return status, None
    if any(context.get(key) for key in (
        "is_questionnaire", "is_question", "is_promotional_or_job_ad",
        "is_historical_information",
    )):
        return "NONE", str(context.get("email_intent") or "NON_OUTCOME_CONTEXT")
    supported = str(context.get("interview_event") or "NONE").upper()
    asserted = {str(item).upper() for item in context.get("asserted_transitions") or []}
    if supported != status or status not in asserted:
        return "NONE", "INTERVIEW_EVENT_NOT_SUPPORTED_BY_ASSERTIVE_CONTEXT"
    return status, None
