// "Needs reading" and Opened never contradict each other (16 Nov 2026): a card
// whose 8 mails were all opened still said "Needs reading" with a check mark.
// A mail the reader could not classify says "Needs reading" while unopened and
// "Needs Review" once opened, both in the review style with the info icon.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { MailCategoryPage, isNeedsReading, needsReadingLabel } from "./MailCategoryPage.jsx";

const group = (over) => ({
  candidate_id: "c1", company_key: "", company_name: null, candidate_name: "Sakthi", candidate_email: null,
  total: 5, unread: 0, read: 5, category_total: 5, category_unread: 0, category_read: 5,
  latest_classification: "selection_needs_reading", latest_status: "Needs reading", latest_status_evidence: "classified", ...over,
});
function serve(groups) {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const body = String(url).includes("/groups?") ? { groups, total: groups.length, companies: [] }
      : { notifications: [], total: 0, summary: {} };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
beforeEach(() => serve([]));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Needs reading vs Opened", () => {
  it("names the state from whether anyone has opened the mail", () => {
    expect(isNeedsReading("selection_needs_reading")).toBe(true);
    expect(isNeedsReading("interview_needs_reading")).toBe(true);
    expect(isNeedsReading("offer_received")).toBe(false);
    expect(needsReadingLabel(true)).toBe("Needs reading");
    expect(needsReadingLabel(false)).toBe("Needs Review");
  });

  it("an all-opened card says Needs Review, in the review style, never with a check mark", async () => {
    serve([group({})]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await screen.findByRole("article", { name: "Sakthi, Company not identified" });
    const pill = card.querySelector(".mcp-pill--status");
    expect(pill.textContent).toBe("Needs Review");
    expect(pill.className).toMatch(/mcp-pill--review/);
    expect(pill.querySelector(".mcp-glyph--info")).not.toBeNull();
    expect(pill.querySelector(".mcp-glyph--check")).toBeNull();
    expect(within(card).getByLabelText("5 opened")).toBeInTheDocument();
  });

  it("a card with unopened mail says Needs reading", async () => {
    serve([group({ unread: 2, category_unread: 2, read: 3, category_read: 3 })]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await screen.findByRole("article", { name: "Sakthi, Company not identified" });
    expect(card.querySelector(".mcp-pill--status").textContent).toBe("Needs reading");
    expect(card.querySelector(".mcp-pill--new").textContent).toBe("2 unread");
  });

  it("other statuses are unchanged", async () => {
    serve([group({ latest_classification: "offer_received", latest_status: "Offer Received", latest_status_evidence: "stated" })]);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const card = await screen.findByRole("article", { name: "Sakthi, Company not identified" });
    const pill = card.querySelector(".mcp-pill--status");
    expect(pill.textContent).toBe("Offer Received");
    expect(pill.querySelector(".mcp-glyph--check")).not.toBeNull();
  });
});
