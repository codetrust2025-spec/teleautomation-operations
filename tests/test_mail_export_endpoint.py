"""Unit tests for the recruitment mail export endpoint and export module."""
import json
import zipfile
import io
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from core.recruitment_mail_export import (
    build_xlsx_bytes,
    records_to_xlsx,
    records_to_json_payload,
    EXCEL_EXPORT_COLUMNS,
)

SAMPLE_RECORDS = [
    {
        "notification_id": 101,
        "candidate_id": "cand-001",
        "candidate_name": "Alice Smith",
        "candidate_email": "alice@example.com",
        "company_name": "Acme Corp",
        "job_role": "Software Engineer",
        "classification": "selected",
        "candidate_status": "Selected",
        "ai_confidence": 0.95,
        "email_subject": "Congratulations & Offer Letter",
        "sender_name": "HR Team",
        "sender_email": "hr@acme.com",
        "recipient_email": "alice@example.com",
        "email_received_at": "2026-10-28T10:00:00Z",
        "detected_at": "2026-10-28T10:01:00Z",
        "priority": "normal",
        "is_read": False,
        "booking_id": "bk-123",
        "booking_status": "booked",
        "block_reason": None,
        "block_reason_code": None,
        "meeting_link": "https://meet.google.com/xyz",
        "interview_starts_at": "2026-11-01T14:00:00Z",
        "interview_ends_at": "2026-11-01T15:00:00Z",
        "gmail_message_id": "msg_001",
        "gmail_thread_id": "th_001",
        "mailbox_message_id": 501,
        "message_sent_at": "2026-10-28T09:59:00Z",
        "rfc_message_id": "<abc@acme.com>",
        "email_body": "Dear Alice,\n\nWe are pleased to offer you the role of Software Engineer.\n\nBest regards,\nAcme HR",
        "attachments": [
            {"filename": "Offer_Letter.pdf", "mime_type": "application/pdf", "size_bytes": 102400}
        ],
    },
    {
        "notification_id": 102,
        "candidate_id": "cand-002",
        "candidate_name": "Bob Jones",
        "candidate_email": "bob@example.com",
        "company_name": "Globex",
        "job_role": None,
        "classification": "interview_invitation",
        "candidate_status": "Interview Scheduled",
        "ai_confidence": 0.88,
        "email_subject": "Invitation for Interview <Technical Round & Discussion>",
        "sender_name": "Recruiting",
        "sender_email": "jobs@globex.com",
        "recipient_email": "bob@example.com",
        "email_received_at": "2026-10-29T11:00:00Z",
        "detected_at": "2026-10-29T11:02:00Z",
        "priority": "high",
        "is_read": True,
        "booking_id": None,
        "booking_status": None,
        "block_reason": None,
        "block_reason_code": None,
        "meeting_link": None,
        "interview_starts_at": None,
        "interview_ends_at": None,
        "gmail_message_id": "msg_002",
        "gmail_thread_id": "th_002",
        "mailbox_message_id": 502,
        "message_sent_at": "2026-10-29T10:58:00Z",
        "rfc_message_id": "<def@globex.com>",
        "email_body": "Hello Bob, please join us on Tuesday.",
        "attachments": [],
    },
]


def test_build_xlsx_bytes_valid_zip():
    headers = ["ID", "Subject", "Body"]
    rows = [["1", "Test & Subject", "Line 1\nLine 2 <special>"]]
    xlsx_bytes = build_xlsx_bytes(headers, rows)
    assert isinstance(xlsx_bytes, bytes)
    assert len(xlsx_bytes) > 0

    # Ensure it is a valid zip containing required OpenXML parts
    zf = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    namelist = zf.namelist()
    assert "[Content_Types].xml" in namelist
    assert "xl/workbook.xml" in namelist
    assert "xl/worksheets/sheet1.xml" in namelist

    # Inspect sheet xml
    sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Test &amp; Subject" in sheet_xml
    assert "Line 1\nLine 2 &lt;special&gt;" in sheet_xml


def test_records_to_xlsx():
    xlsx_bytes = records_to_xlsx(SAMPLE_RECORDS)
    assert len(xlsx_bytes) > 0
    zf = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
    sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Alice Smith" in sheet_xml
    assert "Bob Jones" in sheet_xml
    assert "Offer_Letter.pdf" in sheet_xml
    assert "Dear Alice" in sheet_xml


def test_records_to_json_payload():
    payload = records_to_json_payload(SAMPLE_RECORDS, {"search": "engineer"})
    assert payload["total_records"] == 2
    assert payload["filters_applied"] == {"search": "engineer"}
    assert len(payload["records"]) == 2
    rec1 = payload["records"][0]
    assert rec1["notification_id"] == 101
    assert rec1["candidate"]["name"] == "Alice Smith"
    assert rec1["attachments"][0]["filename"] == "Offer_Letter.pdf"
    # Ensure sensitive fields are excluded
    assert "token" not in rec1
    assert "password" not in rec1


@patch("core.recruitment_mail_api.enabled", return_value=True)
@patch("core.recruitment_mail_api.require_fleet_admin")
@patch("core.recruitment_mail_store.export_notifications")
def test_export_endpoint_xlsx(mock_export, mock_auth, mock_enabled):
    mock_export.return_value = SAMPLE_RECORDS
    client = TestClient(app)
    response = client.get("/api/mail-monitoring/export?format=xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "mail_alerts_" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.xlsx"')
    # Validate payload is a valid zip
    zf = zipfile.ZipFile(io.BytesIO(response.content))
    assert "xl/worksheets/sheet1.xml" in zf.namelist()


@patch("core.recruitment_mail_api.enabled", return_value=True)
@patch("core.recruitment_mail_api.require_fleet_admin")
@patch("core.recruitment_mail_store.export_notifications")
def test_export_endpoint_json(mock_export, mock_auth, mock_enabled):
    mock_export.return_value = SAMPLE_RECORDS
    client = TestClient(app)
    response = client.get("/api/mail-monitoring/export?format=json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert "mail_alerts_" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.json"')
    data = response.json()
    assert data["total_records"] == 2
    assert len(data["records"]) == 2
    assert data["records"][0]["notification_id"] == 101


@patch("core.recruitment_mail_api.enabled", return_value=True)
@patch("core.recruitment_mail_api.require_fleet_admin")
@patch("core.recruitment_mail_store.list_notifications")
@patch("core.recruitment_mail_store.count_notifications")
def test_notifications_endpoint_returns_email_count(mock_count, mock_list, mock_auth, mock_enabled):
    mock_list.return_value = ([{"id": 1, "company_name": "Test"}], 1)
    mock_count.return_value = 5
    client = TestClient(app)

    # Flat view
    res = client.get("/api/mail-monitoring/notifications")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["email_count"] == 1

    # Grouped view
    res_grouped = client.get("/api/mail-monitoring/notifications?group_by=candidate")
    assert res_grouped.status_code == 200
    data_grouped = res_grouped.json()
    assert data_grouped["total"] == 1
    assert data_grouped["email_count"] == 5
