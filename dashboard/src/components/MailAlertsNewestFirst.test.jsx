/**
 * Mail Alerts is one list in Mail received order, newest first.
 *
 * The Selection view used to be grouped by candidate, so a candidate's older
 * mails sat above another candidate's newer one. Every view is now flat: the
 * server orders by Mail received (not Tool detected) and the screen draws
 * the rows exactly in that order, each mail its own row with its candidate.
 * Nothing is merged: two mails that share a candidate or a company are two
 * alerts.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MailMonitoringNotifications } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const row = (over = {}) => ({
  id: "n-1", candidate_id: "cand-1", candidate_name: "Aniket",
  candidate_email: "aniket.rane@example.com", company_name: "Innominds",
  classification: "offer_received", candidate_status: "Offer Received",
  email_subject: "Innominds offer has been released", ai_confidence: 0.95,
  email_received_at: "2026-08-24T12:46:00Z", created_at: "2026-08-28T14:29:00Z",
  is_read: true, is_reviewed: true, ...over,
});

// As the server returns them: Mail received, newest first, the two candidates
// interleaved. Aniket holds three mails, two of them from the same company.
const SELECTION_ROWS = [
  row({ id: "n-1", email_received_at: "2026-08-24T12:46:00Z" }),
  row({ id: "n-4", candidate_id: "cand-2", candidate_name: "Yamini Rohit",
        candidate_email: "rohit.sinha@example.com", company_name: "Onni Global",
        email_subject: "Rohit Yamani_Documents", email_received_at: "2026-08-24T10:00:00Z" }),
  row({ id: "n-2", email_received_at: "2026-08-23T09:00:00Z", classification: "joining_confirmed", candidate_status: "Joining Confirmed",
        email_subject: "please complete the pre-onboarding formalities",
        ai_confidence: 1, is_reviewed: false }),
  row({ id: "n-3", classification: "background_verification", candidate_status: "BGV",
        company_name: "Digiverifier", email_subject: "Invitation - Digital Employment BGV",
        ai_confidence: 0.85, is_reviewed: false, email_received_at: "2026-08-22T09:00:00Z" }),
];

function response(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }

let calls = [];
function install(notifications = SELECTION_ROWS, total = 2) {
  calls = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url) => {
    const href = String(url);
    calls.push(href);
    if (href.includes("/mail-monitoring/candidates")) return response({ status: "ok", candidates: [] });
    if (href.includes("/summary")) return response({ summary: { unread: 0 } });
    if (href.includes("/notifications")) return response({ notifications, total });
    return response({ status: "ok" });
  }));
}

const lastQuery = () => {
  // The table's own request; with a filter set each tab is also counted with
  // limit=1.
  const list = calls.filter((c) => c.includes("/mail-monitoring/notifications?") && !c.includes("limit=1&"));
  return list[list.length - 1] || "";
};

const renderScreen = () => render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);

async function showSelection() {
  renderScreen();
  const select = await screen.findByLabelText("Alert type filter");
  fireEvent.change(select, { target: { value: "selection" } });
  await waitFor(() => expect(lastQuery()).toContain("classification_group=selection"));
  return select;
}

const bodyRows = () => [...document.querySelectorAll("tr.mail-notification-row")];
const cells = (tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the list is in Mail received order", () => {
  beforeEach(() => install(SELECTION_ROWS, 4));

  it("asks for newest first and never groups, in any view", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    await waitFor(() => expect(lastQuery()).toContain("sort=newest"));
    for (const value of ["selection", "interview", ""]) {
      fireEvent.change(select, { target: { value } });
      await waitFor(() => expect(lastQuery().includes("classification_group=")).toBe(Boolean(value)));
      expect(lastQuery()).toContain("sort=newest");
      expect(lastQuery()).not.toContain("group_by");
    }
    expect(calls.some((c) => c.includes("group_by"))).toBe(false);
  });

  it("asks for newest first on every page", async () => {
    install(SELECTION_ROWS, 45);
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastQuery()).toContain("offset=20"));
    expect(lastQuery()).toContain("sort=newest");
    expect(screen.getByText(/45\s+notifications/)).toBeTruthy();
  });

  it("draws the rows exactly in the server's order, candidates interleaved", async () => {
    await showSelection();
    await waitFor(() => expect(bodyRows().length).toBe(4));
    expect(bodyRows().map((tr) => cells(tr)[0])).toEqual([
      "Aniketaniket.rane@example.com",
      "Yamini Rohitrohit.sinha@example.com",
      "Aniketaniket.rane@example.com",
      "Aniketaniket.rane@example.com",
    ]);
    expect(document.querySelectorAll("tbody.mail-group, .mail-group__head").length).toBe(0);
  });
});

describe("every mail stays its own row", () => {
  beforeEach(() => install(SELECTION_ROWS, 4));

  it("preserves each mail's own company, status, subject and confidence", async () => {
    await showSelection();
    await waitFor(() => expect(bodyRows().length).toBe(4));
    const rows = bodyRows().map(cells);
    expect(rows.map((c) => c[1])).toEqual(["Innominds", "Onni Global", "Innominds", "Digiverifier"]);
    expect(rows.map((c) => c[2])).toEqual(["Offer Received", "Offer Received", "Joining Confirmed", "BGV"]);
    expect(rows.map((c) => c[4])).toEqual(["95%", "95%", "100%", "85%"]);
  });

  it("keeps the per-mail actions and opens the mail that was clicked", async () => {
    await showSelection();
    await waitFor(() => expect(bodyRows().length).toBe(4));
    for (const tr of bodyRows()) {
      expect(within(tr).getByRole("button", { name: "Open alert" })).toBeTruthy();
      expect(within(tr).getByRole("button", { name: "Dismiss alert" })).toBeTruthy();
    }
    fireEvent.click(within(bodyRows()[3]).getByRole("button", { name: "Open alert" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(within(screen.getByRole("dialog")).getByText("Invitation - Digital Employment BGV")).toBeTruthy();
  });
});
