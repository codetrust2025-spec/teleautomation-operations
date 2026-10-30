import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MailMonitoringNotifications } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() {}
}

const SAMPLE_NOTIFICATIONS = [
  {
    id: "notif-1",
    candidate_id: "cand-1",
    candidate_name: "Aarav Sharma",
    company_name: "TCS",
    classification: "job_selection_confirmed",
    candidate_status: "Selected",
    priority: "high",
    email_subject: "Offer Letter",
    sender_name: "TCS HR",
    is_read: false,
    ai_confidence: 0.95,
  },
];

let fetchCalls = [];

function setupFetchMock({ notifications = SAMPLE_NOTIFICATIONS, total = 42, email_count = 42 } = {}) {
  fetchCalls = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn((url, init = {}) => {
      const href = String(url);
      fetchCalls.push({ href, method: init.method || "GET", init });

      if (href.includes("/api/mail-monitoring/export")) {
        const isJson = href.includes("format=json");
        const bodyContent = isJson ? JSON.stringify({ total_records: email_count, records: [] }) : "mock-xlsx-bytes";
        return Promise.resolve({
          ok: true,
          status: 200,
          headers: new Headers({
            "Content-Type": isJson ? "application/json" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": `attachment; filename="mail_alerts_20261030_120000.${isJson ? "json" : "xlsx"}"`,
          }),
          blob: () => Promise.resolve(new Blob([bodyContent])),
          json: () => Promise.resolve(JSON.parse(bodyContent)),
        });
      }

      if (href.includes("/mail-monitoring/candidates")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) });
      }

      if (href.includes("/mail-monitoring/summary")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ summary: { unread: 5, ai_retry_pending: 0, visible_total: total } }),
        });
      }

      if (href.includes("/mail-monitoring/notifications")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ notifications, total, email_count }),
        });
      }

      return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok" }) });
    })
  );
}

beforeEach(() => {
  setupFetchMock();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderView = () =>
  render(
    <ConfirmProvider>
      <MailMonitoringNotifications />
    </ConfirmProvider>
  );

describe("Mail Alerts Download Button", () => {
  it("renders download button with exact matching record count", async () => {
    renderView();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Download 42 Mails" })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Download as JSON" })).toBeInTheDocument();
  });

  it("triggers xlsx download with active filters on click", async () => {
    renderView();
    const downloadBtn = await screen.findByRole("button", { name: "Download 42 Mails" });
    expect(downloadBtn).toBeEnabled();

    // Mock URL.createObjectURL and revokeObjectURL
    const createObjectURLMock = vi.fn(() => "blob:http://localhost/mock-blob");
    const revokeObjectURLMock = vi.fn();
    window.URL.createObjectURL = createObjectURLMock;
    window.URL.revokeObjectURL = revokeObjectURLMock;

    await act(async () => {
      fireEvent.click(downloadBtn);
    });

    const exportCalls = fetchCalls.filter((c) => c.href.includes("/api/mail-monitoring/export"));
    expect(exportCalls).toHaveLength(1);
    expect(exportCalls[0].href).toContain("format=xlsx");
    expect(exportCalls[0].href).toContain("is_read=false"); // unread tab is default
  });

  it("triggers json download with active filters on clicking JSON button", async () => {
    renderView();
    const jsonBtn = await screen.findByRole("button", { name: "Download as JSON" });
    expect(jsonBtn).toBeEnabled();

    window.URL.createObjectURL = vi.fn(() => "blob:http://localhost/mock-blob");
    window.URL.revokeObjectURL = vi.fn();

    await act(async () => {
      fireEvent.click(jsonBtn);
    });

    const exportCalls = fetchCalls.filter((c) => c.href.includes("/api/mail-monitoring/export"));
    expect(exportCalls).toHaveLength(1);
    expect(exportCalls[0].href).toContain("format=json");
    expect(exportCalls[0].href).toContain("is_read=false");
  });

  it("disables download buttons when matching email count is 0", async () => {
    setupFetchMock({ notifications: [], total: 0, email_count: 0 });
    renderView();
    await waitFor(() => {
      const downloadBtn = screen.getByRole("button", { name: "Download 0 Mails" });
      expect(downloadBtn).toBeDisabled();
      const jsonBtn = screen.getByRole("button", { name: "Download as JSON" });
      expect(jsonBtn).toBeDisabled();
    });
  });
});
