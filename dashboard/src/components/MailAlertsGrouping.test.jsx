/**
 * Selection Related, grouped by candidate.
 *
 * The flat table repeated a candidate's name once per mail, so a candidate with
 * an offer, a joining confirmation and a BGV read as three unrelated alerts.
 * The view now shows one parent per candidate with those mails underneath.
 *
 * Two things are asserted here that a screenshot would not show:
 *
 * 1. The grouping is paged on the server (`group_by=candidate`). Grouping the
 *    current page in the browser would split a candidate across two pages and
 *    draw them as two parents, which is what the grouping exists to prevent.
 * 2. No mail is lost on its way into a group. Grouping must never behave like
 *    a deduplicator - two mails that share a candidate, a company or a subject
 *    are two alerts, and each keeps its own status, confidence, review state
 *    and actions.
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

// Two candidates; Aniket holds three selection mails, two of them sharing a
// company. Grouping must keep all three.
const SELECTION_ROWS = [
  row({ id: "n-1" }),
  row({ id: "n-2", classification: "joining_confirmed", candidate_status: "Joining Confirmed",
        email_subject: "please complete the pre-onboarding formalities",
        ai_confidence: 1, is_reviewed: false }),
  row({ id: "n-3", classification: "background_verification", candidate_status: "BGV",
        company_name: "Digiverifier", email_subject: "Invitation - Digital Employment BGV",
        ai_confidence: 0.85, is_reviewed: false }),
  row({ id: "n-4", candidate_id: "cand-2", candidate_name: "Yamini Rohit",
        candidate_email: "rohit.sinha@example.com", company_name: "Onni Global",
        email_subject: "Rohit Yamani_Documents" }),
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
  const list = calls.filter((c) => c.includes("/mail-monitoring/notifications?"));
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

const groups = () => document.querySelectorAll("tbody.mail-group");
const headings = () => [...document.querySelectorAll(".mail-group__head strong")].map((n) => n.textContent);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the Selection view pages by candidate", () => {
  beforeEach(() => install());

  it("asks the server to group, so a candidate cannot straddle a page", async () => {
    await showSelection();
    expect(lastQuery()).toContain("group_by=candidate");
  });

  it("leaves the Interview view and the unfiltered view flat", async () => {
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    fireEvent.change(select, { target: { value: "interview" } });
    await waitFor(() => expect(lastQuery()).toContain("classification_group=interview"));
    expect(lastQuery()).not.toContain("group_by");
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => expect(lastQuery()).not.toContain("classification_group"));
    expect(lastQuery()).not.toContain("group_by");
  });

  it("counts candidates rather than mails in the pager", async () => {
    install(SELECTION_ROWS, 42);
    await showSelection();
    await waitFor(() => expect(screen.getByText(/42\s+candidates/)).toBeTruthy());
  });
});

describe("each candidate appears once", () => {
  beforeEach(() => install());

  it("draws one parent per candidate, not one per mail", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    expect(headings()).toEqual(["Aniket", "Yamini Rohit"]);
  });

  it("shows the candidate email on the parent and not on every row", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    const shown = screen.getAllByText("aniket.rane@example.com");
    expect(shown.length).toBe(1);
    expect(shown[0].closest(".mail-group__head")).toBeTruthy();
  });

  it("says how many mails the candidate has", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    expect(screen.getByText("3 selection mails")).toBeTruthy();
    expect(screen.getByText("1 selection mail")).toBeTruthy();
  });
});

describe("the mails underneath are not deduplicated", () => {
  beforeEach(() => install());

  it("keeps every mail as its own row inside the candidate group", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    const [aniket, yamini] = groups();
    expect(within(aniket).getAllByRole("row").length).toBe(4); // parent + 3 mails
    expect(within(yamini).getAllByRole("row").length).toBe(2);
  });

  it("preserves each mail's own company, status, subject, confidence and review", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    const [aniket] = groups();
    const mails = within(aniket).getAllByRole("row").slice(1);
    const cells = mails.map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));
    expect(cells.map((c) => c[1])).toEqual(["Innominds", "Innominds", "Digiverifier"]);
    expect(cells.map((c) => c[2])).toEqual(["Offer Received", "Joining Confirmed", "BGV"]);
    expect(cells.map((c) => c[3])).toEqual([
      "Innominds offer has been released",
      "please complete the pre-onboarding formalities",
      "Invitation - Digital Employment BGV",
    ]);
    expect(cells.map((c) => c[4])).toEqual(["95%", "100%", "85%"]);
    expect(cells.map((c) => c[7])).toEqual(["AUTOMATED", "AUTOMATED", "AUTOMATED"]);
  });

  it("keeps two mails that share a candidate and a company as two rows", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    const innominds = [...groups()[0].querySelectorAll("tr")]
      .filter((tr) => tr.textContent.includes("Innominds"));
    expect(innominds.length).toBe(2);
  });

  it("keeps the per-mail actions on every row", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    for (const tr of within(groups()[0]).getAllByRole("row").slice(1)) {
      expect(within(tr).getByText("Open")).toBeTruthy();
      expect(within(tr).getByText("Dismiss")).toBeTruthy();
    }
  });

  it("still opens the individual mail that was clicked", async () => {
    await showSelection();
    await waitFor(() => expect(groups().length).toBe(2));
    const mails = within(groups()[0]).getAllByRole("row").slice(1);
    fireEvent.click(within(mails[2]).getByText("Open"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(within(screen.getByRole("dialog")).getByText(
      "Invitation - Digital Employment BGV")).toBeTruthy();
  });
});

describe("interview mails are absent from this view", () => {
  it("renders no group when the selection query returns nothing", async () => {
    // The server applies classification_group=selection, so an interview row
    // never arrives. Asserted on the request rather than by filtering again in
    // the browser, which would hide a server-side regression.
    install([]);
    await showSelection();
    expect(lastQuery()).toContain("classification_group=selection");
    await waitFor(() => expect(screen.getByText(
      "No notifications match these filters.")).toBeTruthy());
    expect(groups().length).toBe(0);
  });
});
