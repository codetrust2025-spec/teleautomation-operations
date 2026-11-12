"""Interview Related: the company of an alert stored without one, from its own evidence only.

Cases are the shapes production mail takes (12 Nov 2026), with invented
people. Each resolves from evidence or stays empty: nothing is guessed from a
platform's or an agency's domain, a meeting title, or the candidate's name.
"""
from core.mail_alert_company import Evidence, name_matches_domain, resolve

KNOWN = [
    ("hr@hcltech.com", "HCLTech"),
    ("talent@hcltech.com", "HCL Technologies Limited"),
    ("r@tigeranalytics.com", "Tiger Analytics"),
    ("x@hexaware.com", "Hexaware Technologies"),
    ("a@accenture.com", "Accenture"),
    ("m@aerial.co.in", "Accenture"),  # an agency sending for a client...
    ("n@aerial.co.in", "Aerial Telecom Solutions"),  # ...and for itself
    ("no-reply@flocareer.com", "Zemoso Labs"),  # a platform carries many employers
    ("hello@altimetrik.com", "Altimetrik"),
]
EV = Evidence(KNOWN, booking_companies=["Brillio"])


def alert(subject, sender, name="Ravi Kumar"):
    return {"candidate_name": name, "sender_email": sender, "email_subject": subject}


def test_the_booking_company_comes_first():
    assert resolve(alert("Tech 1 Interview || Ravi Kumar || Aws", "talent@dxc.com"), EV,
                   booking_company="DXC Technology") == ("DXC Technology", "booking")


def test_a_booking_company_that_is_not_one_is_ignored():
    # The candidate's own name or a meeting title typed into the booking is no company.
    assert resolve(alert("Round 1", "x@unknown-mail.com"), EV, booking_company="Ravi Kumar") == ("", "")
    assert resolve(alert("Round 1", "x@unknown-mail.com"), EV, booking_company="L1 Interview") == ("", "")


def test_one_company_in_the_same_thread():
    assert resolve(alert("RE: Virtual Technical Interview", "p@vhspro.net"), EV,
                   thread_companies=["VHS Professional Services Pvt Ltd"]) == ("VHS Professional Services Pvt Ltd", "thread")
    # Two different companies in the thread prove neither.
    assert resolve(alert("RE: Interview", "p@unknown-mail.com"), EV, thread_companies=["Infosys", "Wipro"]) == ("", "")


def test_the_employers_own_domain():
    assert resolve(alert("Technical Round 2 interview scheduled", "recruitment@tigeranalytics.com"), EV) == ("Tiger Analytics", "sender")
    # Name variants that all match the domain are one employer.
    assert resolve(alert("Ravi Kumar", "hr-team@hcltech.com"), EV)[1] == "sender"


def test_a_domain_that_names_several_employers_or_is_a_platform_names_none():
    assert EV.domain_company("aerial.co.in") == ""
    assert EV.domain_company("flocareer.com") == ""
    assert EV.domain_company("gmail.com") == ""
    assert not name_matches_domain("Zemoso Labs", "flocareer.com")


def test_a_company_the_subject_states_and_the_data_confirms():
    assert resolve(alert("Interview Scheduled with Hexaware Technologies", "assistant@glider.ai"), EV) == ("Hexaware Technologies", "subject")
    assert resolve(alert("Altimetrik - Interview Schedule", "admin@hirepro.in"), EV) == ("Altimetrik", "subject")
    assert resolve(alert("Interview || Accenture  || || 6/11/2026  3.00 PM ||", "m@aerial.co.in"), EV) == ("Accenture", "subject")
    assert resolve(alert("Interview with Brillio", "v@brillio.com"), EV) == ("Brillio", "subject")
    # Stated and confirmed by the sender's own domain, though nobody named it before.
    assert resolve(alert("Canceled: Interview Invite-Video call- WinWire Technologies", "careers@winwire.com"), EV) == ("WinWire Technologies", "subject")


def test_an_unconfirmed_or_personal_name_in_the_subject_is_not_a_company():
    # A name only the subject states, from a platform domain, is not confirmed.
    assert resolve(alert("Interview scheduled with Northwind Labs on Thu", "noreply@ripplehire.com"), EV) == ("", "")
    # The candidate is not the company.
    assert resolve(alert("Technical Interview with RAVI KUMAR || Full Stack", "k@unknown-mail.com"), EV) == ("", "")
    assert resolve(alert("L1 Technical interview - Ravi", "d@unknown-mail.com"), EV) == ("", "")


def test_a_company_the_body_states_and_the_data_confirms():
    body = "Dear Ravi,\nYour interview with Tiger Analytics is confirmed for Friday.\nRegards"
    assert resolve(alert("L1 Interview | Ravi Kumar | P-104831", "noreply@veriklick.com"), EV, body=body) == ("Tiger Analytics", "email body")
    unknown = "Your interview with Contoso Widgets is confirmed."
    assert resolve(alert("L1 Interview | Ravi Kumar", "noreply@veriklick.com"), EV, body=unknown) == ("", "")


def test_nothing_proves_a_company_so_none_is_shown():
    assert resolve(alert("Interview for Senior Full Stack Software Developer (React, Python) job", "x@inbound.workablemail.com"), EV) == ("", "")


def sent(subject, sender, sender_name="", name="Ravi Kumar"):
    return {"candidate_name": name, "sender_email": sender, "sender_name": sender_name, "email_subject": subject}


def test_a_forwarded_mail_counts_its_original_senders_domain():
    body = ("---------- Forwarded message ---------\nFrom: Asha Rao < asha-rao@hcltech.com >\n"
            "Date: Wed, 7 Oct 2026\nSubject: Interview with HCL TECH_Ravi Kumar\n")
    assert resolve(sent("Fwd: Interview with HCL TECH_Ravi Kumar", "ravi.kumar@example.com"), EV, body=body) == ("HCLTech", "forwarded sender")
    # A forwarded platform mail proves nothing.
    platform = "---------- Forwarded message ---------\nFrom: Jobs < no-reply@naukri.com >\n"
    assert resolve(sent("Fwd: Interview", "ravi.kumar@example.com"), EV, body=platform) == ("", "")


def test_a_job_board_relay_carries_the_recruiters_own_domain():
    from core.mail_alert_company import relay_domain
    relay = "vijay.bm9ydGh3aW5kLmNvLmlu@naukri.com"  # base64 of northwind.co.in
    assert relay_domain(relay) == "northwind.co.in"
    assert relay_domain("reminder@naukri.com") == ""
    assert relay_domain("someone.c3RyYXRvc3BoZXJl@example.com") == ""  # only a job board relays
    assert resolve(sent("Walk-in interview | Test Engineer", relay, "Northwind It Services"), EV) == ("Northwind It Services", "job board relay")
    # A display name that is not the relayed domain's company proves nothing.
    assert resolve(sent("Walk-in interview | Test Engineer", relay, "Contoso Staffing"), EV) == ("", "")


def test_an_ats_subject_and_body_that_state_the_same_employer():
    subject = "Reminder: Interview for Senior Full Stack Software Developer (React, Python) job at Accellor"
    body = "Dear Ravi,\nGreetings from Accellor! Your technical discussion has been scheduled."
    assert resolve(sent(subject, "4f37gj@inbound.workablemail.com", "Asha Rao"), EV, body=body) == ("Accellor", "subject")
    # The subject alone, from a platform, is not enough.
    assert resolve(sent(subject, "4f37gj@inbound.workablemail.com", "Asha Rao"), EV, body="See you then.") == ("", "")
    flo = "Your job application for the opportunity 'Specialist, SRE Engineer - Asha Rao' for 'EY' is making progress!"
    assert resolve(sent("Interview Scheduled for Specialist, SRE Engineer - Asha Rao at EY", "no-reply@flocareer.com"), EV,
                   body=flo) == ("EY", "subject")


def test_a_person_stated_in_both_subject_and_body_is_still_no_company():
    # "interview with X" names people too, so agreement there proves nothing.
    assert resolve(sent("Interview with Asha Rao", "k@unknown-mail.com"), EV, body="Your interview with Asha Rao is confirmed.") == ("", "")


def test_a_body_name_confirmed_by_the_senders_own_domain():
    body = "Hi Ravi, Your interview for the post of Fullstack Software Developer at Infocepts is cancelled."
    assert resolve(sent("Interview cancelled for the post of Fullstack Software Developer", "career@infocepts.com"), EV,
                   body=body) == ("Infocepts", "email body")
    # Or by a link in the mail itself.
    linked = "Your interview with Contoso Widgets is confirmed. Company website: https://www.contosowidgets.com/about"
    assert resolve(sent("L1 Interview | Ravi Kumar", "noreply@veriklick.com"), EV, body=linked) == ("Contoso Widgets", "email body")


def test_a_subject_naming_a_known_employer_before_interview():
    assert resolve(sent("Altimetrik  India Interview - Ravi Kumar | Java | 9/10", "alerts@risebird.io"), EV) == ("Altimetrik", "subject")
    # Only the whole known name counts: "Hexaware" is not "Hexaware Technologies".
    assert resolve(sent("Hexaware Interview - Ravi Kumar", "alerts@risebird.io"), EV) == ("", "")
    assert resolve(sent("You've Scheduled Your Accenture Interview for the Bank Project!", "x@karat.io"), EV) == ("Accenture", "subject")
    # A title before "Interview" is no employer.
    assert resolve(sent("Sr. Platform Engineer L1 interview with Ravi Kumar @ 11.30 AM", "r@unknown-mail.com"), EV) == ("", "")


def test_a_walk_in_reminder_names_a_known_employer():
    body = "Your walk-in reminder Automation Tester WALK IN Interview in Hyderabad on 31st Oct Accenture Date & time 2026-10-31"
    assert resolve(sent("Reminder: Don't Forget to attend these Walk-in's today", "reminder@naukri.com", "reminder"), EV,
                   body=body) == ("Accenture", "email body")


def test_a_platform_senders_display_name_that_is_a_known_employer():
    assert resolve(sent("Interview for Candidate: [not available]", "x@myworkday.com", "Altimetrik"), EV) == ("Altimetrik", "sender name")
    # A person's display name is not a company.
    assert resolve(sent("Interview for Candidate: [not available]", "x@myworkday.com", "Asha Rao"), EV) == ("", "")


def test_a_relay_marker_character_and_the_mails_own_capitals():
    from core.mail_alert_company import relay_domain
    assert relay_domain("vijay.sbm9ydGh3aW5kLmNvLmlu@naukri.com") == "northwind.co.in"
    ev = Evidence([("a@x-staffing.com", "kpmg"), ("b@y-staffing.com", "kpmg")])
    # The data has it only in lower case; the mail's own capitals are kept.
    assert resolve(sent("KPMG L1 Interview: Azure Data Engineer || Saturday", "t@tdstaffing.com"), ev) == ("KPMG", "subject")
