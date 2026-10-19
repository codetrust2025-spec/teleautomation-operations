/**
 * Candidates toolbar on a phone: every label inside its control.
 *
 * Three controls to a line left each 108-129px at 375-440px, and "Download
 * active CSV", "Total expenditure" and "Pending only" ran past their edges.
 * On six grid tracks the actions go two to a line and the filters two (three
 * when an admin's referrer filter makes four). jsdom performs no layout, so
 * this proves what renders and which rules lay it out; the fit itself was
 * measured in scripts/layout_harness.py from the captures written here.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureLayout } from "../test/captureLayout.js";

const auth = vi.hoisted(() => ({ current: { role: "admin", reference: "", enabled: false } }));
vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: vi.fn() }) }));
vi.mock("../context/AuthContext.jsx", () => ({ useAuth: () => auth.current }));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({ consumePendingWorkOpenIntent: () => null }));

import { CandidatesPanel } from "./candidatesModule.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8").replace(/\r\n/g, "\n");

const ROW = {
  id: "r1", name: "Asha Rao", phone: "9000000001", stage: "in_progress", service_type: "profile_service",
  technology: "Java", reference: "Karthik", date: "2026-09-10", logged_date: "2026-09-10",
  expected_payment: 20000, payment: 0, payment_proofs: [], proof_count: 0, resume_count: 0,
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ status: "ok", stats: { pending_count: 3, top_performers: [], references: [] }, candidates: [ROW] }),
  })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  auth.current = { role: "admin", reference: "", enabled: false };
});

async function renderToolbar() {
  render(<CandidatesPanel />);
  await screen.findByText("Asha Rao");
  return document.querySelector(".cand-page--candidates > .cand-toolbar");
}
const buttons = (toolbar) => [...toolbar.querySelectorAll(":scope > button.cand-btn")].map((b) => b.textContent.trim());

describe("what the toolbar holds", () => {
  it("gives an admin four actions and four filters", async () => {
    const toolbar = await renderToolbar();
    expect(buttons(toolbar)).toEqual(["Active list", "Download active CSV", "Total expenditure", "+ Add candidate"]);
    expect(toolbar.querySelectorAll(":scope > select.cand-input")).toHaveLength(4);
    expect(toolbar.querySelector(":scope > select:nth-of-type(4)").getAttribute("aria-label")).toBe("Filter by handler / reference");
    expect(within(toolbar).getByText("Pending only").closest("label").className).toContain("cand-toggle");
    captureLayout("candidates-toolbar-admin");
  });

  it("gives a handler three actions and three filters", async () => {
    auth.current = { role: "handler", reference: "Karthik", enabled: true };
    const toolbar = await renderToolbar();
    expect(buttons(toolbar)).toEqual(["Active list", "Download active CSV", "+ Add candidate"]);
    expect(toolbar.querySelectorAll(":scope > select.cand-input")).toHaveLength(3);
    captureLayout("candidates-toolbar-handler");
  });
});

describe("phone CSS", () => {
  const block = (head) => {
    const at = CSS.indexOf(head);
    expect(at).toBeGreaterThan(-1);
    return CSS.slice(at, CSS.indexOf("\n}\n", at));
  };
  const rules = () => block("@media (max-width: 599px) {\n  .cand-page.cand-page--candidates > .cand-toolbar {");
  const T = String.raw`\.cand-page\.cand-page--candidates > \.cand-toolbar`;

  it("lays the toolbar on six tracks, after the narrow-screen lock's three", () => {
    expect(rules()).toMatch(new RegExp(T + String.raw` \{\s*grid-template-columns: repeat\(6, minmax\(0, 1fr\)\) !important;`));
    // Both are !important at the same specificity, so the later one decides.
    const lock = block("Final narrow-screen Candidates layout lock");
    expect(lock).toContain("grid-template-columns: repeat(3, minmax(0, 1fr)) !important;");
    expect(CSS.indexOf(rules())).toBeGreaterThan(CSS.indexOf(lock));
  });

  it("puts the actions two to a line, wrapping a label rather than cutting it", () => {
    // 166px each at 375px: 12px text, 6px sides and a 4px icon gap hold "Download active CSV"
    // on one line in Verdana (an iPhone's font for this stack), and 40px stays finger-sized.
    expect(rules()).toMatch(new RegExp(T + String.raw` > \.cand-btn \{\s*grid-column: span 3 !important;\s*min-width: 0;\s*` +
      String.raw`min-height: 40px;\s*padding-inline: 6px;\s*gap: 4px;\s*font-size: 12px;\s*white-space: normal;`));
    // A handler's three: Add candidate takes the second line alone.
    expect(rules()).toMatch(new RegExp(T + String.raw` > button\.cand-btn:last-of-type:nth-of-type\(odd\) \{\s*grid-column: 1 / -1 !important;`));
  });

  it("puts the filters two to a line, three when there is a referrer filter", () => {
    expect(rules()).toMatch(new RegExp(T + String.raw` > select\.cand-input,\n\s*` + T + String.raw` > \.cand-toggle \{\s*grid-column: span 3 !important;`));
    expect(rules()).toMatch(new RegExp(T + String.raw`:has\(> select:nth-of-type\(4\)\) > select\.cand-input:not\(:nth-of-type\(3\)\) \{\s*grid-column: span 2 !important;`));
  });

  it("leaves the desktop toolbar and the Data Room filters alone", () => {
    expect(rules()).not.toMatch(/\.dr-filters|\.dr-page/);
    expect(rules().match(/^\s*\.[^{]+\{/gm).every((sel) => sel.includes(".cand-page.cand-page--candidates > .cand-toolbar"))).toBe(true);
  });
});
