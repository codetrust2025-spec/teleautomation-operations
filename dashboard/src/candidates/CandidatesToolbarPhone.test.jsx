/**
 * Candidates toolbar (owner, 24 Nov): three controls at most.
 *
 * Search, a Filters button whose popup holds type, referrer, month, stage and
 * pending only (Apply, Reset, and a count of the filters in use), and Sort.
 * The actions sit on their own line, short-labelled on a phone; the cards
 * drop their labels and empty space. jsdom performs no layout, so the CSS is
 * read as text; the fit was measured live at 390px and 1440px.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureLayout } from "../test/captureLayout.js";

const auth = vi.hoisted(() => ({ current: { role: "admin", reference: "", enabled: false } }));
vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: vi.fn() }) }));
vi.mock("../context/AuthContext.jsx", () => ({ useAuth: () => auth.current }));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({ consumePendingWorkOpenIntent: () => null }));

import { CandidatesPanel } from "./candidatesModule.jsx";
import { countActiveFilters, sortCandidates } from "./CandidatesFilterBar.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8").replace(/\r\n/g, "\n");

const ROW = {
  id: "r1", name: "Asha Rao", phone: "9000000001", stage: "in_progress", service_type: "profile_service",
  technology: "Java", reference: "Karthik", date: "2026-09-10", logged_date: "2026-09-10",
  expected_payment: 20000, payment: 0, balance_due: 20000, payment_proofs: [], proof_count: 0, resume_count: 0,
};
const ROWS = [
  ROW,
  { ...ROW, id: "r2", name: "Bala Testcase", logged_date: "2026-09-14", date: "2026-09-14", payment: 15000, balance_due: 5000 },
  { ...ROW, id: "r3", name: "Chitra Sample", logged_date: "2026-09-02", date: "2026-09-02", payment: 20000, balance_due: 0 },
];
let calls;

beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    calls.push(String(url));
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ status: "ok", stats: { pending_count: 3, top_performers: [], references: [] }, candidates: ROWS }),
    });
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  auth.current = { role: "admin", reference: "", enabled: false };
});

async function renderToolbar() {
  render(<CandidatesPanel />);
  await screen.findByText("Asha Rao");
  return document.querySelector(".cand-page--candidates > .cand-bar");
}
const actions = (toolbar) => [...toolbar.querySelectorAll(".cand-bar__actions > button.cand-btn")].map((b) => b.getAttribute("aria-label"));
/** What the ••• More menu offers (owner's mockup, 25 Nov: CSV and Expenses live there). */
const moreItems = (toolbar) => {
  fireEvent.click(within(toolbar).getByRole("button", { name: "More actions" }));
  const items = within(screen.getByRole("menu", { name: "More actions" })).getAllByRole("menuitem").map((b) => b.getAttribute("aria-label"));
  fireEvent.keyDown(document, { key: "Escape" });
  return items;
};
const names = () => [...document.querySelectorAll(".cand-table tbody tr[data-cid]")].map((tr) => tr.querySelector(".cand-name").textContent);
const lastList = () => new URL(calls.filter((u) => /\/candidates\?/.test(u)).at(-1), "http://x").searchParams;

describe("what the toolbar holds", () => {
  it("gives an admin three controls, Active, More (CSV, Expenses) and Add; every filter is in the popup", async () => {
    const toolbar = await renderToolbar();
    const controls = [...toolbar.querySelectorAll(".cand-bar__controls > *")];
    expect(controls.map((el) => el.className.split(" ")[0])).toEqual(["cand-bar__search", "cand-bar__filters", "cand-bar__sort"]);
    expect(toolbar.querySelectorAll("select")).toHaveLength(1);
    // Outside the app shell there is no header slot, so Add stays on the toolbar.
    expect(actions(toolbar)).toEqual(["Active list", "Add candidate"]);
    expect(moreItems(toolbar)).toEqual(["Download active CSV", "Total expenditure"]);
    fireEvent.click(within(toolbar).getByRole("button", { name: "Filters" }));
    const popup = screen.getByRole("dialog", { name: "Filters" });
    expect([...popup.querySelectorAll("select")].map((s) => s.getAttribute("aria-label"))).toEqual(
      ["Filter by service type", "Filter by handler / reference", "Filter by month", "Filter by stage"],
    );
    expect(within(popup).getByText("Pending payments only").closest("label").querySelector(".cand-toggle-badge").textContent).toBe("3");
    expect(within(popup).getByRole("button", { name: "Reset" })).toBeTruthy();
    expect(within(popup).getByRole("button", { name: "Apply" })).toBeTruthy();
    captureLayout("candidates-toolbar-admin");
  });

  it("gives a handler Active, More (CSV only) and Add, and no referrer filter (they are held to their own)", async () => {
    auth.current = { role: "handler", reference: "Karthik", enabled: true };
    const toolbar = await renderToolbar();
    expect(actions(toolbar)).toEqual(["Active list", "Add candidate"]);
    expect(moreItems(toolbar)).toEqual(["Download active CSV"]);
    fireEvent.click(within(toolbar).getByRole("button", { name: "Filters" }));
    expect(screen.getByRole("dialog", { name: "Filters" }).querySelectorAll("select")).toHaveLength(3);
    // Reset keeps the handler's own referrer.
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(lastList().get("reference")).toBe("Karthik"));
    captureLayout("candidates-toolbar-handler");
  });
});

describe("the Filters popup", () => {
  it("changes nothing until Apply, then counts the filters in use", async () => {
    await renderToolbar();
    const before = calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.change(screen.getByLabelText("Filter by service type"), { target: { value: "round_wise" } });
    fireEvent.click(screen.getByText("Pending payments only"));
    expect(calls.length).toBe(before);
    fireEvent.click(screen.getByRole("button", { name: "Apply (2)" }));
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();
    await waitFor(() => expect(lastList().get("service_type")).toBe("round_wise"));
    expect(lastList().get("pending_only")).toBe("1");
    expect(screen.getByRole("button", { name: "Filters, 2 in use" })).toBeTruthy();
    expect(document.querySelector(".cand-bar__summary").textContent).toContain("Round-wise · Pending only");
  });

  it("Reset clears every filter at once", async () => {
    await renderToolbar();
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.change(screen.getByLabelText("Filter by stage"), { target: { value: "dropped" } });
    fireEvent.click(screen.getByRole("button", { name: /^Apply/ }));
    await waitFor(() => expect(lastList().get("stage")).toBe("dropped"));
    fireEvent.click(screen.getByRole("button", { name: "Filters, 1 in use" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(lastList().has("stage")).toBe(false));
    expect(screen.getByRole("button", { name: "Filters" })).toBeTruthy();
  });

  it("closes on Escape without applying", async () => {
    await renderToolbar();
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.change(screen.getByLabelText("Filter by stage"), { target: { value: "dropped" } });
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();
    expect(calls.some((u) => u.includes("stage=dropped"))).toBe(false);
  });

  it("counts only what differs from the defaults, and a handler's referrer never", () => {
    const defaults = { service: "all", reference: "all", month: "2026-11", stage: "all", pending: false };
    expect(countActiveFilters(defaults, defaults)).toBe(0);
    expect(countActiveFilters({ ...defaults, month: "all", reference: "Karthik", pending: true }, defaults)).toBe(3);
    expect(countActiveFilters({ ...defaults, reference: "Karthik" }, defaults, { showReference: false })).toBe(0);
  });
});

describe("Sort", () => {
  it("orders the list newest first, then as chosen", async () => {
    await renderToolbar();
    expect(names()).toEqual(["Bala Testcase", "Asha Rao", "Chitra Sample"]);
    const sort = screen.getByRole("combobox", { name: "Sort candidates" });
    fireEvent.change(sort, { target: { value: "oldest" } });
    expect(names()).toEqual(["Chitra Sample", "Asha Rao", "Bala Testcase"]);
    fireEvent.change(sort, { target: { value: "balance" } });
    expect(names()).toEqual(["Asha Rao", "Bala Testcase", "Chitra Sample"]);
    fireEvent.change(sort, { target: { value: "paid" } });
    expect(names()).toEqual(["Chitra Sample", "Bala Testcase", "Asha Rao"]);
    fireEvent.change(sort, { target: { value: "name" } });
    expect(names()).toEqual(["Asha Rao", "Bala Testcase", "Chitra Sample"]);
  });

  it("never drops or repeats a row", () => {
    for (const sort of ["newest", "oldest", "name", "balance", "paid"]) {
      expect(sortCandidates(ROWS, sort).map((r) => r.id).sort()).toEqual(["r1", "r2", "r3"]);
    }
  });
});

describe("CSS", () => {
  const block = () => {
    const at = CSS.indexOf("/* ── Candidates page: Search · Filters · Sort, compact cards (owner, 24 Nov)");
    expect(at).toBeGreaterThan(-1);
    return CSS.slice(at, CSS.indexOf("/* ── Edit Candidate modal: proof cards, footer and scroll", at));
  };

  it("puts search, Filters and Sort on one line on a wide screen, search above the other two on a phone", () => {
    expect(block()).toContain("grid-template-columns: minmax(0, 1fr) auto minmax(150px, auto);");
    expect(block()).toMatch(/@media \(max-width: 599px\) \{[\s\S]*\.cand-bar__controls \{ grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\); \}\n\s*\.cand-bar__search \{ grid-column: 1 \/ -1; \}/);
  });

  it("raises the popup from the bottom of a phone, over a backdrop", () => {
    expect(block()).toMatch(/\.cand-filter-pop \{\n\s*position: fixed; top: auto; left: 0; right: 0; bottom: 0;/);
    expect(block()).toMatch(/\.cand-filter-backdrop \{ display: block; position: fixed; inset: 0;/);
  });

  it("lays a phone card out without labels: name and stage, technology and date, payment and resume", () => {
    expect(block()).toContain(`grid-template-areas: "name stage" "tech date" "pay resume" !important;`);
    expect(block()).toMatch(/tr\.cand-row > td::before \{ content: none !important; display: none !important; \}/);
    expect(block()).toMatch(/td\.cand-cell-service,\n[^\n]*td\.cand-cell-phone,\n[^\n]*td\.cand-cell-ref \{ display: none !important; \}/);
    expect(block()).toMatch(/\.cand-pay-pillrow \{ display: contents !important; \}/);
  });

  it("gives desktop payment badges a row of their own beside the amount", () => {
    expect(block()).toContain(".cand-page.cand-page--candidates .cand-table .cand-th--pay { width: 300px; }");
    expect(block()).toMatch(/\.cand-cell-pay \.cand-pay \{ flex-direction: row; flex-wrap: wrap;/);
  });
});
