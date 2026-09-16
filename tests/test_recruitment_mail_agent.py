from datetime import datetime, timezone

import pytest
from tests.legacy_detection import legacy_rules_first  # noqa: F401

from core.ai_gateway import AIGatewayError
from core.recruitment_mail_store import should_route_to_mail_alert
from services.recruitment_mail_agent import (
    _prompt_json, _manual_review_from_strong_context, clean_email, relevance_score, content_hash, validate_result, parse_model_json,
    routing_decision, _failure_review_result,
)

def valid_result():
    return {'schema_version':'selection_offer_event_v1','is_recruitment_related':True,'is_selection_or_offer_related':True,'should_create_review_record':True,'status':'SELECTED','confidence':.95,'ignore_reason':None,'candidate':{'name':None,'email':None},'company':{'name':None,'domain':None},'job':{'title':None,'employment_type':None,'location':None},'recruiter':{'name':None,'email':None},'interview':{k:None for k in ['date','time','timezone','mode','round','location','meeting_link']},'offer':{'offer_detected':False,'offer_letter_detected':False,'appointment_letter_detected':False,'offer_date':None,'offered_ctc':None,'currency':None,'joining_date':None,'offer_expiry_date':None},'attachments':[],'evidence':[{'source':'EMAIL_BODY','meaning':'SELECTED','text':'you have been selected'}],'risk_flags':[],'requires_manual_review':False,'summary':'Selected.','recommended_action':'Review.'}

def test_clean_email_removes_html_and_quoted_history():
    value=clean_email('<p>Your interview has been scheduled.</p>\nOn yesterday Recruiter wrote:\nold text')
    assert value=='Your interview has been scheduled.'

def test_mail_filter_tracks_interview_update_but_ignores_job_alert():
    invite=relevance_score('Interview scheduled','Your technical round is confirmed.',['invite.pdf'])
    alert=relevance_score('LinkedIn job alert','New jobs selected for you',[])
    assert invite >= .9
    assert alert == 0


def test_mail_filter_routes_assertive_calendar_invite_wording():
    invite=relevance_score(
        'Virtual Interview - Senior Full Stack Engineer (Front-End Focused)',
        'Please join the Virtual Interview at 12.30 pm on 21st July, 2026. Microsoft Teams meeting.',
    )
    assert invite >= .6


def test_mail_filter_routes_numbered_round_calendar_invite_without_interview_word():
    subject=('Invitation from an unknown sender: L1 Discussion with Nitin for SOMT '
             '@ Tue Jul 21, 2026 11:30am - 12pm (IST) (candidate@example.com)')
    route=routing_decision(subject,'Data Template Infotech Private Limited',sender_email='organizer@example.com')
    assert route['send_to_ai'] is True
    assert route['context']['qualified'] is True
    assert route['context']['status']=='INTERVIEW_CONFIRMED'


def test_ai_outage_keeps_strong_interview_invite_visible_for_review():
    subject='Virtual Interview - Senior Full Stack Engineer (Front-End Focused)'
    body='Please join the Virtual Interview at 12.30 pm on 21st July, 2026. Microsoft Teams meeting.'
    route=routing_decision(subject,body,sender_email='recruiter@example.com')
    result=_manual_review_from_strong_context(
        {'subject':subject,'body':body,'sent_at':datetime(2026,7,20,tzinfo=timezone.utc)},
        route['context'],
        AIGatewayError('timeout',code='OLLAMA_REQUEST_TIMEOUT'),
    )
    assert result['primary_status'] == 'AI_RETRY_PENDING'
    assert result['business_domain'] == 'INTERVIEW_TRACKING'
    assert result['interview']['date'] == '2026-07-21'
    assert result['interview']['time'] == '12:30 PM'
    assert result['requires_manual_review'] is False

def test_mail_filter_uses_qualified_thread_context():
    interview=relevance_score('Re: update','Please see below.',[],[{'subject':'Technical round interview confirmation'}])
    selected=relevance_score('Re: update','Please see below.',[],[{'subject':'Selection update','body':'You have been selected for the role.'}])
    assert interview >= .9
    assert selected >= .9

def test_hash_is_deterministic():
    assert content_hash('same')==content_hash('same')
    assert content_hash('same')!=content_hash('different')

def test_confidence_rule_retries_low_and_records_medium_without_asking_anyone():
    """Neither band asks a person, and neither band auto-validates.

    Below the review threshold the reading is too unsure to record at all, so
    it goes back for another attempt. In the medium band it is sure enough to
    keep but not to act on -- which `validation_status` already says, since a
    candidate advances only on AUTO_VALIDATED.
    """
    message={'subject':'Selection update','body':'You have been selected for the role.'}
    low=valid_result();low['confidence']=.65
    validate_result(low,message)
    assert low['status'] == 'AI_RETRY_PENDING'
    assert low['classification'] == 'ai_retry_pending'
    assert low['requires_manual_review'] is False
    assert low['should_create_review_record'] is False
    medium=valid_result();medium['confidence']=.85
    validate_result(medium,message)
    assert medium['status'] != 'MANUAL_REVIEW_REQUIRED'
    assert medium['requires_manual_review'] is False
    assert medium['validation_status'] != 'AUTO_VALIDATED'

def test_invalid_status_is_rejected():
    row=valid_result();row['status']='MADE_UP'
    try:validate_result(row)
    except ValueError:return
    raise AssertionError('invalid status accepted')

def test_safe_json_repair_once():
    assert parse_model_json('```json\n{"status":"ok",}\n```') == {'status':'ok'}


def test_prompt_json_serializes_provider_datetime():
    payload = {"sent_at": datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)}
    assert '"sent_at": "2026-07-18T10:00:00+00:00"' in _prompt_json(payload)

def test_non_iso_offer_dates_are_dropped_without_losing_the_selection():
    """An ambiguous date is still refused - 12/07 could be July or December -
    but it is dropped and flagged, not raised. Raising discarded the whole
    detection, which is how an Innominds onboarding mail and a digiverifier BGV
    invitation vanished in August. See tests/test_lifecycle_date_recovery.py."""
    row=valid_result();row['offer']['joining_date']='12/07/2026'
    validate_result(row,{'subject':'Selection update','body':'You have been selected for the role.'})
    assert row['offer']['joining_date'] is None
    assert 'UNREADABLE_OFFER_DATE_JOINING_DATE' in row['risk_flags']
    assert row['status']=='SELECTED'


@pytest.mark.parametrize(
    ("subject", "body", "attachments"),
    [
        ("Offer has been released", "Please review the terms.", []),
        ("Employment update", "Your offer has been successfully released to you.", []),
        (
            "Employment documents", "Please review the attached document.",
            [{
                "filename": "Gopichand_Offer_Letter.pdf",
                "mime_type": "application/pdf",
                "attachment_type": "OFFER_LETTER",
                "extraction_status": "EXTRACTED",
                "text": "This employment offer confirms your Software Engineer position.",
            }],
        ),
    ],
)
def test_offer_letter_subject_body_and_pdf_patterns_are_routed(subject, body, attachments):
    route = routing_decision(subject, body, attachments=attachments)
    assert route["send_to_ai"] is True
    assert route["context"]["status"] == "OFFER_LETTER_RECEIVED"


def test_unsupported_joining_prediction_with_disagreement_fails_closed():
    row = valid_result()
    row.update(
        status='JOINING_CONFIRMED', classification='joining_confirmed',
        candidate_status='Joining Confirmed', requires_manual_review=True,
        risk_flags=['MODEL_DISAGREEMENT'],
        model_validation={
            'agreed': False, 'primary_status': 'OFFER_APPROVED',
            'validator_status': 'JOINING_CONFIRMED',
        },
    )
    row['offer'].update(
        offer_detected=True, offer_letter_detected=True,
        offer_date='2026-08-24', joining_date='2026-09-14',
    )
    row['evidence'] = [{
        'source': 'EMAIL_BODY',
        'meaning': 'The offer has been successfully released to the candidate.',
        'text': 'The offer has been successfully released to the candidate.',
    }]
    message = {
        'subject': 'Offer has been released',
        'body': (
            'The offer has been successfully released to the candidate. '
            'The candidate is expected to join on 14 September 2026.'
        ),
    }
    attachments = [{
        'filename': 'Gopichand_Offer_Letter.pdf', 'mime_type': 'application/pdf',
        'attachment_type': 'OFFER_LETTER', 'extraction_status': 'FAILED',
        'text': '',
    }]

    validate_result(row, message, attachments)

    # Fails closed exactly as before -- no transition, no lifecycle event and
    # nothing a booking could act on. The unresolved disagreement now returns
    # for an automatic retry rather than to a person's queue.
    assert row['status'] == 'AI_RETRY_PENDING'
    assert row['classification'] == 'ai_retry_pending'
    assert row['should_create_review_record'] is False
    assert row['lifecycle_event'] == 'NONE'
    assert row['requires_manual_review'] is False
    assert row['backend_transition_validated'] is False
    assert row['backend_validation_reason'] == 'MODEL_DISAGREEMENT'


def test_central_recruitment_model_routes_use_required_defaults(monkeypatch):
    from core.ai_model_routing import configured_model_routes
    for name in ('OLLAMA_PRIMARY_MODEL','OLLAMA_MAIL_MODEL','AI_RECRUITMENT_MODEL','AI_RECRUITMENT_VALIDATOR_MODEL','AI_RECRUITMENT_FALLBACK_MODEL','OLLAMA_VISION_MODEL'):
        monkeypatch.delenv(name,raising=False)
    # Vision moved to qwen3-vl:8b-instruct when moondream was removed from the
    # project: it is the one vision-language model present on every Ollama node,
    # so a request cannot fail merely because of where it was scheduled.
    assert configured_model_routes() == {
        'recruitment_email_primary':'qwen2.5:7b',
        'recruitment_email_validator':'qwen2.5:7b',
        'recruitment_document_vision':'qwen3-vl:8b-instruct',
    }


def interview_result(status='INTERVIEW_CONFIRMED', classification='interview_confirmed'):
    row=valid_result();row.update(status=status,classification=classification,candidate_status='Interview Confirmed',confidence=96)
    row['interview'].update(date='2026-07-20',time='03:00 PM',timezone='Asia/Kolkata',mode='Online',round='L1',meeting_link='https://meet.test/room')
    row['evidence']=[{'source':'EMAIL_BODY','meaning':status,'text':'interview is scheduled for July 20, 2026 at 03:00 PM IST'}]
    return row


def test_interview_confidence_0_to_100_is_normalized_and_validated():
    row=interview_result()
    message={'subject':'Technical interview','body':'Your interview is scheduled for July 20, 2026 at 03:00 PM IST.'}
    validate_result(row,message)
    assert row['confidence']==.96
    assert row['classification']=='interview_confirmed'


def test_assertive_interview_overrides_contradictory_model_workflow_flag():
    row=interview_result()
    row.update(
        should_create_review_record=False,
        is_job_outcome=False,
        is_current_event=False,
        business_domain='SELECTION_TRACKING',
    )
    row['interview'].update(date='2026-07-28',time='14:00',timezone='IST')
    row['evidence']=[{
        'source':'EMAIL_BODY',
        'meaning':'INTERVIEW_CONFIRMED',
        'text':'Your interview is scheduled for 2026-07-28 02:00 PM IST.',
    }]
    message={
        'subject':'Interview Invitation - Charan Reddy M S',
        'body':'Hi, Charan Reddy M S! Your interview is scheduled for 2026-07-28 02:00 PM IST.',
        'sender_email':'aitalentquest@hexaware.com',
        'sent_at':datetime(2026,7,27,11,2,6,tzinfo=timezone.utc),
    }

    validate_result(row,message)

    assert row['status']=='INTERVIEW_CONFIRMED'
    assert row['classification']=='interview_confirmed'
    assert row['should_create_review_record'] is True
    assert row['is_job_outcome'] is True
    assert row['business_domain']=='INTERVIEW_TRACKING'
    assert row['interview']['date']=='2026-07-28'
    assert row['interview']['time']=='02:00 PM'
    assert row['interview']['timezone']=='Asia/Kolkata'
    assert row['validation_status']=='AUTO_VALIDATED'


def test_assertive_interview_overrides_speculative_joining_classification():
    row=valid_result()
    row.update(
        status='JOINING_CONFIRMED',
        classification='joining_confirmed',
        candidate_status='Joining Confirmed',
        lifecycle_event='JOINING_CONFIRMED',
        interview_event='INTERVIEW_CONFIRMED',
        business_domain='SELECTION_TRACKING',
        should_create_review_record=False,
        is_job_outcome=False,
    )
    row['interview'].update(date='2026-07-28',time='14:00',timezone='IST')
    row['evidence']=[{
        'source':'EMAIL_BODY',
        'meaning':'INTERVIEW_CONFIRMED',
        'text':'Your interview is scheduled for 2026-07-28 02:00 PM IST.',
    }]
    message={
        'subject':'Interview Invitation - Charan Reddy M S',
        'body':'Hi, Charan Reddy M S! Your interview is scheduled for 2026-07-28 02:00 PM IST.',
        'sender_email':'aitalentquest@hexaware.com',
        'sent_at':datetime(2026,7,27,11,2,6,tzinfo=timezone.utc),
    }

    validate_result(row,message)

    assert row['status']=='INTERVIEW_CONFIRMED'
    assert row['classification']=='interview_confirmed'
    assert row['candidate_status']=='Interview Confirmed'
    assert row['lifecycle_event']=='NONE'
    assert row['business_domain']=='INTERVIEW_TRACKING'
    assert row['should_create_review_record'] is True
    assert row['interview']['date']=='2026-07-28'
    assert row['interview']['time']=='02:00 PM'
    assert row['interview']['timezone']=='Asia/Kolkata'


# The "time" case moved out of this parametrize: a 24-hour model value is now
# recovered from a verbatim AM/PM time in the subject/evidence, and this
# fixture's body states "02:00 PM IST", so it is no longer a rejection case.
# The rejection is pinned instead by test_a_24h_time_with_no_am_pm_in_source_fails
# below, which strips the AM/PM from the source.
@pytest.mark.parametrize(("field","value","match"),[("date",None,"date"),("timezone",None,"timezone")])
def test_confirmed_interview_requires_explicit_schedule(field,value,match):
    """An incomplete schedule still never becomes a confirmed interview - but
    it is downgraded for review rather than discarded, which used to lose the
    mail entirely. See tests/test_lifecycle_date_recovery.py."""
    row=interview_result();row['interview'][field]=value
    message={'subject':'Technical interview','body':'Your interview is scheduled for July 20, 2026 at 03:00 PM IST.'}
    validate_result(row,message)
    assert row['classification']=='interview_update'
    assert row['requires_manual_review'] is False
    assert match in row['reason']


def test_shortlist_without_schedule_remains_informational():
    row=valid_result();row.update(status='INTERVIEW_SHORTLISTED',classification='interview_shortlisted',candidate_status='Interview Shortlisted')
    row['evidence']=[{'source':'EMAIL_BODY','meaning':'INTERVIEW_SHORTLISTED','text':'shortlisted for the next interview'}]
    validate_result(row,{'subject':'Update','body':'You have been shortlisted for the next interview. We will contact you later.'})
    assert row['classification']=='interview_shortlisted'


# Regression coverage for the "Ollama should not be required to reject
# obvious noise" fix. NAUKRI_WALKIN_* reproduces the exact production email
# (subject/body/sender) that was wrongly routed to the AI model and then,
# once Ollama was unreachable, wrongly forced into Needs Review.
NAUKRI_WALKIN_SUBJECT = "Reminder: Don’t Forget to attend these Walk-in's today"
NAUKRI_WALKIN_BODY = (
    "Reminder! Don’t forget to attend the walk-in job(s) you have applied to. "
    "Your walk-in reminder Urgent Opening DevOps Engineer Tcs 18 Jul walkin Interview "
    "Concepts Unlimited Date & time 2026-7-18, 9.00 AM - 11.30 AM Location Will share "
    "Be prepared to answer as well as ask questions to the recruiters. Team Naukri"
)


def test_deterministic_job_ad_is_not_routed_to_ai():
    """TEST 1: obvious deterministic noise must never reach the AI model."""
    route = routing_decision(NAUKRI_WALKIN_SUBJECT, NAUKRI_WALKIN_BODY, "", "reminder@naukri.com")
    assert route["send_to_ai"] is False


# Regression coverage for a follow-up finding: once the Needs Review filter
# worked, it surfaced real production noise the original fix missed — a bank
# transaction alert and two Naukri marketing emails, all pulled into AI by
# the ambiguous-recruitment fallback matching an isolated word ("offer" in a
# fraud-warning footer, "recruiter" in bulk marketing copy) anywhere in the
# email body.
def test_bank_transaction_alert_is_not_routed_to_ai():
    subject = "INR 13.00 was debited from your A/c no. XX2066."
    body = (
        "Dear Customer, here's the summary of your transaction: Amount Debited: "
        "INR 13.00 Account Number: XX2066 Transaction Info: UPI/P2M/127568896017. "
        "Regards, Axis Bank Ltd. RBI never deals with individuals for Savings "
        "Account, Current Account, Credit Card, Debit Card, etc. Don't be victim "
        "to such offers coming to you on phone or email in the name of RBI."
    )
    route = routing_decision(subject, body, "", "alerts@axis.bank.in")
    assert route["send_to_ai"] is False


def test_job_portal_recruiter_marketing_is_not_routed_to_ai():
    subject = "You have a new job in your inbox!"
    body = (
        "You have a job directly sent by recruiter! Apply to the jobs directly "
        "sent by recruiters. Also keep your profile updated to continue to get "
        "noticed by recruiters. Component Design Engineer Pimpri-Chinchwad."
    )
    route = routing_decision(subject, body, "", "donotreply_mailer@naukri.com")
    assert route["send_to_ai"] is False


def test_job_portal_safety_tips_are_not_routed_to_ai():
    subject = "How to make your job search safer"
    body = (
        "Keep yourself safe while searching for a job! Job scams are an "
        "unfortunate reality of the recruitment market. Beware of these common "
        "signs of fraud jobs. A job offer is a scam if you are asked to pay money."
    )
    route = routing_decision(subject, body, "", "info@naukri.com")
    assert route["send_to_ai"] is False


def test_ambiguous_cue_requires_a_whole_word_not_a_substring():
    """A plain `cue in text` check let "offer" match inside "offering" in an
    unrelated investment newsletter, forcing it through the AI pipeline."""
    subject = "New A- rated bond offering is now available"
    body = (
        "View the bond details and other important information. Update your "
        "email preferences here to choose the category of emails you wish to "
        "receive."
    )
    route = routing_decision(subject, body, "", "invest@stablebonds.in")
    assert route["send_to_ai"] is False


def test_genuine_offer_email_still_qualifies_for_ai():
    """The tightened word-boundary cue match must not lose real signal."""
    route = routing_decision(
        "Your Offer",
        "We are pleased to offer you the position of Software Engineer.",
        "", "hr@realcompany.invalid",
    )
    assert route["send_to_ai"] is True


def test_candidate_specific_walkin_confirmation_is_routed_to_ai():
    """TEST 2: a candidate-specific confirmed walk-in must still reach AI/validation."""
    route = routing_decision(
        "Walk-in interview confirmation",
        "Hi Aniket, your interview with ABC Technologies is confirmed today at 2 PM IST.",
        "ABC Technologies HR", "hr@abctechnologies.invalid",
    )
    assert route["send_to_ai"] is True


def test_ollama_down_and_obvious_ad_is_audit_only_not_retry_pending():
    """TEST 3: Ollama unavailable + obvious deterministic ad -> Audit Only, never Needs Review."""
    message = {
        "subject": NAUKRI_WALKIN_SUBJECT, "body": NAUKRI_WALKIN_BODY,
        "sender_email": "reminder@naukri.com", "sender_name": "",
        "recipient_email": "candidate@test.invalid", "sent_at": "2026-07-18T13:38:53Z",
    }
    exc = AIGatewayError("Ollama connection failed", code="OLLAMA_CONNECTION_FAILED")
    result = _failure_review_result(message, exc)
    assert result["is_selection_or_offer_related"] is False
    assert result["should_create_review_record"] is False
    assert result["primary_status"] == "IGNORED_NOT_OFFER_RELATED"
    assert result["classification"] == "not_relevant"
    assert result["ai_status"] == "NOT_REQUIRED"
    assert result["validation_status"] == "NOT_REQUIRED"
    assert result["lifecycle_event"] == "NONE"
    assert result["interview_event"] == "NONE"
    assert result["requires_manual_review"] is False


def test_ollama_down_and_ambiguous_recruitment_email_is_retry_pending():
    """TEST 4: Ollama unavailable + genuinely ambiguous content -> AI_RETRY_PENDING, no lifecycle/interview event."""
    message = {
        "subject": "Update on your application",
        "body": "We wanted to update you on the status of your recent application with our team.",
        "sender_email": "talent@employer.invalid", "sender_name": "Talent Team",
        "recipient_email": "candidate@test.invalid", "sent_at": "2026-07-18T13:38:53Z",
    }
    exc = AIGatewayError("Ollama connection failed", code="OLLAMA_CONNECTION_FAILED")
    result = _failure_review_result(message, exc)
    assert result["primary_status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["ai_status"] == "RETRY_PENDING"
    assert result["validation_status"] == "RETRY_PENDING"
    assert result["lifecycle_event"] == "NONE"


@pytest.mark.parametrize("subject", [
    "You’re now open to work - we can help you get noticed",
    "Aparna Kartik and others share their thoughts on LinkedIn",
    "Job application successful.",
    "You applied for 5 jobs on 17 Jul",
    "Reddy Charan, add Subarta Chandra as a contact",
])
def test_known_profile_network_and_application_noise_skips_ai(subject):
    route = routing_decision(
        subject, "This notification does not confirm selection, offer, or joining.",
        sender_email="messages-noreply@linkedin.com",
    )
    assert route["send_to_ai"] is False


def test_a_24h_time_with_no_recoverable_source_time_fails():
    """Recovery must never invent a time.

    The source carries no clock time at all, so neither the deterministic
    context (which otherwise converts a bare 24-hour source time to 12-hour,
    recruitment_semantics ~l.320) nor the subject/evidence recovery can supply
    one. The model's bare 17:00 must then be rejected exactly as before.
    """
    row=interview_result();row['interview']['time']='17:00'
    row['evidence']=[{
        'source':'EMAIL_BODY','meaning':'INTERVIEW_CONFIRMED',
        'text':'Your interview is scheduled for July 20, 2026.',
    }]
    message={'subject':'Technical interview',
             'body':'Your interview is scheduled for July 20, 2026. We will share the timing separately.'}
    validate_result(row,message)
    assert row['interview']['time'] is None, 'a time the source never stated was invented'
    assert row['classification']=='interview_update'


def test_a_source_stated_am_pm_time_is_recovered_from_a_24h_model_value():
    """The Altimetrik case: model reformatted to 24-hour, source stated AM/PM."""
    row=interview_result();row['interview']['time']='14:00 - 15:00'
    row['evidence']=[{'source':'EMAIL_BODY','meaning':'INTERVIEW_CONFIRMED',
                      'text':'Your interview is scheduled for July 20, 2026 at 02:00 PM IST.'}]
    message={'subject':'Interview scheduled on Fri, July 20, 2:00 PM - 3:00 PM IST',
             'body':'Your interview is scheduled for July 20, 2026 at 02:00 PM IST.'}
    validate_result(row,message)
    assert row['interview']['time']=='02:00 PM'
    assert row['backend_transition_validated'] is True


def test_selection_outcome_detection():
    route = routing_decision(
        "Selection Confirmation - Python Engineer",
        "Dear Candidate, Congratulations! We are pleased to inform you that you have been selected for the position of Python Engineer at ABC Corp.",
        sender_email="hr@abccorp.com",
    )
    assert route["send_to_ai"] is True
    assert route["context"]["status"] in {"SELECTED", "FINAL_SELECTION_CONFIRMED"}


def test_offer_received_outcome_detection():
    route = routing_decision(
        "Congratulations, You're in! Offer letter inside",
        "Hi Rohit, We are pleased to offer you employment at our company. Please review your attached offer letter.",
        sender_email="no-reply@kekamail.com",
    )
    assert route["send_to_ai"] is True
    assert route["context"]["status"] in {"OFFER_LETTER_RECEIVED", "OFFER_RECEIVED"}


def test_final_round_cleared_outcome_detection():
    route = routing_decision(
        "EY | L2 | React JS | Reddy Charan M S",
        "Hi Charan, Thank you for continued interest in working with EY India. We are pleased to inform you that you have successfully cleared the L1 round.",
        sender_email="Sunita.K@in.ey.com",
    )
    assert route["send_to_ai"] is True
    assert route["context"]["status"] == "FINAL_ROUND_CLEARED"


def test_hr_confirmation_and_documentation_outcome_detection():
    route = routing_decision(
        "CAPGEMINI DOCUMENATION",
        "Hi Poojitha, Minimal documents required for offer release: Please share 1. Pan card 2. Aadhar card 3. Degree certificate.",
        sender_email="rajat.b.rajat@capgemini.com",
    )
    assert route["send_to_ai"] is True
    assert route["context"]["status"] in {"HR_CONFIRMATION", "DOCUMENT_VERIFICATION"}


def test_joining_and_bgv_outcome_detection():
    route = routing_decision(
        "Invitation - Digital Employment BGV_RH30116125",
        "Dear Aniket, You have been invited to complete your digital employment background verification process.",
        sender_email="noreply@digiverifier.com",
    )
    assert route["send_to_ai"] is True
    assert route["context"]["status"] in {"BACKGROUND_VERIFICATION", "JOINING_CONFIRMED"}


def test_promotional_course_and_review_noise_is_ignored():
    route_course = routing_decision(
        "Still copy-pasting React code you don't fully understand?",
        "Namaste React Course - Learn React. Build Real Projects. Get Hired as a Frontend Developer.",
        sender_email="team@namastedev.com",
    )
    assert route_course["send_to_ai"] is False

    route_review = routing_decision(
        "Reviews & Salaries of Tata Consultancy Services, and Other Companies you Applied to",
        "Read reviews, salaries, and interview questions of companies you applied to.",
        sender_email="no-reply@ambitionbox.com",
    )
    assert route_review["send_to_ai"] is False
