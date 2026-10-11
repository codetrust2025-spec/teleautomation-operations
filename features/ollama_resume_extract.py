"""Ollama-powered resume PDF extraction.

Extracts structured candidate profile data from uploaded PDF resumes:
  - candidate_name
  - phone
  - email
  - technology (primary skill / tech stack)
  - years_of_experience
  - current_company
  - skills (list)
  - education
  - summary (2-3 line professional summary)

Flow:
  1. Extract text from PDF using PyPDF2/pdfplumber
  2. Send raw text to qwen2.5:7b (fast text model, ~10-30s) for structured extraction
  3. If text extraction fails (scanned PDF), convert pages to images and use qwen2.5vl:7b vision
  4. Return structured JSON with candidate profile data

Ollama runs on developer's laptop, tunneled to VPS via SSH.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from typing import Any

from core.ocr_policy import ocr_enabled

from core.ai_gateway import AIGatewayError, chat, health
from core.ai_model_routing import model_for

logger = logging.getLogger(__name__)

# ── Configuration ───────────────────────────────────────────────────────────
OLLAMA_REASONING_MODEL = model_for("reasoning_text")
OLLAMA_VISION_MODEL = model_for("resume_vision")
OLLAMA_TEXT_TIMEOUT = int(os.environ.get("OLLAMA_TEXT_TIMEOUT", "60"))
# Resume extraction may need longer timeout (model cold start on first call)
OLLAMA_RESUME_TEXT_TIMEOUT = int(os.environ.get("OLLAMA_RESUME_TEXT_TIMEOUT", "180"))
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "900"))


# ── Prompt ──────────────────────────────────────────────────────────────────
RESUME_EXTRACTION_PROMPT = """You are a resume parsing assistant for an IT staffing company in India.
Extract candidate profile data from the following resume text.

Return ONLY valid JSON. Do not explain. Do not use markdown. Do not wrap JSON in code blocks.

Schema:
{{"candidate_name": "", "phone": "", "email": "", "technology": "", "years_of_experience": "", "current_company": "", "current_designation": "", "location": "", "skills": [], "education": "", "certifications": [], "summary": "", "confidence_score": 0}}

Rules:
- "candidate_name": Full name of the candidate
- "phone": Indian mobile number (10 digits) if found, else ""
- "email": Email address if found
- "technology": The PRIMARY technology/skill (e.g., "SAP BASIS", "React JS", "AWS", "Java", ".NET", "Python", "DevOps", "Salesforce"). Pick the most prominent one.
- "years_of_experience": Total years as a string (e.g., "5", "3.5", "10+"). If not explicit, estimate from work history dates.
- "current_company": Most recent employer
- "current_designation": Current job title/role
- "location": City, State if found
- "skills": Array of technical skills (max 10 most relevant)
- "education": Highest degree + institution (e.g., "B.Tech CS - JNTU Hyderabad")
- "certifications": Array of certifications (e.g., ["AWS SAA", "Azure AZ-900"])
- "summary": 1-2 sentence professional summary
- "confidence_score": 0-100 based on how complete the extraction is

Resume text:
---
{resume_text}
---
"""


# ── Empty result ────────────────────────────────────────────────────────────
def _empty_extraction() -> dict[str, Any]:
    return {
        "candidate_name": "",
        "phone": "",
        "email": "",
        "technology": "",
        "years_of_experience": "",
        "current_company": "",
        "current_designation": "",
        "location": "",
        "skills": [],
        "education": "",
        "certifications": [],
        "summary": "",
        "confidence_score": 0,
        "extraction_source": "",
        "extraction_method": "",
        "primary_model": "",
        "is_resume": False,
    }


# ── Ollama helpers ──────────────────────────────────────────────────────────
def _is_ollama_available() -> bool:
    status = health(model=OLLAMA_REASONING_MODEL, timeout=5)
    return bool(status.get("endpoint_reachable") and status.get("model_available"))


def _call_text_model(prompt: str, *, timeout: int = OLLAMA_TEXT_TIMEOUT) -> str | None:
    try:
        result = chat(
            model=OLLAMA_REASONING_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            workload="resume_text",
        )
        return result.content or None
    except AIGatewayError as exc:
        logger.warning("Resume text failed model=%s code=%s", OLLAMA_REASONING_MODEL, exc.code)
        return None


def _call_vision_model(image_base64: str, prompt: str, *, timeout: int = OLLAMA_TIMEOUT) -> str | None:
    try:
        result = chat(
            model=OLLAMA_VISION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            images=[image_base64],
            timeout=timeout,
            temperature=0.1,
            num_predict=2048,
            workload="resume_vision",
        )
        return result.content or None
    except AIGatewayError as exc:
        logger.warning("Resume vision failed model=%s code=%s", OLLAMA_VISION_MODEL, exc.code)
        return None


# ── JSON parsing ────────────────────────────────────────────────────────────
def _parse_json_response(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Find JSON object
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # Try more aggressive JSON extraction for nested arrays
    start = text.find('{')
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        result = json.loads(text[start:i+1])
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        pass
                    break
    return None


# ── Complete contact fields only ────────────────────────────────────────────
_NAME_WORD = re.compile(r"[A-Za-z]+")
_EMAIL_SHAPE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z0-9\-]+)+$")


def _indian_mobile(value: Any) -> str:
    """The 10-digit Indian mobile in `value` (+91 / leading 0 allowed), or ""."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else ""


def _complete_name(value: Any) -> str:
    """A person's name (one word or more, never a section heading), or ""."""
    from features.resume_autofill import person_name

    return person_name(value)


def _unspaced(text: str) -> str:
    """Text without whitespace: some PDFs space letters out ("p ujitha @ gm ail.co m")."""
    return re.sub(r"\s+", "", text or "")


def _on_page_name(name: str, text: str) -> bool:
    return all(
        re.search(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, re.IGNORECASE)
        for word in _NAME_WORD.findall(name)
    )


def _settle_contact_fields(result: dict[str, Any], text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Keep the name, phone and email the model read only when each is complete
    and -- when the resume has a text layer -- actually written in it. Otherwise
    take the plain-text reading's value if that one is, or leave the field
    empty: a cut-short "Kolla Raghava" or "77995" is never passed on."""
    text = text or ""
    digits = re.sub(r"\D", "", text)
    lowered = text.lower()

    phone = ""
    for candidate in (result.get("phone"), fallback.get("phone")):
        mobile = _indian_mobile(candidate)
        if mobile and (not text or mobile in digits):
            phone = mobile
            break

    name = ""
    for candidate in (result.get("candidate_name"), fallback.get("candidate_name")):
        complete = _complete_name(candidate)
        if complete and (not text or _on_page_name(complete, text)):
            name = complete
            break

    email = ""
    squeezed = _unspaced(lowered)
    for candidate in (result.get("email"), fallback.get("email")):
        address = str(candidate or "").strip().lower()
        if _EMAIL_SHAPE.match(address) and (not text or address in lowered or address in squeezed):
            email = address
            break

    result["candidate_name"], result["phone"], result["email"] = name, phone, email
    return result


# ── PDF text extraction ─────────────────────────────────────────────────────
def _extract_text_from_pdf(pdf_data: bytes) -> str | None:
    """Extract text from a PDF using available libraries."""
    # Try pdfplumber first (best quality)
    try:
        import pdfplumber
        import io
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
            for page in pdf.pages[:10]:  # Max 10 pages
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        if text_parts:
            return "\n\n".join(text_parts)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pdfplumber failed: %s", exc)

    # Try PyPDF2
    try:
        import PyPDF2
        import io
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_data))
        text_parts = []
        for page in reader.pages[:10]:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        if text_parts:
            return "\n\n".join(text_parts)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("PyPDF2 failed: %s", exc)

    # Try pymupdf (fitz)
    try:
        import fitz
        import io
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        text_parts = []
        for page in doc[:10]:
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
        doc.close()
        if text_parts:
            return "\n\n".join(text_parts)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pymupdf failed: %s", exc)

    return None


def _pdf_first_page_to_image(pdf_data: bytes) -> str | None:
    """Convert first page of PDF to base64 image for vision model (fallback for scanned PDFs)."""
    try:
        import fitz
        import base64
        import io
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        page = doc[0]
        # Render at 2x for better OCR
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        doc.close()
        return base64.b64encode(img_data).decode("utf-8")
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("PDF to image failed: %s", exc)
    return None


def _ocr_text_from_image_base64(image_base64: str) -> str:
    """Read a rendered resume page locally before using the slow vision model."""
    if not ocr_enabled():
        return ""
    try:
        import base64
        import io

        import pytesseract
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(base64.b64decode(image_base64)))
        full_text = pytesseract.image_to_string(image).strip()

        # Contact details are commonly printed in a small font in the upper-right
        # corner.  Whole-page OCR can merge or drop characters there (especially
        # the dot in ``gmail.com``), so read that area again with a layout mode
        # intended for a compact block of text.  Put this result first so a clean
        # contact-area email wins over a noisier whole-page interpretation.
        width, height = image.size
        contact_area = image.crop((int(width * 0.48), 0, width, int(height * 0.18)))
        contact_text = pytesseract.image_to_string(
            ImageOps.grayscale(contact_area), config="--psm 6"
        ).strip()

        return "\n".join(part for part in (contact_text, full_text) if part)
    except Exception as exc:
        logger.warning("Resume page OCR failed: %s", exc)
        return ""


# ── Regex fallback extraction ───────────────────────────────────────────────
def _extract_name_from_text(text: str) -> str:
    """Heuristic: candidate name is typically the first prominent non-address line.

    Strategy:
    1. Skip blank lines and lines that look like contact info (email, phone, URL, city/country).
    2. Take the first remaining line that is 2-5 words of title-case or ALL-CAPS text.
    """
    skip_patterns = re.compile(
        r'@|http|www\.|linkedin|github|'           # contact / urls
        r'\d{5,}|'                                  # phone / zip
        r'(?:india|hyderabad|bangalore|mumbai|delhi|chennai|pune|'
        r'noida|gurugram|gurgaon|kolkata|ahmedabad)',
        re.IGNORECASE,
    )
    from features.resume_autofill import person_name

    lines = text.splitlines()
    for line_number, line in enumerate(lines[:20], start=1):  # Only look in first 20 lines
        stripped = line.strip()
        if not stripped:
            continue
        # OCR often joins the name and contact label onto the same line.
        stripped = re.split(
            r'\b(?:email|mobile|phone|linkedin)\s*:',
            stripped,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()
        if not stripped:
            continue
        if skip_patterns.search(stripped):
            continue
        # A section heading ("SUMMARY OF QUALIFICATIONS") is never the name.
        if not person_name(stripped):
            continue
        # 1-5 words, no digits, no special chars except spaces and hyphens. A
        # single word ("RAVINA") counts only near the top, where the name is.
        words = stripped.split()
        if not (1 <= len(words) <= 5):
            continue
        if len(words) == 1 and (line_number > 3 or len(words[0]) < 3):
            continue
        if re.search(r'[^A-Za-z\s\-\.]', stripped):
            continue
        # Words should look like a name: title-case or ALL-CAPS
        name_like = all(
            w[0].isupper() or w.isupper()
            for w in words
            if len(w) > 1
        )
        if name_like:
            # Convert ALL-CAPS to title case for readability
            return " ".join(w.title() if w.isupper() else w for w in words)
    return ""


def _regex_extract_from_text(text: str) -> dict[str, Any]:
    """Basic regex extraction when AI is unavailable."""
    result = _empty_extraction()
    if not text:
        return result

    # Candidate name — heuristic from top of resume
    name = _extract_name_from_text(text)
    if name:
        result["candidate_name"] = name

    # Phone number (Indian 10-digit)
    phone_match = re.search(r'(?:\+91[\s\-]?)?([6-9]\d{9})', text)
    if phone_match:
        result["phone"] = phone_match.group(1)

    # Email
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
    if email_match:
        result["email"] = email_match.group(0)
    else:
        # A PDF that spaces letters out ("p ujitha.v59 @ gm ail.co m"): read each
        # line without its spaces, the domain ending at its last letters.
        for line in text.splitlines()[:40]:
            spaced = re.search(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(?:\.[a-z]{2,})+", _unspaced(line), re.IGNORECASE)
            if spaced:
                result["email"] = spaced.group(0).lower()
                break

    # Common technologies — extended with QA/Automation stack
    tech_keywords = [
        # Role-level technologies should win over individual tools below.
        "Java Full Stack", "MERN Stack", "MEAN Stack", "Full Stack",
        # Automation / QA
        "Selenium", "Playwright", "Cypress", "Appium", "TestNG", "JUnit",
        "Cucumber BDD", "Rest Assured", "REST Assured", "Postman",
        # Java ecosystem
        "Java", "Spring Boot", "Spring",
        # SAP
        "SAP BASIS", "SAP HANA", "SAP FICO", "SAP MM", "SAP SD", "SAP ABAP",
        # Frontend
        "React", "Angular", "Vue", "Next.js", "TypeScript", "JavaScript",
        # Backend
        "Node.js", "Python", "C#", ".NET", "Go", "Ruby on Rails",
        # Cloud / DevOps
        "AWS", "Azure", "GCP", "DevOps", "Docker", "Kubernetes", "Jenkins",
        # Data
        "Data Engineer", "Machine Learning", "AI/ML", "Full Stack",
        "SQL Server", "Oracle", "PostgreSQL", "MySQL",
        # Other
        "Salesforce", "ServiceNow", "Workday",
        "iOS", "Android", "Flutter", "React Native",
    ]
    text_lower = text.lower()
    for tech in tech_keywords:
        if tech.lower() in text_lower:
            result["technology"] = tech
            break

    # Years of experience
    exp_match = re.search(r'(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:experience|exp)', text, re.IGNORECASE)
    if exp_match:
        result["years_of_experience"] = exp_match.group(1)

    # Location — common Indian cities
    location_match = re.search(
        r'\b(Hyderabad|Bangalore|Bengaluru|Mumbai|Delhi|Chennai|Pune|Noida|'
        r'Gurugram|Gurgaon|Kolkata|Ahmedabad|Chandigarh|Jaipur|Kochi)\b',
        text, re.IGNORECASE,
    )
    if location_match:
        result["location"] = location_match.group(1)

    # Current company — line after keywords like "at", "working at", or after experience header
    company_match = re.search(
        r'(?:currently\s+(?:working\s+)?(?:at|with)|employer|company)\s*[:\-]?\s*([A-Z][A-Za-z0-9\s\.\,&]+)',
        text, re.IGNORECASE,
    )
    if company_match:
        result["current_company"] = company_match.group(1).strip()[:60]

    result["extraction_source"] = "regex_only"
    result["extraction_method"] = "regex_fallback"
    result["is_resume"] = bool(result["phone"] or result["email"] or result["technology"])
    result["confidence_score"] = sum(10 for f in ["candidate_name", "phone", "email", "technology", "years_of_experience"] if result.get(f))
    return result


# ── Main extraction function ────────────────────────────────────────────────
def extract_resume_with_ollama(
    pdf_data: bytes,
    mime_type: str = "application/pdf",
) -> dict[str, Any]:
    """Extract candidate profile from a PDF resume using hybrid text + AI approach.

    Flow:
      1. Extract text from PDF (pdfplumber/PyPDF2/pymupdf)
      2. If text found, send to qwen2.5:7b text model (~10-30s)
      3. If no text (scanned PDF), render first page to image → qwen2.5vl:7b vision
      4. If AI unavailable, fall back to regex extraction

    Returns structured dict with candidate_name, technology, phone, etc.
    """
    # ── Step 1: Extract text from PDF ───────────────────────────────────────
    pdf_text = _extract_text_from_pdf(pdf_data)

    if pdf_text and len(pdf_text.strip()) > 50:
        logger.info("PDF text extracted: %d chars", len(pdf_text))

        # Try regex first (instant) — gives us a baseline
        regex_result = _regex_extract_from_text(pdf_text)

        # ── Step 2: AI text model extraction ────────────────────────────────
        if _is_ollama_available():
            logger.info("Sending resume text to %s", OLLAMA_REASONING_MODEL)
            start = time.time()
            prompt = RESUME_EXTRACTION_PROMPT.format(
                resume_text=pdf_text[:6000]  # Limit to avoid token overflow
            )
            response = _call_text_model(prompt, timeout=OLLAMA_RESUME_TEXT_TIMEOUT)
            elapsed = time.time() - start
            logger.info("Text model responded in %.1fs", elapsed)

            if response:
                parsed = _parse_json_response(response)
                if parsed and parsed.get("candidate_name"):
                    result = _empty_extraction()
                    result.update(parsed)
                    result["extraction_source"] = "pdf_text_ai"
                    result["extraction_method"] = "hybrid_fast"
                    result["primary_model"] = OLLAMA_REASONING_MODEL
                    result["is_resume"] = True
                    # Ensure skills is a list
                    if isinstance(result.get("skills"), str):
                        result["skills"] = [s.strip() for s in result["skills"].split(",") if s.strip()]
                    if isinstance(result.get("certifications"), str):
                        result["certifications"] = [c.strip() for c in result["certifications"].split(",") if c.strip()]
                    # Normalize confidence
                    try:
                        result["confidence_score"] = max(0, min(100, int(result.get("confidence_score", 0))))
                    except (ValueError, TypeError):
                        result["confidence_score"] = 70
                    _settle_contact_fields(result, pdf_text, regex_result)
                    logger.info(
                        "AI extracted: name=%s, tech=%s, exp=%s",
                        result["candidate_name"],
                        result["technology"],
                        result["years_of_experience"],
                    )
                    return result

        # AI unavailable or failed — return regex result
        return regex_result

    # ── Step 3: Scanned PDF — try vision model ──────────────────────────────
    logger.info("PDF text extraction failed or too short, trying vision model")

    if not _is_ollama_available():
        logger.info("Ollama not available for vision fallback")
        result = _empty_extraction()
        result["extraction_source"] = "failed"
        result["extraction_method"] = "no_text_no_ai"
        result["confidence_score"] = 0
        return result

    # Convert first page to image
    img_b64 = _pdf_first_page_to_image(pdf_data)
    if not img_b64:
        logger.warning("Could not convert PDF to image for vision model")
        result = _empty_extraction()
        result["extraction_source"] = "failed"
        result["extraction_method"] = "pdf_to_image_failed"
        return result

    # Fast scanned-PDF path: local OCR is normally enough for the profile
    # fields and avoids waiting several minutes for a remote vision model.
    ocr_text = _ocr_text_from_image_base64(img_b64)
    if len(ocr_text) > 50:
        ocr_result = _regex_extract_from_text(ocr_text)
        if ocr_result.get("is_resume"):
            ocr_result["extraction_source"] = "pdf_image_ocr"
            ocr_result["extraction_method"] = "tesseract_regex"
            ocr_result["primary_model"] = "tesseract"
            return ocr_result

    # Call vision model
    vision_prompt = RESUME_EXTRACTION_PROMPT.format(resume_text="[Image of resume page attached]")
    logger.info("Calling vision model for scanned resume")
    start = time.time()
    response = _call_vision_model(img_b64, vision_prompt, timeout=OLLAMA_TIMEOUT)
    elapsed = time.time() - start
    logger.info("Vision model responded in %.1fs", elapsed)

    if response:
        parsed = _parse_json_response(response)
        if parsed and parsed.get("candidate_name"):
            result = _empty_extraction()
            result.update(parsed)
            result["extraction_source"] = "scanned_pdf_vision"
            result["extraction_method"] = "vision"
            result["primary_model"] = OLLAMA_VISION_MODEL
            result["is_resume"] = True
            if isinstance(result.get("skills"), str):
                result["skills"] = [s.strip() for s in result["skills"].split(",") if s.strip()]
            if isinstance(result.get("certifications"), str):
                result["certifications"] = [c.strip() for c in result["certifications"].split(",") if c.strip()]
            try:
                result["confidence_score"] = max(0, min(100, int(result.get("confidence_score", 0))))
            except (ValueError, TypeError):
                result["confidence_score"] = 60
            # No text layer to check against: completeness only.
            _settle_contact_fields(result, "", {})
            return result

    # Everything failed
    result = _empty_extraction()
    result["extraction_source"] = "failed"
    result["extraction_method"] = "all_failed"
    return result
