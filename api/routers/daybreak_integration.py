"""Authenticated, facts-only AI headline generation for Daybreak."""

from __future__ import annotations

import json
import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/api/integrations/daybreak", tags=["daybreak"])


class ApprovedFacts(BaseModel):
    """Facts deliberately supplied by one Daybreak candidate owner."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_role: str = Field(max_length=100)
    experience: str = Field(max_length=60)
    skills: list[str] = Field(max_length=30)
    profile_facts: str = Field(max_length=1000)

    @field_validator("skills")
    @classmethod
    def bounded_skills(cls, values: list[str]) -> list[str]:
        if any(not item or len(item) > 60 for item in values):
            raise ValueError("skills must be non-empty and at most 60 characters")
        return values


class DaybreakHeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    candidate_ref: str = Field(min_length=1, max_length=200)
    facts: ApprovedFacts
    previous_headline: str = Field(default="", max_length=250)
    max_length: int = Field(default=250, ge=30, le=250)


_HEADLINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline"],
    "properties": {"headline": {"type": "string", "minLength": 1, "maxLength": 250}},
}


def _authorized(authorization: str | None) -> bool:
    expected = (os.environ.get("DAYBREAK_HEADLINE_TOKEN") or "").strip()
    if not expected or not authorization:
        return False
    scheme, _, value = authorization.partition(" ")
    return scheme.lower() == "bearer" and bool(value) and secrets.compare_digest(value, expected)


def _prompt(request: DaybreakHeadlineRequest) -> str:
    facts = request.facts.model_dump()
    # JSON makes the boundary explicit: only this candidate's approved facts
    # are passed to the shared Operations gateway. No stored Operations
    # candidate data is consulted by this route.
    return (
        "Write one professional Naukri resume headline. Return JSON only. "
        "Use only the approved facts below. Do not add companies, projects, "
        "certifications, locations, salaries, seniority labels, achievements, "
        "or technologies that are not present. Every non-neutral word must appear "
        "verbatim in the approved facts; do not add generic claim words such as "
        "experienced, strong, expertise, expert, skilled, or skills. Do not include a name, quote, "
        "markdown, URL, email, phone number, or first-person wording. "
        f"The headline must be 30 to {request.max_length} characters.\n"
        "APPROVED_FACTS=" + json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    )


@router.post("/headline")
def generate_headline(
    payload: DaybreakHeadlineRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    """Use the existing Operations node router; never call Ollama directly."""
    if not _authorized(authorization):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        from core.ai_gateway import AIGatewayError, chat_structured
        from core.ai_model_routing import model_for

        result = chat_structured(
            messages=[
                {"role": "system", "content": "You produce concise, factual resume headlines."},
                {"role": "user", "content": _prompt(payload)},
            ],
            schema=_HEADLINE_SCHEMA,
            model=model_for("reasoning_text"),
            temperature=0,
            max_retries=1,
            workload="daybreak_headline",
            num_predict=120,
            think=False,
        )
        generated = json.loads(result.content)
        headline = generated.get("headline") if isinstance(generated, dict) else None
        if (
            not isinstance(headline, str)
            or len(headline.strip()) < 30
            or len(headline.strip()) > payload.max_length
        ):
            raise ValueError("invalid structured headline")
        return {
            "status": "ok",
            "headline": headline.strip(),
            "node_id": result.node_id,
            "node_label": result.node_label,
            "model": result.model,
            "duration_ms": result.duration_ms,
        }
    except AIGatewayError:
        # Node/model diagnostics must not be reflected to an external caller.
        raise HTTPException(status_code=503, detail="AI generation unavailable") from None
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="AI generation returned an invalid result") from None
