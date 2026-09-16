import base64
import json

from features import ollama_payment_extract as extractor
from features import payment_verification_engine as engine


SUPER_MONEY_OCR = """
Payment Successful
₹10000
June 4 at 7:49 PM
To: B THRILOKNATH
thrilokn4@ybl
State Bank of India
From: ANIKET RANE
9000000108@superyes
UPI reference ID: 615527427709
"""


def test_amount_normalization_preserves_ten_thousand():
    assert extractor._normalize_amount_number("₹10,000") == 10000
    assert extractor._normalize_amount_number("10000") == 10000
    assert extractor._normalize_amount_number("INR 10,000.00") == 10000
    assert extractor._extract_amount_from_text(SUPER_MONEY_OCR) == 10000
    assert extractor._extract_utr_from_text(SUPER_MONEY_OCR) == "615527427709"
    assert extractor._extract_amount_from_text("Payment Successful\n\n210000\n\nJune 4 at 7:49 PM") == 10000


def test_super_money_receipt_uses_original_and_never_verifies_wrong_amount(monkeypatch):
    original_image = b"original-full-resolution-super-money-image"
    captured = {}
    raw_response = json.dumps(
        {
            "payment_status": "SUCCESS",
            "direction": "PAID_TO",
            "amount_minor": 100000,
            "currency": "INR",
            "sender_name": "ANIKET RANE",
            "receiver_name": "B THRILOKNATH",
            "receiver_upi_id": "thrilokn4@ybl",
            "utr": "615527427709",
            "provider": "super.money",
            "confidence": {"amount": 0.96, "receiver_name": 0.98, "utr": 0.98},
            "is_payment_screenshot": True,
        }
    )
    monkeypatch.setattr(extractor, "_is_ollama_available", lambda: True)
    monkeypatch.setattr(extractor, "ocr_enabled", lambda: True)
    monkeypatch.setattr(extractor, "_run_tesseract_ocr", lambda _data: SUPER_MONEY_OCR)

    def fake_vision(model_name, image_base64, prompt, *, timeout):
        captured["model"] = model_name
        captured["image"] = base64.b64decode(image_base64)
        captured["prompt"] = prompt
        return raw_response

    monkeypatch.setattr(extractor, "_call_vision_model", fake_vision)
    result = extractor.extract_payment_with_ollama(
        original_image, "image/png", allow_slow_ai=True, use_ocr=True
    )
    assert captured["image"] == original_image
    assert result["amount"] == 10000
    assert result["vision_amount"] == 1000
    assert result["ocr_amount"] == 10000
    assert result["utr_number"] == "615527427709"
    assert result["amount_crosscheck"] == "mismatch"
    assert "INR 10,000" in result["amount_mismatch_reason"]
    assert "INR 1,000" in result["amount_mismatch_reason"]
    assert result["_raw_model_response"] == raw_response


def test_amount_disagreement_is_needs_review_and_raw_response_is_audited(monkeypatch, tmp_path):
    ledger_path = tmp_path / "payment_ledger.json"
    registry_path = tmp_path / "receiver_registry.json"
    registry_path.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv("PAYMENT_VERIFICATION_LEDGER_FILE", str(ledger_path))
    monkeypatch.setenv("PAYMENT_RECEIVER_REGISTRY_FILE", str(registry_path))
    monkeypatch.setattr(engine, "payment_ocr_enabled", lambda: True)
    raw_response = '{"amount_minor":100000,"receiver_name":"B THRILOKNATH"}'
    monkeypatch.setattr(
        extractor,
        "extract_payment_with_ollama",
        lambda *_args, **_kwargs: {
            "amount": 10000,
            "vision_amount": 1000,
            "ocr_amount": 10000,
            "amount_mismatch_reason": "OCR amount INR 10,000 disagrees with vision amount INR 1,000.",
            "receiver_name": "B THRILOKNATH",
            "receiver_upi_id": "thrilokn4@ybl",
            "utr_number": "615527427709",
            "status": "success",
            "confidence_score": 98,
            "is_payment_screenshot": True,
            "_raw_model_response": raw_response,
        },
    )
    result = engine.verify_payment_screenshot(
        b"original-full-resolution-super-money-image",
        "image/png",
        source_module="candidate_payment_proof",
        expected_amount=5000,
        entity_id="candidate-1",
        candidate_id="candidate-1",
        purpose="candidate_payment",
    )
    assert result["amount"] == 10000
    assert result["deterministic_verified"] is False
    assert result["verification_state"] == "PENDING_MANUAL_REVIEW"
    assert "AMOUNT_SOURCE_MISMATCH" in result["reason_codes"]
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["evidence"][0]["raw_ollama_response"] == raw_response
    assert ledger["evidence"][0]["normalized_extraction"]["amount_mismatch_reason"] == "OCR amount INR 10,000 disagrees with vision amount INR 1,000."


def test_rupee_glyph_misread_does_not_create_a_phantom_conflict():
    """OCR reads the rupee sign as a 7, inventing 742,000 beside 42,000.

    A PhonePe receipt showing a single 42,000 was rejected with "OCR found
    conflicting visible amounts: INR 42,000, INR 742,000", blocking a valid
    referrer expense.
    """
    from features.ollama_payment_extract import _extract_amount_candidates_from_text

    text = "Paid to\n\u20b942,000\nDebited from\n742,000"
    assert sorted(set(_extract_amount_candidates_from_text(text))) == [42000]


def test_two_genuinely_marked_amounts_still_conflict():
    """The fix must not hide a real disagreement between two rupee amounts."""
    from features.ollama_payment_extract import _extract_amount_candidates_from_text

    text = "Paid \u20b942,000 and \u20b9742,000"
    assert sorted(set(_extract_amount_candidates_from_text(text))) == [42000, 742000]


def test_unrelated_bare_amounts_are_preserved():
    from features.ollama_payment_extract import _extract_amount_candidates_from_text

    text = "Paid \u20b942,000\nFee 5,500"
    assert sorted(set(_extract_amount_candidates_from_text(text))) == [5500, 42000]


def test_bare_seven_prefixed_amount_without_a_partner_is_kept():
    """Only drop the 7 when the same digits appeared with a currency marker."""
    from features.ollama_payment_extract import _extract_amount_candidates_from_text

    assert sorted(set(_extract_amount_candidates_from_text("Total 742,000"))) == [742000]
