import pytest
from services.recruitment_semantics import classify_context

def test_accenture_scheduling_invite_is_not_offer():
    subject = "Time to schedule your interview with Accenture!"
    body = """Dear Candidate,
    Congratulations on clearing the initial assessment. It is now time to schedule your interview with Accenture.
    Please choose your preferred time slot.
    Disclaimer: Accenture does not charge any fee at any stage of the recruitment process or for a job offer of employment. Beware of fraudulent emails."""
    ctx = classify_context(subject, body, sender_email="accenture.recruitment@accenture.com")
    assert ctx["lifecycle_event"] == "NONE"
    assert ctx["email_intent"] != "OFFER_LETTER"

def test_accenture_availability_reminder_is_not_offer():
    subject = "Reminder: Your Availability Required to Proceed for Interview"
    body = """Dear Candidate,
    This is a reminder to provide your availability for the technical round interview.
    Accenture never charges any fee for an offer of employment."""
    ctx = classify_context(subject, body, sender_email="careers@accenture.com")
    assert ctx["lifecycle_event"] == "NONE"
    assert ctx["email_intent"] != "OFFER_LETTER"

def test_job_description_with_hr_discussion_is_job_ad_not_hr_confirmation():
    subject = "JD For ServiceNow developer (ITSM or CMDB/CSDM)"
    body = """Hi,
    We have an opening for ServiceNow Developer at Infosys.
    Job Description:
    Skills: ITSM, CMDB, CSDM
    Experience: 4-6 years
    Location: Hyderabad
    Selection Process:
    1. L1 Technical Round
    2. L2 Managerial Round
    3. HR Discussion / CTC discussion
    If interested, please share your updated resume."""
    ctx = classify_context(subject, body, sender_email="hr@infosys-vendor.com")
    assert ctx["lifecycle_event"] == "NONE"
    assert ctx["email_intent"] in {"JOB_ADVERTISEMENT", "JOB_REQUIREMENT", "RECRUITER_QUESTIONNAIRE", "CANDIDATE_DETAILS_REQUEST", "UNKNOWN"}

def test_genuine_pre_offer_documents_is_hr_confirmation():
    subject = "Pre-Offer Documents !"
    body = "Hi Aniket, Minimal documents required for offer release: Please share PAN, Aadhar, Payslips, Degree Certificate."
    ctx = classify_context(subject, body, sender_email="recruiter@company.com")
    assert ctx["lifecycle_event"] == "HR_CONFIRMATION"

def test_genuine_bgv_is_retained_as_bgv_not_joining_confirmed():
    subject = "Invitation - Digital Employment BGV_RH30116125"
    body = "Dear Aniket, You have been invited to complete your digital employment background verification process."
    ctx = classify_context(subject, body, sender_email="noreply@digiverifier.com")
    assert ctx["lifecycle_event"] == "BACKGROUND_VERIFICATION"

def test_genuine_final_round_cleared():
    subject = "EY | L2 | React JS | Reddy Charan M S"
    body = "Hi Charan, Thank you for continued interest in working with EY India. We are pleased to inform you that you have successfully cleared the L1 round."
    ctx = classify_context(subject, body, sender_email="Sunita.K@in.ey.com")
    assert ctx["lifecycle_event"] == "FINAL_ROUND_CLEARED"

def test_genuine_offer_letter():
    subject = "Intent Offer Letter - Infoshare"
    body = "Dear Charan, We are pleased to extend an offer for the position of React JS Developer at Infoshare."
    ctx = classify_context(subject, body, sender_email="hr@infoshare.com")
    assert ctx["lifecycle_event"] in {"OFFER_LETTER_RECEIVED", "OFFER_RECEIVED"}
