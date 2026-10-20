/**
 * Mail Alerts, compact (20 Oct).
 *
 * Measured live at 1920px before this: the page ran to 1,430px in an 873px
 * frame, so the table scrolled with the page and its sticky header never held;
 * rows were 44-106px; a 1,180px minimum width scrolled the table sideways under
 * about 1,460px; Open / Read / Dismiss were three text buttons; and 50
 * automation cells said "AUTOMATED" beside "Auto Booked", 37 statuses "Needs
 * reading" beside "Interview Automatically Booked".
 *
 * Asserted here: the labels are evened out without changing what they say;
 * every alert still renders, its long values carrying their whole text as a
 * tooltip; each icon action is named and calls the endpoint it did; an empty
 * result says why and offers the way back; and the layout rules (jsdom performs
 * no layout, so the stylesheet is read) fill the frame without a sideways
 * scroll.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { captureLayout } from "../test/captureLayout.js";
import { MailMonitoringNotifications, automationLabel, automationTone, displayLabel } from "./MailMonitoringNotifications.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "recruitmentMail.css"), "utf-8").replace(/\r\n/g, "\n");

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

// Invented candidates, shaped like production on 20 Oct: every booking outcome
// it holds, the one lower-case status, and alerts with no booking at all.
const LONG_SUBJECT = "Invitation: Technical Interview Round 2 for the Senior Data Engineer role at Example Analytics Private Limited (Hyderabad) - please confirm your availability";
const alert = (id, over = {}) => ({
  id, candidate_id: `cand-${id}`, candidate_name: `Candidate ${id}`, candidate_email: `candidate${id}@example.com`,
  company_name: "Example Analytics", job_role: "Data Engineer", classification: "interview_confirmed",
  candidate_status: "Interview Automatically Booked", booking_status: "Auto Booked",
  email_subject: `Interview invitation ${id}`, ai_confidence: 0.92,
  email_received_at: "2026-10-19T09:30:00Z", created_at: "2026-10-19T09:31:00Z", is_read: false, ...over,
});
const ROWS = [
  alert("a1", { email_subject: LONG_SUBJECT }),
  alert("a2", { candidate_status: "Interview Rescheduled", booking_status: "Rescheduled", is_read: true }),
  alert("a3", { candidate_status: "Interview Cancelled", booking_status: "Cancelled", classification: "interview_cancelled" }),
  alert("a4", { candidate_status: "Booking Removed", booking_status: "Booking Removed" }),
  alert("a5", { candidate_status: "Automatic Booking Blocked", booking_status: "Blocked" }),
  alert("a6", { candidate_status: "Needs reading", booking_status: null, classification: "interview_needs_reading" }),
  alert("a7", { candidate_status: "Offer Received", booking_status: null, classification: "offer_received" }),
  ...Array.from({ length: 13 }, (_, i) => alert(`b${i + 1}`, { candidate_status: "Interview Shortlisted", booking_status: null, classification: "interview_shortlisted" })),
];

let calls;
let listed;
function install(rows = ROWS, total = 184) {
  calls = [];
  listed = rows;
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url, init = {}) => {
    const href = String(url);
    calls.push({ href, method: init.method || "GET" });
    const body = href.includes("/mail-monitoring/candidates") ? { status: "ok", candidates: [] }
      : href.includes("/summary") ? { summary: { unread: 6, ai_retry_pending: 17, visible_total: total } }
        : href.includes("/notifications?") ? { notifications: listed, total }
          : { status: "ok" };
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));
}
beforeEach(() => install());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const renderScreen = () => render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);
const rows = () => [...document.querySelectorAll("tr.mail-notification-row")];
const rowOf = (id) => rows().find((tr) => tr.textContent.includes(`Candidate ${id}`));
const lastList = () => calls.filter((c) => c.href.includes("/mail-monitoring/notifications?")).at(-1)?.href || "";
const posts = () => calls.filter((c) => c.method === "POST").map((c) => c.href.replace(/^.*\/notifications\//, ""));

describe("labels", () => {
  it("even out the case without changing what they say", () => {
    expect(displayLabel("Needs reading")).toBe("Needs Reading");
    expect(displayLabel("AUTOMATED")).toBe("Automated");
    expect(displayLabel("auto_booked")).toBe("Auto Booked");
    expect(displayLabel("BGV")).toBe("BGV");
    expect(displayLabel("HR confirmation")).toBe("HR Confirmation");
    expect(displayLabel("Interview Automatically Booked")).toBe("Interview Automatically Booked");
    expect(displayLabel("Offer of employment")).toBe("Offer of Employment");
  });

  it("call an alert that changed no booking Automated, in the tone of every other outcome", () => {
    expect(automationLabel({ booking_status: null })).toBe("Automated");
    expect(automationLabel({ automation_state: "AUTOMATED" })).toBe("Automated");
    expect([
      ["Auto Booked", "success"], ["Rescheduled", "info"], ["Booking Removed", "warning"],
      ["Blocked", "warning"], ["Cancelled", "danger"], [null, "neutral"],
    ].map(([booking_status]) => automationTone({ booking_status }))).toEqual(["success", "info", "warning", "warning", "danger", "neutral"]);
  });
});

describe("the alerts table", () => {
  it("shows every alert of the page, with the count and the pages", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    expect(screen.getByText("184 notifications")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("shows each status and automation outcome as an evened-out badge", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    const badge = (id, cls) => rowOf(id).querySelector(cls);
    expect(badge("a6", ".mail-status").textContent).toBe("Needs Reading");
    expect(badge("a6", ".mail-automation").textContent).toBe("Automated");
    expect(badge("a6", ".mail-automation")).toHaveClass("mail-automation--neutral");
    expect(badge("a1", ".mail-automation")).toHaveClass("mail-automation--success");
    expect(badge("a2", ".mail-automation")).toHaveClass("mail-automation--info");
    expect(badge("a3", ".mail-automation")).toHaveClass("mail-automation--danger");
    expect(badge("a5", ".mail-automation")).toHaveClass("mail-automation--warning");
    expect(document.querySelector(".mail-table").textContent).not.toContain("AUTOMATED");
  });

  it("gives every long value its whole text as a tooltip", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    const a1 = rowOf("a1");
    expect(a1.querySelector(".mail-cell--subject")).toHaveAttribute("title", LONG_SUBJECT);
    expect(a1.querySelector(".mail-cell--candidate strong")).toHaveAttribute("title", "Candidate a1 · candidatea1@example.com");
    expect(a1.querySelector(".mail-cell--company .mail-cell__text")).toHaveAttribute("title", "Example Analytics");
    expect(a1.querySelector(".mail-status")).toHaveAttribute("title", "Interview Automatically Booked");
  });

  it("names every action, as a button with a tooltip, reachable from the keyboard", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    for (const tr of rows()) {
      const actions = [...tr.querySelectorAll(".mail-cell--actions button")];
      expect(actions.map((b) => b.getAttribute("aria-label"))).toEqual([
        "Open alert", tr.classList.contains("is-unread") ? "Mark as read" : "Mark as unread", "Dismiss alert",
      ]);
      for (const b of actions) {
        expect(b).toHaveAttribute("type", "button");
        expect(b).toHaveAttribute("title", b.getAttribute("aria-label"));
        expect(b).not.toHaveAttribute("tabindex", "-1");
        expect(b.textContent).toBe("");
      }
    }
  });

  it("marks read, unread and dismisses through the same endpoints", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    fireEvent.click(within(rowOf("a1")).getByRole("button", { name: "Mark as read" }));
    fireEvent.click(within(rowOf("a2")).getByRole("button", { name: "Mark as unread" }));
    fireEvent.click(within(rowOf("a3")).getByRole("button", { name: "Dismiss alert" }));
    await waitFor(() => expect(posts()).toEqual(["a1/read", "a2/unread", "a3/dismiss"]));
  });

  it("opens the alert from its Open action without the row opening it a second time", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    fireEvent.click(within(rowOf("a7")).getByRole("button", { name: "Open alert" }));
    const dialog = await screen.findByRole("dialog", { name: "Mail monitoring notification" });
    expect(within(dialog).getByRole("heading", { name: "Offer Received" })).toBeInTheDocument();
    await waitFor(() => expect(posts()).toEqual(["a7/read"]));
  });
});

describe("the controls", () => {
  it("keep the tabs, Clear all and Live on one row, above search and the three filters", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    const topbar = document.querySelector(".mail-topbar");
    expect(within(topbar).getAllByRole("tab").map((t) => t.textContent)).toEqual(["Unread Invitations6", "AI Retry Pending17", "All Alerts99+"]);
    expect(within(topbar).getByRole("button", { name: "Clear all notifications" })).toBeInTheDocument();
    expect(within(topbar).getByText("Live")).toBeInTheDocument();
    const filters = document.querySelector(".mail-filters");
    expect(topbar.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (const name of ["Search notifications", "Candidate filter", "Alert type filter", "Booking result filter"]) {
      expect(within(filters).getByLabelText(name)).toBeInTheDocument();
    }
    captureLayout("mail-alerts", document.querySelector(".mail-monitoring-page"));
  });
});

describe("an empty result", () => {
  it("says no alert matches and clears the filters in one step", async () => {
    renderScreen();
    await waitFor(() => expect(rows()).toHaveLength(20));
    listed = [];
    fireEvent.change(screen.getByLabelText("Search notifications"), { target: { value: "nobody" } });
    await screen.findByText("No notifications match these filters.");
    expect(lastList()).toContain("search=nobody");
    expect(screen.getByText("Try another search, or clear the filters to see every alert in this tab.")).toBeInTheDocument();
    captureLayout("mail-alerts-empty", document.querySelector(".mail-monitoring-page"));
    listed = ROWS;
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(rows()).toHaveLength(20));
    expect(lastList()).not.toContain("search=");
    expect(screen.getByLabelText("Search notifications")).toHaveValue("");
  });

  it("offers no Clear filters when the tab alone is empty", async () => {
    install([], 0);
    renderScreen();
    await screen.findByText("No unread invitations. All caught up!");
    expect(screen.getByText("New invitations appear here as soon as they arrive.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();
    expect(document.querySelectorAll("tbody tr")).toHaveLength(1);
  });
});

describe("the layout rules", () => {
  const block = CSS.slice(CSS.indexOf("/* ── Mail Alerts, compact (20 Oct)"), CSS.indexOf("/* ── Compact Gmail operations layout"));
  const media = (header) => {
    const at = block.indexOf(`@media ${header} {`);
    return at < 0 ? "" : block.slice(at, block.indexOf("\n}\n", at));
  };

  it("never give the table a minimum width that scrolls it sideways", () => {
    expect(CSS).not.toMatch(/\.mail-table \{ min-width: 1180px; \}/);
    expect(CSS).not.toMatch(/\.mail-table[^{]*\{[^}]*min-width:\s*\d{3,}px/);
  });

  it("fill the frame from 641px, the table box scrolling inside with its header held", () => {
    const wide = media("(min-width: 640.02px)");
    expect(wide).toMatch(/\.mail-monitoring-page \{[^}]*height: 100%;[^}]*max-width: none;[^}]*padding: 0;/);
    expect(wide).toMatch(/\.mail-table-wrap \{ flex: 1 1 auto; min-height: 160px; \}/);
    expect(CSS).toMatch(/\.mail-table-wrap \{\n  overflow: auto;/);
    expect(CSS).toMatch(/\.mail-table th \{\n  position: sticky;\n  top: 0;/);
    expect(block).toMatch(/\.mail-table-wrap \{ position: relative; overscroll-behavior: contain; scroll-behavior: smooth; \}/);
  });

  it("give Email subject what the fixed columns leave, and let two columns give way", () => {
    const desk = media("(min-width: 1180.02px)");
    expect(desk).toMatch(/\.mail-table \{ table-layout: fixed; width: 100%; \}/);
    expect(desk).toMatch(/\.mail-col--subject \{ width: auto; \}/);
    expect(desk).toMatch(/\.mail-col--confidence \{ width: 84px;/);
    expect(desk).toMatch(/\.mail-col--automation \{ width: 128px; \}/);
    expect(desk).toMatch(/\.mail-cell--subject \{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
    expect(media("(min-width: 1180.02px) and (max-width: 1440px)")).toMatch(/\.mail-col--detected,\n  \.mail-table \.mail-cell--detected \{ display: none; \}/);
    expect(media("(min-width: 1180.02px) and (max-width: 1366px)")).toMatch(/\.mail-col--confidence,\n  \.mail-table \.mail-cell--confidence \{ display: none; \}/);
  });

  it("make each alert two lines of one grid between a phone and a desktop", () => {
    const mid = media("(min-width: 640.02px) and (max-width: 1180px)");
    expect(mid).toMatch(/\.mail-table thead \{ display: none; \}/);
    expect(mid).toContain('"candidate subject received actions"');
    expect(mid).toContain('"company status automation actions"');
  });

  it("make every action a 40px target on a phone and under a finger", () => {
    expect(CSS).toMatch(/@media \(max-width: 640px\) \{[\s\S]*\.mail-cell--actions \.mail-action \{ width: 40px; height: 40px; margin: 0; \}/);
    expect(media("(pointer: coarse)")).toMatch(/\.mail-table \.mail-action \{ width: 40px; height: 40px; \}/);
    expect(media("(pointer: coarse)")).toMatch(/\.mail-table \.mail-empty-state__clear \{ min-height: 40px; \}/);
    // Scoped under .mail-table: unscoped, the table's `td button` rule won and made it 23px.
    expect(block).toMatch(/\n\.mail-table \.mail-empty-state__clear \{\n  min-height: 34px;/);
  });
});
