// Selection Related (owner, 24 Nov): each candidate once, in an expandable card
// with their total alerts, company count and latest status; every company and
// its emails sit inside it. Names are invented.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { MailCategoryPage, groupsByCandidate } from "./MailCategoryPage.jsx";

const g = (over) => ({
  candidate_id: "c-asha", candidate_name: "Asha Fakename", candidate_email: "asha@example.com",
  total: 2, unread: 1, read: 1, category_total: 2, category_unread: 1, category_read: 1,
  latest_classification: "offer_received", latest_status: "Offer Received", latest_status_evidence: "stated", ...over,
});
// As the server returns them with group_by=candidate: Asha's three companies
// (newest first), then Ravi's one.
const GROUPS = [
  g({ company_key: "infosys", company_name: "Infosys", latest_at: "2026-11-20T10:00:00Z",
      latest_classification: "joining_confirmed", latest_status: "Joining Confirmed" }),
  g({ company_key: "tcs", company_name: "TCS", latest_at: "2026-11-18T10:00:00Z", category_total: 3, category_unread: 0, category_read: 3 }),
  g({ company_key: "wipro", company_name: "Wipro", latest_at: "2026-11-02T10:00:00Z", category_total: 1, category_unread: 1, category_read: 0 }),
  g({ candidate_id: "c-ravi", candidate_name: "Ravi Testcase", candidate_email: "ravi@example.com", company_key: "hcltech",
      company_name: "HCLTech", latest_at: "2026-11-10T10:00:00Z", category_total: 1, category_unread: 0, category_read: 1 }),
];

function serve(groups = GROUPS) {
  const calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    calls.push(String(url));
    const body = String(url).includes("/groups/emails?")
      ? { notifications: [{ id: `m-${calls.length}`, candidate_id: "c", classification: "offer_received", candidate_status: "Offer Received",
          email_subject: "Offer letter", is_read: true, email_received_at: "2026-11-14T08:00:00Z" }], total: 1 }
      : String(url).includes("/groups?") ? { groups, total: 2, companies: [] }
      : { notifications: [], total: 0, summary: {} };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("one card per candidate", () => {
  it("gathers a candidate's companies, sums their alerts and takes the newest company's status", () => {
    const [asha, ravi] = groupsByCandidate(GROUPS);
    expect(asha.groups.map((x) => x.company_name)).toEqual(["Infosys", "TCS", "Wipro"]);
    expect([asha.total, asha.unread, asha.opened]).toEqual([6, 2, 4]);
    expect(asha.latest.latest_status).toBe("Joining Confirmed");
    expect(ravi.groups).toHaveLength(1);
  });

  it("shows each candidate once with total alerts, company count and latest status", async () => {
    serve();
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const asha = await screen.findByRole("article", { name: "Asha Fakename" });
    expect(screen.getAllByRole("article", { name: "Asha Fakename" })).toHaveLength(1);
    expect(within(asha).getByLabelText("6 Selection alerts")).toBeInTheDocument();
    expect(asha.querySelector(".mcp-pill--companies").textContent).toBe("3 companies");
    expect(asha.querySelector(".mcp-pill--new").textContent).toBe("2 unread");
    expect(asha.querySelector(".mcp-pill--status").textContent).toBe("Joining Confirmed");
    // Each figure once: the count says what it counts, and opened is not repeated.
    expect(within(asha).getByLabelText("6 Selection alerts").textContent).toBe("6 alerts");
    expect(asha.querySelector(".mcp-group__opened")).toBeNull();
    const badges = asha.querySelector(".mcp-group__badges");
    expect(badges.parentElement.className).toContain("mcp-group__head");
    expect([...badges.children].map((b) => b.className.match(/mcp-pill--(\w+)/)[1])).toEqual(["count", "companies", "new", "status"]);
    expect(screen.getByRole("article", { name: "Ravi Testcase" }).querySelector(".mcp-pill--companies").textContent).toBe("1 company");
    expect(screen.getByText("2 Candidates")).toBeInTheDocument();
    // Folded: no company card until it is opened.
    expect(screen.queryByRole("article", { name: "Asha Fakename, Infosys" })).toBeNull();
  });

  it("opens to every company, folded, each opening to its own emails", async () => {
    const calls = serve();
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const asha = await screen.findByRole("article", { name: "Asha Fakename" });
    fireEvent.click(within(asha).getByRole("button", { name: "Show companies and emails for Asha Fakename" }));
    const companies = within(asha).getByRole("group", { name: "Asha Fakename's companies" });
    expect(within(companies).getAllByRole("article").map((a) => a.getAttribute("aria-label")))
      .toEqual(["Asha Fakename, Infosys", "Asha Fakename, TCS", "Asha Fakename, Wipro"]);
    const tcs = within(companies).getByRole("article", { name: "Asha Fakename, TCS" });
    expect(tcs.querySelector(".mcp-group__name").textContent).toBe("TCS");
    expect(within(tcs).getByLabelText("3 Selection emails")).toBeInTheDocument();
    expect(calls.some((url) => url.includes("/groups/emails?"))).toBe(false);
    fireEvent.click(within(tcs).getByRole("button", { name: "Show emails for Asha Fakename at TCS" }));
    await waitFor(() => expect(within(tcs).getAllByRole("listitem")).toHaveLength(1));
    expect(calls.find((url) => url.includes("/groups/emails?"))).toContain("company_key=tcs");
  });

  it("a single company opens straight to its emails", async () => {
    serve();
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    const ravi = await screen.findByRole("article", { name: "Ravi Testcase" });
    fireEvent.click(within(ravi).getByRole("button", { name: "Show companies and emails for Ravi Testcase" }));
    expect(await within(ravi).findByRole("list", { name: "Emails, newest first" })).toBeInTheDocument();
  });
});

describe("live Selection page (24 Nov)", () => {
  it("never leaves the list dimmed when a live update overtakes the first load", async () => {
    const { readFileSync } = await import("node:fs");
    const { dirname, join } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const here = dirname(fileURLToPath(import.meta.url));
    const page = readFileSync(join(here, "MailCategoryPage.jsx"), "utf-8");
    expect(page).toContain("if (current === version.current) setLoading(false);");
    expect(page).not.toContain("if (!silent && current === version.current) setLoading(false);");
    const css = readFileSync(join(here, "MailCategoryPage.css"), "utf-8").replace(/\r\n/g, "\n");
    // On a phone an email subject has the whole row; state and time sit under it.
    expect(css).toContain('.mcp--selection .mcp-mail__row { grid-template-columns: auto minmax(0, 1fr) auto; grid-template-areas: "icon text text" "icon state when";');
  });
});
