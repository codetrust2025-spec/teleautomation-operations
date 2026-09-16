"""Test data must be synthetic, because this repository is public.

The fixtures grew out of production: real candidates' Gmail addresses,
recruiters' mobile numbers, a resume's OCR text, a Teams invite carrying its
tenant and organiser ids. Each looked harmless on its own; together they were a
name, an address, a phone number and an interview time for identifiable people,
readable by anyone who cloned the repository.

These rules are the convention in docs/testing/synthetic-test-data.md, enforced.
They check the shape of an identifier, which is what a machine can check. A test
that genuinely needs a real value says so in ALLOWED, with the reason.

Names are pinned as digests rather than a list, because writing the list out
would put back exactly what this file exists to keep out.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCANNED = ("tests/**/*.py", "tests/**/*.json", "tests/**/*.html", "tests/**/*.txt",
           "dashboard/src/**/*.test.jsx", "dashboard/src/**/*.test.js",
           "dashboard/src/**/__fixtures__/*")

#: Consumer providers: an address at one of these is somebody's own mailbox.
CONSUMER_DOMAIN = re.compile(
    r"@(?:gmail|googlemail|yahoo|ymail|outlook|hotmail|live|msn|aol|icloud|"
    r"protonmail|proton|zoho|rediffmail|rediff)\.[A-Za-z.]{2,}$", re.I)
#: Reserved by RFC 2606/6761, so nothing sent there can reach a real person.
SAFE_MAIL_DOMAIN = re.compile(
    r"@(?:[A-Za-z0-9\-]+\.)*(?:example\.(?:com|org|net)|example|invalid|test|localhost)$",
    re.I)
#: Test numbers: the 90000 block this repository reserves, the classic 98765
#: placeholder, and the shapes older fixtures already used.
SAFE_MOBILE = re.compile(
    r"^(?:90000\d{5}|98765\d{5}|97000000\d{2}|98000000\d{2}|88899\d{5}|"
    r"91234\d{5}|98123\d{5}|9(\d)\1{5,}\d*)$")
#: A handle passes when its local part announces itself as test data, or when
#: it is a mask -- a receipt prints XXXXXX4573@ybl and the engine must match it.
SAFE_UPI_LOCAL = re.compile(
    r"^(?:[xX]+\d*|\d{3,4}|(?:test|example|sample|synthetic|fake|dummy|company|"
    r"referrer|payee|owner|sender|receiver|stranger|attacker|unknown|different|"
    r"someone|somebody|real|shared|first|second|acc|john|pawan|thrilok|ferrer|a|b)"
    r"[A-Za-z0-9._\-]*)$", re.I)
#: The company's own registered handle, the handle derived from its registered
#: phone, and the near-misses tests use to prove those two are not forgiving.
#: Not a third party's data: the same values are the payee configuration in
#: features/payment_verification_engine.py.
COMPANY_UPI_LOCAL = re.compile(r"^t?(?:ravi)?arvind[l0-9]*$|^jolluravinder\d*$", re.I)

MAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
MOBILE = re.compile(r"(?<![\w.])(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?![\w.])")
UPI = re.compile(r"\b([A-Za-z0-9.\-_]+)@(ybl|okaxis|oksbi|okhdfcbank|okicici|"
                 r"paytm|upi|ibl|axl|apl|yescred|examplebank|otherbank)\b")
#: Teams writes the tenant and the organiser into a join URL as GUIDs; Google
#: Calendar writes a 26-character event id. Both identify a real organisation.
GUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
#: A join URL, including the wrapped form a mail client leaves in a stored body.
MEETING_URL = re.compile(
    r"(?:teams\.microsoft\.com|meet\.google\.com|zoom\.us)/[^\s\"'<>]*"
    r"(?:[\s\\][^\s\"'<>]*%[^\s\"'<>]*)*")
CALENDAR_UID = re.compile(r"\b([0-9a-z]{18,})@(?:google\.com|group\.calendar\.google\.com)\b")

#: Real values a test is allowed to carry, each with the reason it has to be
#: real. Nothing personal to a third party belongs in here.
ALLOWED = {
    "8639074573": "the company's own payment phone, registered as its receiver",
    "careers@google.com": "a public recruiting address, quoted as a noise-rule example",
    "noreply@e.read.ai": "a vendor's public sending address",
    "noreply@clickup.com": "a vendor's public sending address",
}

#: sha256 of a lowercased word that was removed from the fixtures as personal
#: data. To block another name: hashlib.sha256(name.lower().encode()).hexdigest()
BLOCKED_NAME_DIGESTS = {
    "0554474af53bf82487c0db32a29cde18990c1a90d61c304bdeafc97c2b1d7920",
    "058fd9f8cee4fa52bdae20238a6b52b3254432bcce3eb9fea0bdebe624b818f1",
    "0584700e85572bc926475b64d47cff91c7802896c50ddf86021c91ab43667903",
    "096b6eb554a9083a0ebc4636d4492533875789ac6eb4bcd94c3c4ef8aefdc950",
    "0b94adc166e90806bbb2a17e484a938ff968ce7a9824d4b7fc70245af3cebdcb",
    "164b4a312f6b7793526877d67c4f65ea3e7204df60fb015157f0a86514b521c1",
    "17e8a6265fa646fd66ee3b6c8fbc61ebb121c481577eaedbad4652397d594fcb",
    "23fdf9ed25cb4f7b499cd78cf166fa6f07585ba4afea23cdf25076b6d2e93df2",
    "3599e7ef27c830c1f669f7563344ddc97b478ed095f79bde190f0941c39fc59d",
    "37404481724a642cedd515f5a960523c3d71732aadfe5e0bc12a32b6b936a7ed",
    "3e4b9e17be94a2b925e34fbfe8a121505a1131c5289fe62011ec0210214849f2",
    "48afbde946e49312118a0137d2db1b43e9741a0796e11ed616671c12cc70c4e4",
    "4840c6f06ab2618fd7d1eda83cbfea36c882eb2ae4221bff32594aa001bf7423",
    "4a65ededfc2238c9969279511d703349281d82e36a89640bd8c82b2d31abadc4",
    "4ac5160f5fae92e630a2e2d1bea6d8705fcfa6c4047f8af46e36e33a839e93a9",
    "526aad5371b7cea13e16bb10090d431adb40d8b56dedbcd487f42a989e15c871",
    "54af2c4339007bd9d8c3777b2d06991d72820eab876d302e10518068274b6b47",
    "56e5d97cbacd0f96c2695e4a959c769491c9b3b47bfc06cd86f8da3f533699a1",
    "58b6d77a2c557628385fad84e309775cca4c432c518626b5085233495698f5f3",
    "5960c7e5884f7f0fc77bf2487392b2b0bf06745bd8657b8a461a93b52935691e",
    "5cfaf80e0bd9ef685abb3acb7d51262daa4b414ec4c4185c1e4a96abf7d895de",
    "5d4dddab0efadccf3375cb053090167fa36912afc8c220673ebd276efe8195af",
    "614f72007b642f49a85c930d8a831b03970da6b18598ed433e039e8674634de8",
    "620a8e678e05249cfc22080189e37d8e2f56ec7a961db7ea735be0c1d7dc94f1",
    "6bffd440e60d996868d0108e727427c7cfa3cc9589c1a69612636150c16be312",
    "71e1dc7b6e2022cf3a07107b95402b05f986426597c7fa5674aa7e4da2b04232",
    "7e74c40d62c5df803f1c91b665b7a35524edafc94816f59c164bb3aad53938a9",
    "7eb091930d8b8c5440d1035086de414e879dc7c7150e9bf8e47c8318bde120b9",
    "80ab0f8ebd5e1b7c2f3807e18b66c1ad8036e89ca8342a6d471fd56f6569ab85",
    "941459c10323716b64c83dbf9c97c39c745c1aea60107f1b9cd998bd8337f08d",
    "a0609ec0a7c878a13ddd4649c20d58b393a102195109ee4573ad963779261546",
    "a0891babf15601b0e5bddbc3cf8027dfe755a81e938308ab072d09a6972669e1",
    "a1e8458955e491ee4a2b7d39323255a9d6bf7cef408c834359247506153876f9",
    "b67dfbcbdd3945ca5480e656cafe4cfd56e1d51595073d4f6aa32d6c139ebf85",
    "bfa8e05d7f47fab68c622d4835f9f1ffed8bcaad5a12b86a793f7e7ec8e1b4cd",
    "c20d26b77c1c7fb3461d23af63301c6cdc86924ea7950820881d8b6cdb9d8dbf",
    "c21b9a1acc50b607e4385326e9e4af9024c8dbb56e4858f58902e3d717d9fca9",
    "c7cef9264a4cfc5242e452347d9192528cdd24902d3aa7e04b46510c704097b2",
    "cc0785a6c2b2566e43d7c0819bb0b27883bc072217e95595e6bc258678bf8f3a",
    "d4747f94c3497bf109f34ce6fc937a722d8c9bb9289913ddfe4a74f990e193e1",
    "d6876f908483f35900890bf94e2790822af67bc78b0bdddc2c9a4c8a5efbbf41",
    "db4d11a7fcba1ede8e00a459d9c1774d3ecb994d8bd9cf61b020d4840dcc4896",
    "eb070bcd558c431247a91806eb17334b18d9bae30a33962857a66349e00ae2b1",
    "eb9d7ea809edb36cb5a539716e9af998858510534d86a23785f6e37ca376fd7d",
    "ed5ff82801be582c7fbb18d3af1909ece036aa841609eebf7df5a3c42d4595d5",
    "edefc5f0aa509539628391ed621bed45c6b06444818c824182569a664ccc7cab",
    "f082acd35b279d42da4c4845a9a3556b997190f6230b6ab37c6a5cb73163d3e6",
    "f598b4f57dfd23369d2bcd32f4ece846a8acdd4c1452aea11ac67a45ba0c9175",
    "fb075c765f8e2e8d82771cff7592a54cc12430afe5f6029422a63d8759e55d83",
    "fba202563ae47ddd93fd0b03d84932c2d98676e664778c08fbc3fa15156c5445",
    "fbcf19146f1d2e792b00e8d07ec533a48e6c32ddef3ce191af4d1996bf94c456",
    "fc1a8fc81824ab1600eda9dd118c6550b281ed3eedb2f9254df1f39c7d57caae",
    "fc902c216945282ab5949a0fd6ee5be177f5085d46651fb46647bac375069214",
}


def _digest(token: str) -> str:
    return hashlib.sha256(token.strip().lower().encode("utf-8")).hexdigest()


def _low_entropy(value: str) -> bool:
    """A generated id uses most of its alphabet; a hand-written one does not."""
    return len(set(value.replace("-", "").lower())) <= 8


def scanned_files() -> list[Path]:
    found: list[Path] = []
    for pattern in SCANNED:
        found += [p for p in ROOT.glob(pattern)
                  if p.is_file() and "__pycache__" not in p.parts
                  and p.name != Path(__file__).name]
    return sorted(set(found))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _at(path: Path, text: str, index: int) -> str:
    return f"{path.relative_to(ROOT).as_posix()}:{text[:index].count(chr(10)) + 1}"


def test_there_are_fixtures_to_check():
    """A guard that scans nothing passes for the wrong reason."""
    assert len(scanned_files()) > 200


def test_no_address_at_a_consumer_mail_provider():
    """An @gmail.com address in a fixture is a real person's mailbox."""
    offences = []
    for path in scanned_files():
        text = _read(path)
        for match in MAIL.finditer(text):
            value = match.group(0)
            if value in ALLOWED or not CONSUMER_DOMAIN.search(value):
                continue
            offences.append(f"{_at(path, text, match.start())}  {value}")
    assert not offences, (
        "personal mailboxes in test data -- use an @example.com address:\n  "
        + "\n  ".join(offences))


def test_every_mobile_number_is_from_a_test_range():
    offences = []
    for path in scanned_files():
        text = _read(path)
        for match in MOBILE.finditer(text):
            digits = re.sub(r"\D", "", match.group(0))[-10:]
            if digits in ALLOWED or SAFE_MOBILE.match(digits):
                continue
            offences.append(f"{_at(path, text, match.start())}  {match.group(0)}")
    assert not offences, (
        "real-looking mobile numbers -- use the 90000xxxxx block:\n  "
        + "\n  ".join(offences))


def test_every_payment_handle_is_synthetic_or_a_mask():
    offences = []
    for path in scanned_files():
        text = _read(path)
        for match in UPI.finditer(text):
            local = match.group(1)
            if (match.group(0) in ALLOWED or SAFE_UPI_LOCAL.match(local)
                    or COMPANY_UPI_LOCAL.match(local)):
                continue
            offences.append(f"{_at(path, text, match.start())}  {match.group(0)}")
    assert not offences, (
        "payment handles that are not obviously synthetic:\n  "
        + "\n  ".join(offences))


def test_no_meeting_carries_a_real_tenant_organiser_or_event():
    """A Teams join URL embeds a tenant and an organiser; a Google invite an
    event id. Both identify a real organisation and a real meeting.

    Only GUIDs written inside a meeting URL are checked. A bare GUID elsewhere
    in a fixture is a row id -- opaque, and not personal data.
    """
    offences = []
    for path in scanned_files():
        text = _read(path)
        for url in MEETING_URL.finditer(text):
            for match in GUID.finditer(url.group(0)):
                if match.group(0) in ALLOWED or _low_entropy(match.group(0)):
                    continue
                offences.append(f"{_at(path, text, url.start())}  {match.group(0)}")
        for match in CALENDAR_UID.finditer(text):
            local = match.group(1)
            if match.group(0) in ALLOWED or local.lower().startswith("synthetic"):
                continue
            if not _low_entropy(local):
                offences.append(f"{_at(path, text, match.start())}  {match.group(0)}")
    assert not offences, (
        "ids that may identify a real tenant, organiser or meeting -- use a "
        "00000000-1111-4222-8333-444444444444 shape, or a syntheticuid… "
        "calendar id:\n  " + "\n  ".join(offences))


def test_removed_names_have_not_come_back():
    offences = []
    for path in scanned_files():
        text = _read(path)
        for match in re.finditer(r"\b[A-Za-z][A-Za-z0-9]{3,}\b", text):
            if _digest(match.group(0)) in BLOCKED_NAME_DIGESTS:
                offences.append(_at(path, text, match.start()))
    assert not offences, (
        "a name removed from the fixtures as personal data is back at:\n  "
        + "\n  ".join(sorted(set(offences))))


@pytest.mark.parametrize("value,synthetic", [
    ("someone@gmail.com", False), ("someone@example.com", True),
    ("a.b@example.invalid", True), ("a.b@yahoo.co.in", False),
])
def test_the_address_rule_itself(value, synthetic):
    """A guard is only worth having if it rejects the shape it is aimed at."""
    assert bool(SAFE_MAIL_DOMAIN.search(value)) is synthetic
    assert bool(CONSUMER_DOMAIN.search(value)) is (not synthetic)


@pytest.mark.parametrize("digits,synthetic", [
    ("9000000101", True), ("9876543210", True), ("9111111111", True),
    ("9845303472", False), ("8328646540", False), ("7306994576", False),
])
def test_the_mobile_rule_itself(digits, synthetic):
    assert bool(SAFE_MOBILE.match(digits)) is synthetic


@pytest.mark.parametrize("value,synthetic", [
    ("00000000-1111-4222-8333-444444444444", True),
    ("404b1967-6507-45ab-8a6d-7374a3f478be", False),
    ("000000000000000000000001", True),
    ("6h71dqlrvrk041f0h0m2inrs95", False),
])
def test_the_identifier_entropy_rule_itself(value, synthetic):
    """A generated id spends its whole alphabet; a written one repeats itself."""
    assert _low_entropy(value) is synthetic


def test_a_calendar_id_may_also_say_it_is_synthetic_in_words():
    """syntheticuid… is readable, and readable beats a run of zeroes in a
    fixture someone has to understand later."""
    assert "syntheticuid000000000001ab".startswith("synthetic")
    assert not _low_entropy("syntheticuid000000000001ab")
