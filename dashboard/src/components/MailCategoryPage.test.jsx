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
    category_total: 1, category_unread: 1, category_read: 0,
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
const INTERVIEW_EMAILS = [
  { id: "i2", candidate_id: "c-ravi", candidate_name: "Ravi Kumar", company_name: "Capgemini", job_role: "Analyst", email_subject: "Interview confirmed for 14 Nov", classification: "interview_confirmed", candidate_status: "Interview Confirmed", verified_status: "Interview Confirmed", is_read: false, email_received_at: "2026-11-11T09:00:00Z" },
  { id: "i1", candidate_id: "c-ravi", candidate_name: "Ravi Kumar", company_name: "Capgemini", email_subject: "You are shortlisted for interview", classification: "interview_shortlisted", candidate_status: "Interview Shortlisted", is_read: true, email_received_at: "2026-11-10T09:00:00Z" },
  { id: "i0", candidate_id: "c-ashok", candidate_name: "Ashok Rao", company_name: "HCLTech", company_source: "booking", email_subject: "Interview slot", classification: "interview_confirmed", is_read: true, email_received_at: "2026-11-09T09:00:00Z" },
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
    if (path.includes("/mail-monitoring/emails?")) {
      const rows = INTERVIEW_EMAILS.map((row) => ({ ...row, is_read: readNow(row) }))
        .filter((row) => params.get("is_read") === null || String(row.is_read) === params.get("is_read"));
      return respond({ emails: rows, total: rows.length, companies: [{ key: "capgemini", name: "Capgemini", count: 2 }, { key: "hcltech", name: "HCLTech", count: 1 }] });
    }
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
    if (/\/notifications\/[^/]+\/email$/.test(path)) {
      const id = path.split("/").at(-2);
      return respond({
        email: { subject: `Subject of ${id}`, sender_name: "Recruiter", sender_email: "hr@example.com",
          recipient_email: "ashok@example.com", sent_at: "2026-11-11T05:00:00Z", body: `Body of mail ${id}.`, body_html: "" },
        gmail: { message_id: `msg-${id}`, thread_id: `thr-${id}`, account: "ashok@example.com" },
      });
    }
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
    // The card counts Selection mail, like the counters; the history counts all.
    expect(within(card).getByLabelText("1 Selection email")).toBeInTheDocument();
    expect(card.querySelector(".mcp-pill--new").textContent).toBe("1 unread");
    expect(within(card).getByLabelText("0 opened")).toBeInTheDocument();
    expect(within(card).getByTitle("Latest verified status").textContent).toBe("Joining Confirmed");
    expect(screen.getByText("2 Results")).toBeInTheDocument();
  });

  it("shows the status the mail states, not an unproven Joining Confirmed", async () => {
    install({
      groups: [{ ...GROUPS[0], latest_status: "Background Verification", latest_status_evidence: "different step" }],
      history: [{ ...HISTORY[0], verified_status: "Background Verification", status_evidence: "different step" }],
    });
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    expect(within(card).getByTitle(/states this step, not the detected label/).textContent).toBe("Background Verification");
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
    // m2 is an Interview mail: the history's unread drops, the Selection card's does not.
    await waitFor(() => expect(card.querySelector(".mcp-history__unread").textContent).toBe("1 unread"));
    expect(card.querySelector(".mcp-pill--new").textContent).toBe("1 unread");
    // The mail opens inside the card, not in Gmail and not in a dialog.
    const reading = await within(card).findByRole("region", { name: "Email: Evaluation 2 Selection for 141850" });
    await waitFor(() => expect(reading.textContent).toContain("Body of mail m2."));
    expect(screen.queryByRole("dialog")).toBeNull();
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

describe("reading mail in the app", () => {
  it("tapping anywhere on the card opens its emails", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.click(card.querySelector(".mcp-group__email"));
    expect(await within(card).findByRole("list", { name: "Emails, newest first" })).toBeInTheDocument();
    fireEvent.click(card.querySelector(".mcp-group__name"));
    await waitFor(() => expect(within(card).queryByRole("list", { name: "Emails, newest first" })).toBeNull());
  });

  it("Gmail is an optional link inside the opened mail, never the default", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.click(within(card).getByRole("button", { name: /Show emails/ }));
    const list = await within(card).findByRole("list", { name: "Emails, newest first" });
    await waitFor(() => expect(within(list).getAllByRole("listitem")).toHaveLength(3));
    // Nothing on the card or the list links to Gmail until a mail is opened.
    expect(card.querySelector('a[href*="mail.google.com"]')).toBeNull();
    fireEvent.click(within(list).getByRole("button", { name: "Opened: Evaluation 1 Selection for 141850" }));
    const gmail = await within(card).findByRole("link", { name: /Open in Gmail/ });
    expect(gmail.getAttribute("href")).toBe("https://mail.google.com/mail/u/?authuser=ashok%40example.com#all/thr-m1");
    expect(gmail).toHaveAttribute("target", "_blank");
    captureLayout("mail-category-reading", document.querySelector(".mcp"));
    // Already read: opening it posts nothing.
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
    // Opening it again closes it.
    fireEvent.click(within(list).getByRole("button", { name: "Opened: Evaluation 1 Selection for 141850" }));
    await waitFor(() => expect(within(card).queryByRole("link", { name: /Open in Gmail/ })).toBeNull());
  });

  it("iPhone Safari does not turn addresses into mail links", async () => {
    const { readFileSync } = await import("node:fs");
    const { dirname, join } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..", "index.html"), "utf-8");
    expect(html).toMatch(/<meta name="format-detection" content="[^"]*email=no/);
  });
});

describe("unread counts", () => {
  it("the history line shows every mail with the company, by kind, and its unread", async () => {
    renderPage();
    const card = await screen.findByRole("article", { name: "Ashok Rao, HCLTech" });
    fireEvent.click(within(card).getByRole("button", { name: /Show emails/ }));
    await waitFor(() => expect(card.querySelector(".mcp-history__summary").textContent)
      .toBe("All emails with HCLTech: 3 (1 Selection, 2 Interview)2 unread"));
  });

  it("logo text is the company's first word, whole", async () => {
    const { logoText } = await import("./MailCategoryPage.jsx");
    expect(logoText("Fractal Analytics Private Limited")).toBe("Fractal");
    expect(logoText("VHS Professional Services Pvt. Ltd")).toBe("VHS");
    expect(logoText("HCLTech")).toBe("HCLTech");
    expect(logoText("Onni Global Services India Pvt Ltd")).toBe("Onni");
  });
});

describe("the compact mockup", () => {
  it("counts Unread, Opened, AI Retry and All Alerts in one row; Opened asks for read mail", async () => {
    renderPage();
    const tabs = await screen.findByRole("tablist", { name: "Selection Related tabs" });
    expect(within(tabs).getAllByRole("tab").map((t) => t.querySelector(".mcp-tile__label").textContent))
      .toEqual(["Unread", "Opened", "AI Retry", "All Alerts"]);
    fireEvent.click(within(tabs).getByRole("tab", { name: /Opened/ }));
    await waitFor(() => expect(groupsRequests().at(-1).get("is_read")).toBe("true"));
  });

  it("downloads Excel or JSON from one Download menu", async () => {
    renderPage();
    const button = await screen.findByRole("button", { name: /Download/ });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);
    const menu = screen.getByRole("menu", { name: "Download format" });
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Excel (27)", "JSON (27)"]);
  });

  it("has no Mute/Unmute text toggle: mute and test are icon buttons", async () => {
    renderPage();
    const sound = await screen.findByRole("group", { name: "Selection alarm sound" });
    expect(within(sound).queryByText(/^(Mute|Unmute|ON|OFF)$/)).toBeNull();
    const mute = within(sound).getByRole("button", { name: "Mute selection alarm" });
    expect(mute).toHaveAttribute("aria-pressed", "false");
    expect(within(sound).getByRole("slider", { name: "Selection alarm volume" })).toBeInTheDocument();
    expect(within(sound).getByRole("button", { name: "Test alarm" })).toBeInTheDocument();
  });

  it("drops the helper description", async () => {
    renderPage();
    await screen.findByRole("tablist", { name: "Selection Related tabs" });
    expect(screen.queryByText(/Selected, offers, joining/)).toBeNull();
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

describe("Interview Related: one card per email", () => {
  const emailRequests = () => calls.filter((c) => c.path.includes("/mail-monitoring/emails?")).map((c) => new URL(c.path, "http://test.invalid").searchParams);

  it("shows every email as its own card, with no group counts or expand control", async () => {
    renderPage("interview");
    const cards = await screen.findAllByRole("article");
    expect(cards).toHaveLength(3);
    // Two mails of one candidate with one company are two cards.
    expect(cards.filter((c) => c.textContent.includes("Ravi Kumar"))).toHaveLength(2);
    const first = cards[0];
    expect(first.textContent).toContain("Capgemini");
    expect(first.textContent).toContain("Interview confirmed for 14 Nov");
    expect(first.textContent).toContain("Interview Confirmed");
    expect(first.querySelector(".mcp-state").textContent).toBe("Unread");
    expect(cards[1].querySelector(".mcp-state").textContent).toBe("Opened");
    expect(document.querySelector(".mcp-group, .mcp-chevron, .mcp-pill--count")).toBeNull();
    expect(screen.queryByRole("button", { name: /Show emails/ })).toBeNull();
    expect(screen.getByText("3 Emails")).toBeInTheDocument();
    expect(emailRequests().at(-1).get("classification_group")).toBe("interview");
  });

  it("opening an email reads it in the card and marks only that one read", async () => {
    renderPage("interview");
    fireEvent.click(await screen.findByRole("button", { name: /^Unread: Interview confirmed for 14 Nov/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual(["/api/mail-monitoring/notifications/i2/read"]));
    const reading = await screen.findByRole("region", { name: "Email: Interview confirmed for 14 Nov" });
    await waitFor(() => expect(reading.textContent).toContain("Body of mail i2."));
    const card = reading.closest("article");
    await waitFor(() => expect(card.querySelector(".mcp-state").textContent).toBe("Opened"));
  });

  it("tabs, search, company and status filter the emails", async () => {
    renderPage("interview");
    await screen.findAllByRole("article");
    fireEvent.click(screen.getByRole("tab", { name: /Unread/ }));
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Search notifications"), { target: { value: "Ravi" } });
    fireEvent.change(screen.getByLabelText("Company filter"), { target: { value: "hcltech" } });
    fireEvent.change(screen.getByLabelText("Status filter"), { target: { value: "interview_confirmed" } });
    await waitFor(() => {
      const last = emailRequests().at(-1);
      expect(last.get("is_read")).toBe("false");
      expect(last.get("search")).toBe("Ravi");
      expect(last.get("company_key")).toBe("hcltech");
      expect(last.get("classification")).toBe("interview_confirmed");
      expect(last.get("classification_group")).toBe("interview");
    });
    // The company options still come from the server's employer list.
    expect([...screen.getByLabelText("Company filter").options].map((o) => o.value)).toContain("hcltech");
  });

  it("Selection Related keeps its Candidate + Company cards", async () => {
    renderPage("selection");
    expect(await screen.findByRole("article", { name: "Ashok Rao, HCLTech" })).toBeInTheDocument();
    expect(calls.some((c) => c.path.includes("/mail-monitoring/emails?"))).toBe(false);
  });

  it("a company resolved from evidence says where it came from; a stored one does not", async () => {
    renderPage("interview");
    const cards = await screen.findAllByRole("article");
    expect(cards[2].querySelector(".mcp-email__company").getAttribute("title")).toBe("HCLTech — from this interview's booking");
    expect(cards[0].querySelector(".mcp-email__company").getAttribute("title")).toBe("Capgemini");
    // The company options come from the emails endpoint, resolved names included.
    expect([...screen.getByLabelText("Company filter").options].map((o) => o.textContent)).toEqual(["All Companies", "Capgemini", "HCLTech"]);
    expect(calls.some((c) => c.path.includes("/groups?"))).toBe(false);
  });
});

describe("Interview Related: cards per page", () => {
  const emailRequests = () => calls.filter((c) => c.path.includes("/mail-monitoring/emails?")).map((c) => new URL(c.path, "http://test.invalid").searchParams);
  beforeEach(() => { try { window.localStorage.removeItem("mcp.interviewPageSize"); } catch { /* none */ } });

  it("shows 10 email cards a page by default", async () => {
    renderPage("interview");
    const choice = await screen.findByRole("combobox", { name: "Interview emails per page" });
    expect(choice.value).toBe("10");
    expect([...choice.options].map((o) => o.value)).toEqual(["10", "20"]);
    await waitFor(() => expect(emailRequests().at(-1).get("limit")).toBe("10"));
  });

  it("asks for 20 from the first page when 20 is chosen, and remembers it", async () => {
    renderPage("interview");
    const choice = await screen.findByRole("combobox", { name: "Interview emails per page" });
    fireEvent.change(choice, { target: { value: "20" } });
    await waitFor(() => expect(emailRequests().at(-1).get("limit")).toBe("20"));
    expect(emailRequests().at(-1).get("offset")).toBe("0");
    expect(window.localStorage.getItem("mcp.interviewPageSize")).toBe("20");
    cleanup();
    renderPage("interview");
    expect((await screen.findByRole("combobox", { name: "Interview emails per page" })).value).toBe("20");
  });

  it("ignores a stored size that is not offered", async () => {
    window.localStorage.setItem("mcp.interviewPageSize", "500");
    renderPage("interview");
    expect((await screen.findByRole("combobox", { name: "Interview emails per page" })).value).toBe("10");
  });

  it("leaves Selection Related at 20 groups a page with no size choice", async () => {
    renderPage("selection");
    await waitFor(() => expect(groupsRequests().at(-1).get("limit")).toBe("20"));
    expect(screen.queryByRole("combobox", { name: "Interview emails per page" })).toBeNull();
  });
});
