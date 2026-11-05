/**
 * Mail Alerts is grouped Candidate -> Company -> Mail.
 *
 * Candidates follow their latest Mail received, each candidate's companies
 * follow that company's latest mail, and a company's mails are newest first.
 * The server pages by candidate (`group_by=candidate`) and returns the rows
 * in that order; the screen draws them in exactly that order under a heading
 * per candidate and per company, each with its count. Nothing is merged:
 * every mail stays its own row with its own actions.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MailMonitoringNotifications, groupByCandidateAndCompany } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

let n = 0;
const mail = (candidate, company, received, over = {}) => ({
  id: `m-${++n}`, candidate_id: candidate.id, candidate_name: candidate.name,
  candidate_email: candidate.email, company_name: company,
  classification: "interview_confirmed", candidate_status: "Interview Confirmed",
  email_subject: `${company || "Unknown"} mail ${n}`, ai_confidence: 0.9,
  email_received_at: received, created_at: "2026-11-05T13:00:00Z",
  is_read: true, is_reviewed: true, ...over,
});
const ASHOK = { id: "cand-ashok", name: "Ashok", email: "ashok@example.com" };
const PUJA = { id: "cand-puja", name: "Puja", email: "puja@example.com" };
const hour = (i) => String(12 - i).padStart(2, "0");

// As the server returns one page: Ashok (newest mail 5 Nov) first, his
// HCLTech mails (latest 5 Nov) before his Infosys ones (latest 4 Nov), each
// company newest first; then Puja.
const PAGE = [
  ...Array.from({ length: 10 }, (_, i) => mail(ASHOK, "HCLTech", `2026-11-05T${hour(i)}:00:00Z`)),
  ...Array.from({ length: 10 }, (_, i) => mail(ASHOK, "Infosys", `2026-11-04T${hour(i)}:00:00Z`)),
  mail(PUJA, "Fractal", "2026-11-03T10:00:00Z", { classification: "offer_received", candidate_status: "Offer Received" }),
  mail(PUJA, "", "2026-11-02T10:00:00Z"),
];

function response(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }

let calls = [];
function install(notifications = PAGE, total = 2, emailCount = notifications.length) {
  calls = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url) => {
    const href = String(url);
    calls.push(href);
    if (href.includes("/mail-monitoring/candidates")) return response({ status: "ok", candidates: [] });
    if (href.includes("/summary")) return response({ summary: { unread: 0 } });
    if (href.includes("/notifications")) return response({ notifications, total, email_count: emailCount });
    return response({ status: "ok" });
  }));
}

const tableQueries = () => calls.filter((c) => c.includes("/mail-monitoring/notifications?") && !c.includes("limit=1&"));
const lastQuery = () => tableQueries().at(-1) || "";
const renderScreen = () => render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);
const candidates = () => [...document.querySelectorAll("tbody.mail-group")];
const text = (el, sel) => el.querySelector(sel)?.textContent;
// Each candidate as [name, count, companies], each company as [name, count, subjects].
const outline = () => candidates().map((tbody) => {
  const companies = [];
  for (const tr of tbody.querySelectorAll("tr")) {
    if (tr.classList.contains("mail-company__head")) companies.push([text(tr, ".mail-company__name"), text(tr, ".mail-company__count"), []]);
    if (tr.classList.contains("mail-notification-row")) companies.at(-1)[2].push(text(tr, ".mail-cell--subject"));
  }
  return [text(tbody, ".mail-group__head strong"), text(tbody, ".mail-group__count"), companies];
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the table is Candidate -> Company -> Mail", () => {
  beforeEach(() => install());

  it("shows two companies of one candidate as two sections, each with its count", async () => {
    renderScreen();
    await waitFor(() => expect(candidates().length).toBe(2));
    const [ashok, puja] = outline();
    expect(ashok[0]).toBe("Ashok");
    expect(ashok[1]).toBe("20 mails · 2 companies");
    expect(ashok[2].map(([name, count]) => [name, count])).toEqual([["HCLTech", "10 mails"], ["Infosys", "10 mails"]]);
    expect(puja[1]).toBe("2 mails · 2 companies");
    expect(puja[2].map(([name, count]) => [name, count])).toEqual([["Fractal", "1 mail"], ["Company not identified", "1 mail"]]);
  });

  it("keeps the server's order at every level: candidate, company, mail", async () => {
    renderScreen();
    await waitFor(() => expect(candidates().length).toBe(2));
    const drawn = outline().flatMap(([, , companies]) => companies.flatMap(([, , subjects]) => subjects));
    expect(drawn).toEqual(PAGE.map((m) => m.email_subject));
  });

  it("puts the candidate's email on the heading, and each mail keeps its own row and actions", async () => {
    renderScreen();
    await waitFor(() => expect(candidates().length).toBe(2));
    expect(text(candidates()[0], ".mail-group__head small")).toBe("ashok@example.com");
    const rows = candidates()[0].querySelectorAll("tr.mail-notification-row");
    expect(rows.length).toBe(20);
    for (const tr of rows) {
      expect(within(tr).getByRole("button", { name: "Open alert" })).toBeTruthy();
      expect(within(tr).getByRole("button", { name: "Dismiss alert" })).toBeTruthy();
    }
    fireEvent.click(within(rows[12]).getByRole("button", { name: "Open alert" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(within(screen.getByRole("dialog")).getByText(PAGE[12].email_subject)).toBeTruthy();
  });
});

describe("paging and filters", () => {
  it("pages by candidate, newest first, under every alert type", async () => {
    install();
    renderScreen();
    const select = await screen.findByLabelText("Alert type filter");
    await waitFor(() => expect(lastQuery()).toContain("group_by=candidate"));
    for (const value of ["selection", "interview", ""]) {
      fireEvent.change(select, { target: { value } });
      await waitFor(() => expect(lastQuery().includes("classification_group=")).toBe(Boolean(value)));
      expect(lastQuery()).toContain("group_by=candidate");
      expect(lastQuery()).toContain("sort=newest");
    }
  });

  it("still counts each tab in mails, not candidates", async () => {
    install();
    renderScreen();
    fireEvent.change(await screen.findByLabelText("Alert type filter"), { target: { value: "selection" } });
    await waitFor(() => expect(calls.some((c) => c.includes("limit=1&"))).toBe(true));
    for (const count of calls.filter((c) => c.includes("limit=1&"))) expect(count).not.toContain("group_by");
  });

  it("names candidates and mails in the pager and asks for the next page of candidates", async () => {
    install(PAGE, 45, 310);
    renderScreen();
    await waitFor(() => expect(screen.getByText("45 candidates · 310 mails")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastQuery()).toContain("offset=20"));
    expect(lastQuery()).toContain("group_by=candidate");
  });
});

describe("grouping loses nothing", () => {
  it("keeps every mail once, and treats spacing and case as the same company", () => {
    const rows = [
      mail(ASHOK, "HCLTech", "2026-11-05T10:00:00Z"),
      mail(ASHOK, " hcltech ", "2026-11-05T09:00:00Z"),
      mail(ASHOK, "HCLTech", "2026-11-05T09:00:00Z"),
      mail(ASHOK, null, "2026-11-04T09:00:00Z"),
    ];
    const [ashok] = groupByCandidateAndCompany(rows);
    expect(ashok.count).toBe(4);
    expect(ashok.companies.map((c) => [c.name, c.items.length])).toEqual([["HCLTech", 3], ["Company not identified", 1]]);
    expect(ashok.companies.flatMap((c) => c.items).map((m) => m.id).sort()).toEqual(rows.map((m) => m.id).sort());
  });
});
