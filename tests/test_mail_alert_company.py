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
