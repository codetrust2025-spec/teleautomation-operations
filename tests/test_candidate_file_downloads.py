"""Resume and payment-proof files are saved under their own name and format.

The Edit Candidate modal has a Download button beside View for a resume and for
each payment proof. Download asks for the stored file as an attachment; the name
it is saved under comes from here: the name it was uploaded with, without any
folder part or control characters, and with an extension matching its type when
the uploaded name had none. View of a resume asks for the same file inline.
"""

from __future__ import annotations

from core.operations_api_helpers import (
    download_filename,
    resume_file_response,
    stored_file_response,
)


def test_the_uploaded_name_is_kept():
    assert download_filename({"original_name": "Asha_Rao_Resume.pdf", "mime_type": "application/pdf"}, "resume") == "Asha_Rao_Resume.pdf"
    assert download_filename({"original_name": "IMG_2041.JPG", "mime_type": "image/jpeg"}, "payment-proof") == "IMG_2041.JPG"


def test_a_folder_part_is_dropped():
    assert download_filename({"original_name": "C:\\Users\\asha\\Resume.pdf"}, "resume") == "Resume.pdf"
    assert download_filename({"original_name": "a/b/proof.png"}, "payment-proof") == "proof.png"


def test_a_name_without_extension_gets_the_one_its_type_has():
    assert download_filename({"original_name": "scan", "mime_type": "application/pdf"}, "resume") == "scan.pdf"
    assert download_filename({"original_name": "receipt", "mime_type": "image/png"}, "payment-proof") == "receipt.png"
    # No known type: the stored file's own extension.
    assert download_filename({"original_name": "receipt", "filename": "f00d.webp", "mime_type": ""}, "payment-proof") == "receipt.webp"


def test_no_uploaded_name_falls_back_to_the_stored_one_then_a_default():
    assert download_filename({"original_name": "", "filename": "abc123.jpeg"}, "payment-proof") == "abc123.jpeg"
    assert download_filename({"mime_type": "image/jpeg"}, "payment-proof") == "payment-proof.jpg"
    assert download_filename({}, "resume") == "resume"


def test_control_characters_are_removed():
    assert download_filename({"original_name": "pay\x01ment\x7f.png"}, "payment-proof") == "payment.png"


def _disposition(response) -> str:
    return response.headers["content-disposition"]


def test_download_is_an_attachment_and_view_is_inline(tmp_path):
    path = tmp_path / "stored.pdf"
    path.write_bytes(b"%PDF-1.4")
    entry = {"original_name": "Asha_Rao_Resume.pdf", "mime_type": "application/pdf"}
    saved = resume_file_response(str(path), entry)
    shown = resume_file_response(str(path), entry, inline=True)
    assert _disposition(saved) == 'attachment; filename="Asha_Rao_Resume.pdf"'
    assert _disposition(shown) == 'inline; filename="Asha_Rao_Resume.pdf"'
    assert saved.media_type == "application/pdf"


def test_a_non_ascii_name_is_encoded_instead_of_breaking_the_header(tmp_path):
    path = tmp_path / "stored.jpg"
    path.write_bytes(b"\xff\xd8")
    response = stored_file_response(str(path), {"original_name": "रसीद ₹20000.jpg", "mime_type": "image/jpeg"}, fallback="payment-proof")
    assert _disposition(response).startswith("attachment; filename*=utf-8''")
    response.headers["content-disposition"].encode("latin-1")  # a valid header value
    inline = resume_file_response(str(path), {"original_name": "बायोडाटा.pdf", "mime_type": "application/pdf"}, inline=True)
    assert _disposition(inline).startswith("inline; filename*=utf-8''")
