// On Selection Related, "Needs Review" is not displayed; Opened already
// describes a read mail. Keep unread "Needs reading" and real verified statuses.
// The stored classification is unchanged, and Interview Related is unaffected.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { MailCategoryPage, isNeedsReading, needsReadingLabel, selectionVisibleStatus } from "./MailCategoryPage.jsx";

const group = (over) => ({
  candidate_id: "c1", company_key: "", company_name: null, candidate_name: "Sakthi", candidate_email: null,
  total: 5, unread: 0, read: 5, category_total: 5, category_unread: 0, category_read: 5,
  latest_classification: "selection_needs_reading", latest_status: "Needs reading", latest_status_evidence: "classified", ...over,
});
function serve(groups, history = []) {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const body = String(url).includes("/groups/emails?") ? { notifications: history, total: history.length }
      : String(url).includes("/groups?") ? { groups, total: groups.length, companies: [] }
      : { notifications: [], total: 0, summary: {} };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
beforeEach(() => serve([]));

// Selection Related, 24 Nov: one card per candidate, each company's card inside
// it. Opening the candidate shows them; a single company's emails open with it.
async function companyCard(name) {
  const candidate = await screen.findByRole("article", { name: name.split(", ")[0] });
  const open = within(candidate).getByRole("button", { name: /companies and emails for/ });
  if (open.getAttribute("aria-expanded") !== "true") fireEvent.click(open);
  return screen.findByRole("article", { name });
}
const showEmails = (card) => {
  const button = within(card).getByRole("button", { name: /(Show|Hide) emails for/ });
  if (button.getAttribute("aria-expanded") !== "true") fireEvent.click(button);
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Needs reading vs Opened", () => {
  it("names the state from whether anyone has opened the mail", () => {
    expect(isNeedsReading("selection_needs_reading")).toBe(true);
    expect(isNeedsReading("interview_needs_reading")).toBe(true);
    expect(isNeedsReading("offer_received")).toBe(false);
    expect(needsReadingLabel(true)).toBe("Needs reading");
    expect(needsReadingLabel(false)).toBe("Needs Review");
    expect(selectionVisibleStatus("Needs Review", "selection")).toBe("");
    expect(selectionVisibleStatus(" Needs Review ", "selection")).toBe("");
    expect(selectionVisibleStatus("Needs Review", "interview")).toBe("Needs Review");
    expect(selectionVisibleStatus("Offer Received", "selection")).toBe("Offer Received");
  });

  it("an all-opened Selection card has no Needs Review badge", async () => {
    serve([group({})]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await companyCard("Sakthi, Company not identified");
    expect(card.querySelector(".mcp-pill--status")).toBeNull();
    expect(within(card).queryByText("Needs Review")).toBeNull();
    expect(within(card).getByLabelText("5 Selection emails").textContent).toBe("5 emails");
  });

  it("also hides a stored Needs Review label without changing its backend status", async () => {
    serve([group({ latest_classification: "selection_other", latest_status: "Needs Review" })]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await companyCard("Sakthi, Company not identified");
    expect(card.querySelector(".mcp-pill--status")).toBeNull();
  });

  it("expanded Selection emails omit Needs Review but keep Opened and verified statuses", async () => {
    serve([group({})], [
      { id: "one", candidate_id: "c1", classification: "selection_needs_reading",
        candidate_status: "Needs reading", email_subject: "Verification reminder", is_read: true,
        email_received_at: "2026-11-15T08:00:00Z" },
      { id: "two", candidate_id: "c1", classification: "offer_received",
        candidate_status: "Offer Received", email_subject: "Offer letter", is_read: true,
        email_received_at: "2026-11-14T08:00:00Z" },
    ]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await companyCard("Sakthi, Company not identified");
    showEmails(card);
    await waitFor(() => expect(within(card).getAllByRole("listitem")).toHaveLength(2));
    expect(within(card).queryByText("Needs Review")).toBeNull();
    const labels = [...card.querySelectorAll(".mcp-mail__label")].map((el) => el.textContent);
    expect(labels).toEqual(["Selection", "Selection · Offer Received"]);
    expect(within(card).getAllByText("Opened")).toHaveLength(2);
  });

  it("a card with unopened mail says Needs reading", async () => {
    serve([group({ unread: 2, category_unread: 2, read: 3, category_read: 3 })]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await companyCard("Sakthi, Company not identified");
    expect(card.querySelector(".mcp-pill--status").textContent).toBe("Needs reading");
    expect(card.querySelector(".mcp-pill--new").textContent).toBe("2 unread");
  });

  it("other statuses are unchanged", async () => {
    serve([group({ latest_classification: "offer_received", latest_status: "Offer Received", latest_status_evidence: "stated" })]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await companyCard("Sakthi, Company not identified");
    const pill = card.querySelector(".mcp-pill--status");
    expect(pill.textContent).toBe("Offer Received");
    expect(pill.querySelector(".mcp-glyph--check")).not.toBeNull();
  });
});
