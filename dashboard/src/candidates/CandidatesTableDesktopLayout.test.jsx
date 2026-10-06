/**
 * Candidates table, desktop layout.
 *
 * Set column widths (Name takes what is left), a visible Actions heading,
 * repeated names told apart by phone and referrer, one resume control per row,
 * and columns that fold under Name/Technology instead of scrolling sideways.
 * Phones keep their cards: the folded extras are hidden there.
 */
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: vi.fn() }) }));
vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ role: "admin", reference: "", enabled: false }),
}));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({
  consumePendingWorkOpenIntent: () => null,
}));

import { CandidatesPanel } from "./candidatesModule.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8").replace(/\r\n/g, "\n");

const base = {
  stage: "in_progress", service_type: "profile_service", technology: "Java", reference: "Karthik",
  date: "2026-09-10", logged_date: "2026-09-10", expected_payment: 20000, payment: 0,
  payment_proofs: [], proof_count: 0, resume_count: 0, slot_count: 1,
};
const ROWS = [
  { ...base, id: "r1", name: "Ram Charan M S", phone: "8328646540", resume_count: 1 },
  { ...base, id: "r2", name: "ram charan m s", phone: "9876501234", reference: "Bhavana", service_type: "round_wise", technology: "React JS" },
  { ...base, id: "r3", name: "Deepa Shetty", phone: "9000000804" },
];

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ status: "ok", stats: { pending_count: 0, top_performers: [], references: [] }, candidates: ROWS }),
  })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderTable() {
  render(<CandidatesPanel />);
  await screen.findByText("Deepa Shetty");
}
const rowById = (id) => document.querySelector(`tr[data-cid="${id}"]`);

describe("headings", () => {
  it("names the actions column and gives every column a class for its width", async () => {
    await renderTable();
    const heads = [...document.querySelectorAll(".cand-table thead th")];
    expect(heads.at(-1).textContent).toBe("Actions");
    expect(heads.map((th) => th.className.match(/cand-th--(\w+)/)?.[1])).toEqual(
      ["name", "service", "tech", "stage", "pay", "date", "phone", "ref", "resume", "actions"],
    );
  });
});

describe("a repeated name", () => {
  it("carries its phone and referrer under the name", async () => {
    await renderTable();
    for (const [id, phone, ref] of [["r1", "8328646540", "Karthik"], ["r2", "9876501234", "Bhavana"]]) {
      const meta = rowById(id).querySelector(".cand-cell-name .cand-name-meta");
      expect(meta.className).toContain("cand-name-meta--dup");
      expect(meta.textContent).toContain(phone);
      expect(meta.querySelector(".cand-name-meta__ref").textContent).toBe(ref);
    }
  });

  it("is not flagged on a unique name, whose phone shows only where its column folds away", async () => {
    await renderTable();
    const meta = rowById("r3").querySelector(".cand-name-meta");
    expect(meta.className).not.toContain("cand-name-meta--dup");
    expect(CSS).toMatch(/\.cand-tech-service,\n\.cand-name-meta \{ display: none; \}/);
  });
});

describe("the resume cell", () => {
  it("is one control either way: Upload resume, or the count with Update", async () => {
    await renderTable();
    const none = within(rowById("r3").querySelector(".cand-cell-resume")).getAllByRole("button");
    expect(none.map((b) => b.textContent)).toEqual(["Upload resume"]);
    const one = within(rowById("r1").querySelector(".cand-cell-resume")).getAllByRole("button");
    expect(one).toHaveLength(1);
    expect(one[0]).toHaveAccessibleName("1 resume: view or update");
    expect(one[0].querySelector(".cand-resume-chip__cta").textContent).toBe("Update");
  });
});

describe("the service type where its column folds away", () => {
  it("is shown under the technology", async () => {
    await renderTable();
    expect(rowById("r2").querySelector(".cand-cell-tech .cand-tech-service").textContent).toBe("Round-wise");
    expect(rowById("r1").querySelector(".cand-cell-tech .cand-tech-service").textContent).toBe("Profile-wise");
  });
});

describe("desktop CSS", () => {
  const block = (head) => {
    const at = CSS.indexOf(head);
    expect(at).toBeGreaterThan(-1);
    return CSS.slice(at, CSS.indexOf("\n}\n", at));
  };

  it("lays the table out by set widths from 600px up", () => {
    const rules = block("@media (min-width: 600px) {\n  .cand-page--candidates > .cand-table-wrap .cand-table {");
    expect(rules).toMatch(/table-layout:\s*fixed/);
    expect(rules).toMatch(/\.cand-th--pay \{ width: \d+px; \}/);
    expect(rules).toMatch(/\.cand-th--actions \{ width: \d+px; text-align: right; \}/);
    // The actions cell is a real table cell again, under its heading.
    expect(rules).toMatch(/td\.cand-cell-actions \{\s*display: table-cell;/);
  });

  it("folds Service type below 1600px and Phone/Reference below 1440px, never on a phone", () => {
    const service = block("@media (min-width: 600px) and (max-width: 1599px) {");
    expect(service).toMatch(/td\.cand-cell-service \{ display: none; \}/);
    expect(service).toMatch(/\.cand-tech-service \{ display: flex; \}/);
    const contact = block("@media (min-width: 600px) and (max-width: 1439px) {");
    expect(contact).toMatch(/td\.cand-cell-phone,/);
    expect(contact).toMatch(/td\.cand-cell-ref \{ display: none; \}/);
    expect(contact).toMatch(/\.cand-name-meta \{ display: flex; \}/);
  });

  it("wraps the toolbar onto a second line for the actions instead of scrolling it", () => {
    const rules = block("@media (min-width: 600px) {\n  .cand-page--candidates > .cand-table-wrap .cand-table {");
    expect(rules).toMatch(/\.cand-toolbar \{\s*flex-wrap: wrap !important;/);
    expect(rules).toMatch(/\.cand-toolbar::after \{[^}]*flex: 0 0 100%;/);
    expect(rules).toMatch(/\.cand-toolbar-actions-start \{ margin-left: auto; \}/);
  });

  it("keeps no highlight that nothing applies", () => {
    expect(CSS).not.toContain("cand-row--pending-focus");
  });
});
