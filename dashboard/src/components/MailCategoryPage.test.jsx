/**
 * Selection Related / Interview Related, drawn to the approved mockup.
 *
 * Groups are Candidate + Company with counts from the server; expanding one
 * lists its whole history (Selection and Interview, labelled) newest first;
 * opening a mail marks that one mail read and no other.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { captureLayout } from "../test/captureLayout.js";
import { MailCategoryPage, mailWhen } from "./MailCategoryPage.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const GROUPS = [
  {
    candidate_id: "c-ashok", company_key: "hcltech", candidate_name: "Ashok Rao",
    candidate_email: "ashok@example.com", company_name: "HCLTech", total: 3, unread: 2, read: 1,
    latest_at: "2026-11-11T11:22:31Z", latest_classification: "joining_confirmed", latest_status: "Joining Confirmed",
  },
  {
    candidate_id: "c-meera", company_key: "fractal analytics", candidate_name: "Meera Iyer",
    candidate_email: "meera@example.com", company_name: "Fractal Analytics", total: 8, unread: 0, read: 8,
    latest_at: "2026-10-29T03:34:30Z", latest_classification: "offer_received", latest_status: "Offer Received",
  },
];
const HISTORY = [
  { id: "m3", candidate_id: "c-ashok", company_name: "HCLTech", email_subject: "Next Steps: Document Upload", classification: "joining_confirmed", candidate_status: "Joining Confirmed", is_read: false, email_received_at: "2026-11-11T11:22:31Z" },
  { id: "m2", candidate_id: "c-ashok", company_name: "HCLTech", email_subject: "Evaluation 2 Selection for 141850", classification: "post_interview_shortlisted", candidate_status: "Shortlisted After Interview", is_read: false, email_received_at: "2026-11-11T05:00:00Z" },
  { id: "m1", candidate_id: "c-ashok", company_name: "HCLTech", email_subject: "Evaluation 1 Selection for 141850", classification: "post_interview_shortlisted", candidate_status: "Shortlisted After Interview", is_read: true, email_received_at: "2026-11-10T05:00:00Z" },
];
const COUNTS = { unread: 2, retry: 0, all: 27 };

let calls = [];
let readIds = new Set();
const respond = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
function install({ groups = GROUPS, history = HISTORY } = {}) {
  calls = [];
  readIds = new Set();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const path = String(url);
    calls.push({ path, method: options.method || "GET" });
    const params = new URL(path, "http://test.invalid").searchParams;
    const readNow = (row) => row.is_read || readIds.has(row.id);
    if ((options.method || "GET") === "POST" && path.endsWith("/read")) readIds.add(path.split("/").at(-2));
    if (path.includes("/groups/emails")) return respond({ notifications: history.map((row) => ({ ...row, is_read: readNow(row) })), total: history.length });
    if (path.includes("/groups?")) {
      // The server's counts follow what has been read, as production's do.
      const live = groups.map((group) => (group.candidate_id === "c-ashok"
        ? { ...group, unread: history.filter((row) => !readNow(row)).length, read: history.filter(readNow).length }
        : group));
      return respond({ groups: live, total: live.length, companies: [{ key: "hcltech", name: "HCLTech", count: 3 }] });
    }
    if (path.includes("/summary")) return respond({ summary: { unread: 79, selection_unread: 2, interview_unread: 77 } });
    if (path.includes("/notifications?")) {
      const total = params.get("is_read") === "false" ? COUNTS.unread : params.get("priority") ? COUNTS.retry : COUNTS.all;
      return respond({ notifications: [], total });
    }
    if (path.includes("/api/ai-recruitment/events/")) return respond({ event: null });
    return respond({ status: "ok" });
  }));
}
const renderPage = (category = "selection") => render(<ConfirmProvider><MailCategoryPage category={category} /></ConfirmProvider>);
const groupsRequests = () => calls.filter((c) => c.path.includes("/groups?")).map((c) => new URL(c.path, "http://test.invalid").searchParams);

beforeEach(() => install());
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the counters are the tabs", () => {
  it("show the category's real counts and open on All Alerts", async () => {
    renderPage();
    const tabs = await screen.findByRole("tablist", { name: "Selection Related tabs" });
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: /All Alerts/ }).textContent).toContain("27"));
    expect(within(tabs).getByRole("tab", { name: /Unread/ }).textContent).toContain("2");
    expect(within(tabs).getByRole("tab", { name: /All Alerts/ })).toHaveAttribute("aria-selected", "true");
    expect(groupsRequests().at(-1).get("classification_group")).toBe("selection");
  });

  it("Unread asks for groups with unread mail of this category", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: /Unread/ }));
    await waitFor(() => expect(groupsRequests().at(-1).get("is_read")).toBe("false"));
    expect(groupsRequests().at(-1).get("classification_group")).toBe("selection");
  });
});

describe("a Candidate + Company card", () => {
  it("shows total, unread and read, and the latest verified status", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    expect(within(card).getByText("3 Emails")).toBeInTheDocument();
    expect(card.textContent).toContain("Unread: 2");
    expect(card.textContent).toContain("Opened: 1");
    expect(within(card).getByTitle("Latest verified status").textContent).toBe("Joining Confirmed");
    expect(screen.getByText("2 Results")).toBeInTheDocument();
  });

  it("shows the status the mail states, not an unproven Joining Confirmed", async () => {
    install({
      groups: [{ ...GROUPS[0], latest_status: "Background Verification", latest_status_evidence: "joining not stated" }],
      history: [{ ...HISTORY[0], verified_status: "Background Verification", status_evidence: "joining not stated" }],
    });
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    expect(within(card).getByTitle(/does not state joining/).textContent).toBe("Background Verification");
    fireEvent.click(within(card).getByRole("button", { name: /Show emails/ }));
    const list = await within(card).findByRole("list", { name: "Emails, newest first" });
    await waitFor(() => expect(list.textContent).toContain("Selection · Background Verification"));
    expect(list.textContent).not.toContain("Joining Confirmed");
  });

  it("expands to every mail of the group, newest first, Selection and Interview labelled", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.click(within(card).getByRole("button", { name: /Show emails for Ashok Rao at HCLTech/ }));
    const list = await within(card).findByRole("list", { name: "Emails, newest first" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
    const history = calls.find((c) => c.path.includes("/groups/emails")).path;
    expect(history).toContain("candidate_id=c-ashok");
    expect(history).toContain("company_key=hcltech");
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector(".mcp-mail__subject").textContent)).toEqual(HISTORY.map((m) => m.email_subject));
    expect(items[0].className).toContain("mcp-mail--unread");
    expect(items[2].className).toContain("mcp-mail--read");
    expect(items[0].textContent).toContain("Unread");
    expect(items[2].textContent).toContain("Opened");
    expect(items[0].textContent).toContain("Selection · Joining Confirmed");
    expect(items[1].textContent).toContain("Interview · Shortlisted After Interview");
    captureLayout("mail-category-expanded", document.querySelector(".mcp"));
  });

  it("opening a mail marks only that mail read", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.click(within(card).getByRole("button", { name: /Show emails/ }));
    const list = await within(card).findByRole("list", { name: "Emails, newest first" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
    fireEvent.click(within(list).getByRole("button", { name: "Unread: Evaluation 2 Selection for 141850" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/mail-monitoring/notifications/m2/read"]));
    const items = within(list).getAllByRole("listitem");
    expect(items[1].className).toContain("mcp-mail--read");
    expect(items[0].className).toContain("mcp-mail--unread");
    expect(card.textContent).toContain("Unread: 1");
    expect(await screen.findByRole("dialog", { name: "Mail monitoring notification" })).toBeInTheDocument();
  });
});

describe("filters", () => {
  it("search, company and status narrow the groups and keep the category", async () => {
    renderPage();
    await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.change(screen.getByLabelText("Search notifications"), { target: { value: "Ashok" } });
    fireEvent.change(screen.getByLabelText("Company filter"), { target: { value: "hcltech" } });
    fireEvent.change(screen.getByLabelText("Status filter"), { target: { value: "joining_confirmed" } });
    await waitFor(() => {
      const last = groupsRequests().at(-1);
      expect(last.get("search")).toBe("Ashok");
      expect(last.get("company_key")).toBe("hcltech");
      expect(last.get("classification")).toBe("joining_confirmed");
      expect(last.get("classification_group")).toBe("selection");
    });
    const statuses = [...screen.getByLabelText("Status filter").options].map((o) => o.value);
    expect(statuses).not.toContain("interview_confirmed");
  });

  it("an empty Selection page names what Selection holds", async () => {
    install({ groups: [] });
    renderPage();
    await screen.findByText("No selection alerts yet.");
    captureLayout("mail-category-empty", document.querySelector(".mcp"));
  });

  it("the Interview page carries its own bell controls; Selection its alarm", async () => {
    renderPage("interview");
    expect(await screen.findByRole("group", { name: "Interview alert sound" })).toBeInTheDocument();
    cleanup();
    renderPage("selection");
    expect(await screen.findByRole("group", { name: "Selection alarm sound" })).toBeInTheDocument();
    captureLayout("mail-category", document.querySelector(".mcp"));
  });
});

describe("dates", () => {
  it("read as Today, Yesterday or the day, in IST", () => {
    const now = new Date("2026-11-11T12:00:00Z");
    expect(mailWhen("2026-11-11T04:28:00Z", now)).toBe("Today, 09:58 AM");
    expect(mailWhen("2026-11-10T12:50:00Z", now)).toBe("Yesterday, 06:20 PM");
    expect(mailWhen("2026-11-02T05:45:00Z", now)).toBe("Mon, 2 Nov, 11:15 AM");
  });
});
