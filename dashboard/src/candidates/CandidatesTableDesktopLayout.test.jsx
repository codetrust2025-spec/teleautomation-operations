/**
 * Candidates table, desktop layout.
 *
 * Set column widths (Name takes what is left) with room and spacing for
 * Service, Technology, Phone, Reference, Resume and Actions; columns that fold
 * under Name/Technology instead of scrolling sideways, and phone/referrer
 * under the name only where their columns are folded away; a two-line toolbar
 * with no empty half-lines; one row marker (balance due) explained by a
 * legend; and filters that show when they are filtering. Phones keep their
 * cards: none of this applies there.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
  { ...base, id: "r1", name: "Ram Charan M S", phone: "9000000811", resume_count: 1, needs_followup: true },
  { ...base, id: "r2", name: "ram charan m s", phone: "9000000812", reference: "Bhavana", service_type: "round_wise", technology: "React JS",
    payment: 10000, payment_status: "partial", proof_count: 1, needs_followup: true,
    payment_proofs: [{ id: "p2", attachment_type: "payment_proof", url: "/p2", verification_state: "VERIFIED_COMPANY_PAYMENT", verified_amount: 10000 }] },
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

describe("phone and referrer under the name", () => {
  it("are in every row's name cell, for the widths where their columns fold away", async () => {
    await renderTable();
    for (const [id, phone, ref] of [["r1", "9000000811", "Karthik"], ["r2", "9000000812", "Bhavana"], ["r3", "9000000804", "Karthik"]]) {
      const meta = rowById(id).querySelector(".cand-cell-name .cand-name-meta");
      expect(meta.textContent).toContain(phone);
      expect(meta.querySelector(".cand-name-meta__ref").textContent).toBe(ref);
    }
  });

  it("are hidden wherever the Phone and Reference columns show: no second copy, even for a repeated name", () => {
    expect(CSS).toMatch(/\.cand-tech-service,\n\.cand-name-meta \{ display: none; \}/);
    expect(CSS).not.toMatch(/\.cand-name-meta--dup \{ display: flex; \}/);
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

  it("folds Service type below 1700px and Phone/Reference below 1536px, never on a phone", () => {
    const service = block("@media (min-width: 600px) and (max-width: 1699px) {");
    expect(service).toMatch(/td\.cand-cell-service \{ display: none; \}/);
    expect(service).toMatch(/\.cand-tech-service \{ display: flex; \}/);
    const contact = block("@media (min-width: 600px) and (max-width: 1535px) {");
    expect(contact).toMatch(/td\.cand-cell-phone,/);
    expect(contact).toMatch(/td\.cand-cell-ref \{ display: none; \}/);
    expect(contact).toMatch(/\.cand-name-meta \{ display: flex; \}/);
  });

  it("gives Service, Phone, Reference, Resume and Actions room, and space between neighbours", () => {
    const rules = block("@media (min-width: 600px) {\n  .cand-page--candidates > .cand-table-wrap .cand-table {");
    const px = (col) => Number(rules.match(new RegExp("\\.cand-th--" + col + " \\{ width: (\\d+)px;"))[1]);
    expect(px("service")).toBeGreaterThanOrEqual(112);
    expect(px("phone")).toBeGreaterThanOrEqual(140);
    expect(px("resume")).toBeGreaterThanOrEqual(140);
    expect(px("actions")).toBeGreaterThanOrEqual(84);
    expect(rules).toMatch(/\.cand-th--ref \{ width: 9%; \}/);
    // The service tag stays inside its column; Technology and Reference start with clear space.
    expect(rules).toMatch(/td\.cand-cell-service \.cand-channel-tag \{\s*margin-left: 0;/);
    expect(rules).toMatch(/td\.cand-cell-tech,\n\s*\.cand-page--candidates \.cand-table td\.cand-cell-ref \{ padding-left: 12px; \}/);
    // The phone chip fits its column; the resume chip and the actions do not crowd.
    expect(rules).toMatch(/td\.cand-cell-phone \.cand-phone-trigger \{\s*max-width: 100%;/);
    expect(rules).toMatch(/td\.cand-cell-resume \{ padding-right: 12px; overflow: hidden; \}/);
    expect(rules).toMatch(/td\.cand-cell-actions \{ padding-left: 10px; \}/);
    expect(rules).toMatch(/td\.cand-cell-actions \.cand-btn \+ \.cand-btn \{ margin-left: 6px; \}/);
  });

  it("lays the toolbar out as two full lines: search and actions, then the filters sharing the second", () => {
    const rules = block("@media (min-width: 600px) {\n  .cand-page--candidates > .cand-table-wrap .cand-table {");
    expect(rules).toMatch(/\.cand-toolbar \{\s*flex-wrap: wrap !important;/);
    expect(rules).toMatch(/\.cand-input--search \{\s*order: 0;\s*flex: 1 1 220px;/);
    expect(rules).toMatch(/\.cand-toolbar > \.cand-btn \{ order: 1; flex: none; \}/);
    expect(rules).toMatch(/\.cand-toolbar::after \{[^}]*order: 2;[^}]*flex: 0 0 100%;/);
    expect(rules).toMatch(/> select\.cand-input,\n\s*\.cand-page--candidates > \.cand-toolbar > \.cand-toggle \{\s*order: 3;\s*flex: 1 1 0;/);
    expect(CSS).not.toContain(".cand-toolbar-actions-start { margin-left: auto; }");
  });

  it("keeps no highlight that nothing applies", () => {
    expect(CSS).not.toContain("cand-row--pending-focus");
  });
});

describe("row marks and filters", () => {
  it("marks a balance due by how much is paid, and explains the marks once", async () => {
    await renderTable();
    expect(rowById("r1").className).toContain("cand-row--due-unpaid");
    expect(rowById("r2").className).toContain("cand-row--due-partial");
    expect(rowById("r3").className).not.toMatch(/cand-row--due|cand-row--pending/);
    const legend = document.querySelector(".cand-page > .cand-table-legend");
    expect(legend.textContent).toContain("Balance due (red: nothing paid, amber: part paid)");
    expect(legend.textContent).toContain("All details entered");
    expect(legend.nextElementSibling.className).toBe("cand-table-wrap");
    const desktop = CSS.slice(CSS.indexOf("/* From 600px the balance-due bar"));
    expect(desktop).toMatch(/cand-row--due-partial \{ box-shadow: inset 3px 0 0 rgba\(245, 158, 11/);
    expect(desktop).toMatch(/cand-row--due-unpaid \{ box-shadow: inset 3px 0 0 rgba\(248, 113, 113/);
    // Phones: the legend is hidden and the bar keeps its original colour.
    expect(CSS).toMatch(/\n\.cand-table-legend \{ display: none; \}/);
  });

  it("shows Service and Stage as active when they filter, like Referrer", async () => {
    await renderTable();
    const service = screen.getByLabelText("Filter by service type");
    const stage = screen.getByLabelText("Filter by stage");
    expect(service.className).not.toContain("cand-input--active");
    expect(stage.className).not.toContain("cand-input--active");
    fireEvent.change(service, { target: { value: "round_wise" } });
    fireEvent.change(stage, { target: { value: stage.options[1].value } });
    expect(screen.getByLabelText("Filter by service type").className).toContain("cand-input--active");
    expect(screen.getByLabelText("Filter by stage").className).toContain("cand-input--active");
  });
});
