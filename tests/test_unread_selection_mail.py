"""A parked offer, joining or BGV mail reaches Selection Related.

11 Nov 2026: a VHS "Offer of Employment" with its offer letter, its revised
date-of-joining follow-up and a VHS BGV request were parked as
AI_PROCESSING_FAILED with no alert anywhere. The shapes below are theirs; the
people are invented.
"""
from core import recruitment_mail_store as store
from services import unread_selection_mail as sel


def mail(subject, body, sender="hr@vendor.example"):
    return {"id": "m1", "provider_message_id": "g1", "subject": subject, "body_text": body,
            "html_body_text": "", "sender_email": sender, "candidate_id": "c1"}


OFFER = mail("Vendor Professional_ Offer of Employment_Ravi Kumar_12 Nov 2026",
             "Hi Ravi Kumar, Congratulations! Further to your recent interview, we are pleased to offer you "
             "employment in Vendor Professional Services Pvt. Ltd. Your Date of Joining 12th Nov 2026.")
REVISED = mail("RE: Vendor Professional_ Offer of Employment_Ravi Kumar_12 Nov 2026",
               "Hi Ravi Kumar, Please ignore the previous email and consider today as your Date og Joiing, "
               "PFA offer with revised DOJ. Kindly share your acceptance on the revised offer.")
BGV = mail("BGV Form Completion and Required Documents Submission",
           "Hi Ravi, Please find attached BGV form. Kindly fill in all the required details.")


def test_the_three_vhs_shapes_are_surfaced():
    assert "an offer" in sel.selection_evidence(OFFER)
    assert "a joining arrangement" in sel.selection_evidence(OFFER)
    assert "an offer" in sel.selection_evidence(REVISED)  # the subject states it
    assert "a background check" in sel.selection_evidence(BGV)
    letter = sel.selection_evidence(REVISED, [{"filename": "Vendor Offer Letter_Ravi Kumar_11 Nov 2026.pdf"}])
    assert "an attached Vendor Offer Letter" in letter


def test_a_job_description_is_not_selection_news():
    kyndryl = mail("Client - Payroll Impact Infotech",
                   "Hello Ravi, Greetings! As discussed over call, kindly go through the details below. Kindly share "
                   "an acknowledgement if your skill set matches the JD. Manage changes in ServiceNow instance as BAU.")
    assert sel.selection_evidence(kyndryl) is None


def test_negated_and_conditional_wording_never_counts():
    disclaimer = mail("Interview Invitation",
                      "Your interview is on Friday. This communication does not constitute an offer of employment, "
                      "and no offer letter is implied.")
    conditional = mail("Next steps", "If selected, the offer letter will be shared with your date of joining.")
    assert sel.selection_evidence(disclaimer) is None
    assert sel.selection_evidence(conditional) is None


def test_quoted_history_and_job_boards_never_count():
    reply = mail("Re: Documents", "Thanks, received. On Tue, 10 Nov 2026 at 15:42, HR wrote: > Please find your "
                                  "offer letter attached with your date of joining.")
    assert sel.selection_evidence(reply) is None
    board = mail("Offer letter roles near you", "Apply now: roles with fast offer letter and joining date.",
                 sender="jobalerts-noreply@linkedin.com")
    assert sel.selection_evidence(board) is None


def test_it_is_a_selection_alert_that_reaches_selection_related():
    assert "selection_needs_reading" in store.CANONICAL_CLASSIFICATIONS
    assert store.notification_is_tracked("selection_needs_reading")
    assert "selection_needs_reading" in store.SELECTION_RELATED_CLASSIFICATIONS
    assert "selection_needs_reading" not in store.INTERVIEW_RELATED_CLASSIFICATIONS
    assert store._CLASSIFICATION_STATUS["selection_needs_reading"] == "Needs reading"


def test_the_pass_files_only_what_it_can_state(monkeypatch):
    kyndryl = mail("Client - Payroll", "Kindly share an acknowledgement if your skill set matches the JD.")
    filed = []
    monkeypatch.setattr(store, "unread_interview_candidates", lambda **_: [OFFER, kyndryl, BGV])
    monkeypatch.setattr(store, "attachments_for_message", lambda _id: [])
    monkeypatch.setattr(store, "record_unread_selection_alert",
                        lambda message, evidence: filed.append((message["subject"], evidence)) or {"id": message["subject"]})
    created = sel.surface_unread_selection_mail()
    assert [subject for subject, _ in filed] == [OFFER["subject"], BGV["subject"]]
    assert len(created) == 2


def test_codes_registrations_adverts_and_outreach_are_not_selection_news():
    for subject, body in (
        ("Verification Code", "Your code is 123456. Complete the BGV process after you sign in."),
        ("J2WOfferLetter - Thank you for registering", "Your profile on the Offer Letter portal is created."),
        ("Job Opening : Problem Management || Bangalore", "Selected candidates go through background verification and get an offer letter."),
        ("Mail Acknowledgement || Client", "Further to our discussion, we have an opening for ServiceNow projects. "
                                           "Our process includes a background check."),
    ):
        assert sel.selection_evidence(mail(subject, body)) is None, subject
