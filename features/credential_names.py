"""How a Data Room credential is named and typed, wherever it appears (20 Oct).

One rule for the table, its search, a new or edited record, and every export
(dashboard/src/utils/credentialNames.js is its twin for the browser):

- spaces collapsed, and a word typed all in lower case (or a long word all in
  capitals) given a capital: "uday kumar" -> "Uday Kumar";
- a word with capitals of its own inside it left as typed: "RajaGopal";
- technology names spelled their own way: "Servicenow" -> "ServiceNow",
  "Sailpoint" -> "SailPoint", "Data Engineer" stays "Data Engineer";
- a last word that only repeats the credential's type dropped, because the type
  is shown beside the name: "Asha Rao Naukri" (Naukri) -> "Asha Rao". A word
  before the end is kept ("Telegram Login Number"), and a name is never emptied.

Nothing here touches a username, a password or an id.
"""
from __future__ import annotations

import re

# Spelled as their makers spell them. Keys are lower case.
TECH_NAMES = {
    "servicenow": "ServiceNow", "sailpoint": "SailPoint", "salesforce": "Salesforce", "devops": "DevOps",
    "mulesoft": "MuleSoft", "peoplesoft": "PeopleSoft", "sharepoint": "SharePoint", "powerapps": "PowerApps",
    "powerbi": "PowerBI", "workday": "Workday", "databricks": "Databricks", "snowflake": "Snowflake",
    "tableau": "Tableau", "oracle": "Oracle", "kubernetes": "Kubernetes", "jira": "Jira", "linkedin": "LinkedIn",
    "github": "GitHub", "gitlab": "GitLab", "javascript": "JavaScript", "typescript": "TypeScript",
    "reactjs": "ReactJS", "nodejs": "NodeJS", "ios": "iOS", "macos": "macOS",
    "sap": "SAP", "aws": "AWS", "gcp": "GCP", "qa": "QA", "ui": "UI", "ux": "UX", "sql": "SQL", "etl": "ETL",
    "api": "API", "hr": "HR", "it": "IT", "ai": "AI", "ml": "ML", "bi": "BI", "sdet": "SDET", "crm": "CRM",
    "gmail": "Gmail", "naukri": "Naukri", "telegram": "Telegram",
}

# The types a credential can have, as they are shown and exported.
LOGINS_TYPE = "Dashboard / Tool Login"
_SERVICE_TYPE_NAMES = {"Naukri": "Naukri", "Gmail": "Gmail", "Telegram": "Telegram", "Tool": LOGINS_TYPE}
NOT_SET_TYPE = "Not Set"

_SPACES = re.compile(r"\s+")
_EDGE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.UNICODE)


def credential_type(service: str | None, *, login: bool = False) -> str:
    """The type a credential is shown and exported as."""
    if login:
        return LOGINS_TYPE
    return _SERVICE_TYPE_NAMES.get(str(service or "").strip(), NOT_SET_TYPE)


def _capitalise(word: str) -> str:
    """One word, with any punctuation round it kept: "(current)" -> "(Current)"."""
    lead, core, trail = _EDGE.match(word).groups()
    if not core:
        return word
    known = TECH_NAMES.get(core.lower())
    if known:
        return f"{lead}{known}{trail}"
    letters = [c for c in core if c.isalpha()]
    if not letters:
        return word
    all_lower = all(c.islower() for c in letters)
    shouting = all(c.isupper() for c in letters) and len(letters) > 3
    if not (all_lower or shouting):
        return word                                   # capitals of its own: "RajaGopal", "QA"
    # Each part of a hyphenated or apostrophised word: "sai-kiran" -> "Sai-Kiran".
    parts = re.split(r"([-'])", core.lower())
    return lead + "".join(p[:1].upper() + p[1:] if p not in ("-", "'") else p for p in parts) + trail


def _bare(word: str) -> str:
    return _EDGE.match(word).group(2).lower()


def standard_credential_name(name: str | None, service: str | None = None, *, drop_type: bool = True) -> str:
    """The credential's name, standardised. `service` is its stored service (Naukri, Gmail, Telegram, Tool)."""
    text = _SPACES.sub(" ", str(name or "")).strip()
    if not text:
        return ""
    words = [_capitalise(w) for w in text.split(" ")]
    type_word = str(service or "").strip().lower()
    if drop_type and type_word and type_word != "tool":
        while len(words) > 1 and _bare(words[-1]) == type_word:
            words.pop()
    return " ".join(words)
