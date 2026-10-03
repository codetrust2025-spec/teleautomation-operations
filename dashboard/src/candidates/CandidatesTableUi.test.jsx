/**
 * Candidates table: sticky header, an explained tick, readable notes, a payment
 * cell with a clear hierarchy, and Edit/Delete that say what they do.
 *
 * The proof states ("Proof file lost", "Recorded — proof missing") and every
 * existing control are asserted to survive the redesign.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const confirmMock = vi.hoisted(() => vi.fn());
vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: confirmMock }) }));
vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ role: "admin", reference: "", enabled: false }),
}));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({
  consumePendingWorkOpenIntent: () => null,
}));

import { CandidatesPanel, deleteRowMessage } from "./candidatesModule.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
// Normalised: the stylesheet is checked out with CRLF on Windows.
const CSS = readFileSync(join(HERE, "..", "index.css"), "utf8").replace(/\r\n/g, "\n");

const LONG_NOTE =
  "Candidate asked to move the second round to next week because of a clash with another interview; confirm by Friday evening.";
const LONG_FOLLOW_UP =
  "Will pay the remaining amount in 7-10 days as agreed with the client, call on Monday to confirm the transfer date.";

const base = {
  stage: "in_progress",
  service_type: "profile_service",
  technology: "Java",
  reference: "Test Owner",
  date: "2026-09-10",
  logged_date: "2026-09-10",
  expected_payment: 20000,
  resume_count: 1,
  slot_count: 1,
};
const verified = {
  id: "p1",
  attachment_type: "payment_proof",
  url: "/p1",
  verification_state: "VERIFIED_COMPANY_PAYMENT",
  verified_amount: 20000,
};

const ROWS = [
  {
    ...base, id: "row-complete", name: "Complete Person", phone: "9000000801",
    payment: 20000, payment_status: "paid", payment_proofs: [verified], proof_count: 1,
    details_complete: true, notes: LONG_NOTE, follow_up: LONG_FOLLOW_UP, slot_count: 3,
  },
  {
    ...base, id: "row-lost", name: "Lost Person", phone: "9000000802",
    payment: 20000, payment_status: "paid", payment_unevidenced: true, payment_proof_files_lost: true,
    payment_proofs: [{ id: "old", attachment_type: "payment_proof", url: "/old" }], proof_count: 1,
    details_complete: false, notes: "short note",
  },
  {
    ...base, id: "row-missing", name: "Missing Person", phone: "9000000803",
    payment: 20000, payment_status: "paid", payment_unevidenced: true, payment_proofs: [], proof_count: 0,
  },
  {
    ...base, id: "row-partial", name: "Partial Person", phone: "9000000804",
    payment: 10000, payment_status: "partial", payment_proofs: [{ ...verified, id: "p2", verified_amount: 10000 }], proof_count: 1,
    details_complete: true,
  },
];

let calls;

function mockFetch({ deleteOk = true } = {}) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url, options = {}) => {
      calls.push({ url: String(url), method: options.method || "GET" });
      const body = options.method === "DELETE"
        ? { status: deleteOk ? "ok" : "error", message: "nope" }
        : { status: "ok", stats: { pending_count: 0, top_performers: [], references: [] }, candidates: ROWS };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
}

async function renderTable() {
  render(<CandidatesPanel />);
  await screen.findByText("Complete Person");
}

const rowOf = (name) => screen.getByText(name, { selector: ".cand-name" }).closest("tr");

beforeEach(() => {
  confirmMock.mockReset();
  mockFetch();
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:p"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("2. the green tick says what it means", () => {
  it("is a focusable, labelled indicator whose tooltip lists what is complete", async () => {
    await renderTable();
    const tick = rowOf("Complete Person").querySelector(".cand-row-complete");
    expect(tick).toBeTruthy();
    expect(tick).toHaveAttribute("role", "img");
    expect(tick).toHaveAttribute("tabindex", "0");
    expect(tick).toHaveAttribute("aria-label", "All required details entered");
    const tip = tick.getAttribute("data-tip");
    for (const part of ["name", "technology", "date", "phone", "reference", "resume", "payment proof"]) {
      expect(tip).toContain(part);
    }
    expect(tip).toContain("not a payment or interview status");
  });

  it("appears only on rows whose details are complete, once each", async () => {
    await renderTable();
    await new Promise((resolve) => setTimeout(resolve, 60)); // let the post-render effects run
    expect(rowOf("Lost Person").querySelector(".cand-row-complete")).toBeNull();
    expect(rowOf("Missing Person").querySelector(".cand-row-complete")).toBeNull();
    expect(rowOf("Complete Person").querySelectorAll(".cand-row-complete")).toHaveLength(1);
    expect(rowOf("Partial Person").querySelectorAll(".cand-row-complete")).toHaveLength(1);
  });

  it("is explained in the column header too", async () => {
    await renderTable();
    const hint = document.querySelector("thead .cand-th-hint");
    expect(hint.getAttribute("data-tip")).toMatch(/green ✓ after a name/);
  });
});

describe("3. notes wrap and expand cleanly", () => {
  it("keeps the whole note and follow-up in the page, never cut with an ellipsis", async () => {
    await renderTable();
    const row = rowOf("Complete Person");
    expect(row.textContent).toContain(LONG_NOTE);
    expect(row.textContent).toContain(LONG_FOLLOW_UP);
    expect(row.textContent).not.toContain("…");
    expect(row.textContent).not.toContain("...");
  });

  it("shows the full text on hover as well", async () => {
    await renderTable();
    expect(rowOf("Complete Person").querySelector(".cand-cell-note")).toHaveAttribute("title", LONG_NOTE);
  });

  it("a long note gets a Show more control that opens it in place and closes it again", async () => {
    await renderTable();
    const note = rowOf("Complete Person").querySelector(".cand-cell-note");
    expect(note.className).toContain("cand-note--long");
    expect(note.className).not.toContain("cand-note--open");
    const toggle = within(note).getByRole("button", { name: "Show more" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(note.className).toContain("cand-note--open");
    expect(within(note).getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(within(note).getByRole("button", { name: "Show less" }));
    expect(note.className).not.toContain("cand-note--open");
  });

  it("the control never opens the row's editor", async () => {
    await renderTable();
    fireEvent.click(within(rowOf("Complete Person").querySelector(".cand-cell-note")).getByRole("button", { name: "Show more" }));
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(calls.some((c) => /\/candidates\/row-complete$/.test(c.url))).toBe(false);
  });

  it("the follow-up has its own control", async () => {
    await renderTable();
    const followUp = rowOf("Complete Person").querySelector(".cand-cell-followup");
    fireEvent.click(within(followUp).getByRole("button", { name: "Show more" }));
    expect(followUp.className).toContain("cand-note--open");
  });

  it("a short note is plain text with no control", async () => {
    await renderTable();
    const note = rowOf("Lost Person").querySelector(".cand-cell-note");
    expect(note.textContent).toContain("short note");
    expect(note.className).not.toContain("cand-note--long");
    expect(within(note).queryByRole("button")).toBeNull();
  });
});

describe("4. the payment cell", () => {
  const pay = (name) => rowOf(name).querySelector(".cand-pay");

  it("reads amount, then status, then evidence, each in its own tier", async () => {
    await renderTable();
    const cell = pay("Partial Person");
    const [amount, pills] = cell.children;
    expect(amount.className).toContain("cand-pay-amount");
    expect(pills.className).toContain("cand-pay-pillrow");
    expect(cell.querySelector(".cand-pay-pill").textContent).toMatch(/10k due/i);
    const proofs = cell.querySelector(".cand-pay-proofs");
    expect(proofs.querySelector(".cand-pay-proofs-count").textContent).toBe("1 proof");
    expect(proofs.querySelector(".cand-pay-proofs-cta").textContent).toBe("View");
  });

  it("keeps the PROOF FILE LOST state", async () => {
    await renderTable();
    expect(pay("Lost Person").textContent).toContain("Proof file lost");
    expect(pay("Lost Person").querySelector(".cand-pay-pill--unevidenced")).toBeTruthy();
  });

  it("keeps the RECORDED — PROOF MISSING state", async () => {
    await renderTable();
    expect(pay("Missing Person").textContent).toContain("Recorded — proof missing");
    expect(pay("Missing Person").querySelector(".cand-pay-proofs")).toBeNull();
  });

  it("still opens the proofs from View", async () => {
    await renderTable();
    fireEvent.click(within(rowOf("Complete Person")).getByRole("button", { name: /View 1 payment screenshot/ }));
    expect(calls.some((c) => /\/candidates\/row-complete$/.test(c.url) && c.method === "GET")).toBe(true);
  });
});

describe("5. Edit and Delete say what they do, and Delete asks first", () => {
  it("both have a name for assistive technology and a visible tooltip", async () => {
    await renderTable();
    const row = rowOf("Complete Person");
    const edit = within(row).getByRole("button", { name: "Edit Complete Person" });
    const del = within(row).getByRole("button", { name: "Delete Complete Person" });
    expect(edit.getAttribute("data-tip")).toMatch(/Edit candidate/);
    expect(del.getAttribute("data-tip")).toMatch(/asked to confirm first/);
    expect(edit.className).toContain("cand-tip");
    expect(del.className).toContain("cand-tip");
  });

  it("asks, naming exactly what is removed, and deletes nothing if declined", async () => {
    confirmMock.mockResolvedValue(false);
    await renderTable();
    fireEvent.click(within(rowOf("Complete Person")).getByRole("button", { name: "Delete Complete Person" }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    const ask = confirmMock.mock.calls[0][0];
    expect(ask.title).toBe("Delete Complete Person?");
    expect(ask.variant).toBe("danger");
    expect(ask.confirmLabel).toBe("Delete row");
    expect(ask.message).toContain("profile row dated 2026-09-10");
    expect(ask.message).toContain("₹20,000 recorded");
    expect(ask.message).toContain("1 payment proof");
    expect(ask.message).toContain("2 other rows of this candidate are not affected");
    expect(ask.message).toContain("cannot be undone");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("deletes that one row when confirmed", async () => {
    confirmMock.mockResolvedValue(true);
    await renderTable();
    fireEvent.click(within(rowOf("Lost Person")).getByRole("button", { name: "Delete Lost Person" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(calls.find((c) => c.method === "DELETE").url).toMatch(/\/candidates\/row-lost$/);
  });

  it("Edit still opens the candidate", async () => {
    await renderTable();
    fireEvent.click(within(rowOf("Partial Person")).getByRole("button", { name: "Edit Partial Person" }));
    await waitFor(() => expect(calls.some((c) => /\/candidates\/row-partial$/.test(c.url))).toBe(true));
  });

  it("the confirmation message for a bare row stays short and accurate", () => {
    expect(deleteRowMessage({ service_type: "round_wise", date: "2026-09-03", payment: 0, slot_count: 1 })).toBe(
      "This permanently removes this round-wise row dated 2026-09-03. It cannot be undone.",
    );
  });
});

describe("1. the header stays visible without a second scrollbar; mobile keeps its cards", () => {
  const sticky = () => {
    const start = CSS.lastIndexOf("@media (min-width: 1200px) {\n  .cand-page > .cand-table-wrap {");
    expect(start).toBeGreaterThan(-1);
    return CSS.slice(start, start + 1700);
  };

  it("from 1200px up the page scrolls and the header sticks to it: no inner scroller", () => {
    const rules = sticky();
    expect(rules).toMatch(/\.cand-page > \.cand-table-wrap\s*{\s*overflow:\s*visible/);
    expect(rules).toMatch(/\.cand-table thead th\s*{[^}]*position:\s*sticky/);
    expect(rules).toMatch(/top:\s*-20px/); // flush with the padded page body
  });

  it("no rule bounds the table's height, which is what made the nested scrollbar", () => {
    expect(CSS).not.toMatch(/\.cand-page > \.cand-table-wrap\s*{[^}]*max-height/);
    expect(sticky()).not.toMatch(/max-height/);
  });

  it("the header is opaque, sits above the rows, and keeps its bottom edge when it sticks", () => {
    const rules = sticky();
    expect(rules).toMatch(/z-index:\s*3/);
    expect(rules).toMatch(/background:\s*#[0-9a-f]{6}/i);
    expect(rules).toMatch(/border-collapse:\s*separate/);
    expect(rules).toMatch(/box-shadow:\s*0 1px 0/);
  });

  it("a row scrolled into view or focused lands below the sticky header, not behind it", () => {
    expect(sticky()).toMatch(/scroll-margin-top:\s*56px/);
  });

  it("below 600px the table is still cards with no header, as before", () => {
    expect(CSS).toMatch(/\.cand-table thead \{ display: none; \}/);
    expect(CSS).toMatch(/\.cand-table tbody tr \{\s*display: grid;/);
  });

  it("each mobile card label names its own cell", () => {
    const label = (selector) => {
      const m = CSS.match(new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "::before \\{ content: '([^']+)'"));
      return m && m[1];
    };
    const cell = (n) => label(`.cand-page .cand-table tbody td:nth-child(${n})`);
    expect([2, 3, 4, 5, 6].map(cell)).toEqual(["Service", "Technology", "Stage", "Payment", "Date"]);
    expect(label(".cand-page .cand-table tbody td.cand-cell-phone")).toBe("Phone");
    expect(label(".cand-page .cand-table tbody td.cand-cell-ref")).toBe("Reference");
    expect(label(".cand-page .cand-table tbody td.cand-cell-resume")).toBe("Resume");
  });

  it("tooltips show on hover and on focus, and stay inside the table", () => {
    expect(CSS).toMatch(/\.cand-tip:hover\[data-tip\]::after,\s*\.cand-tip:focus\[data-tip\]::after/);
    expect(CSS).toMatch(/\.cand-tip--below\[data-tip\]::after/);
    expect(CSS).toMatch(/\.cand-tip--left\[data-tip\]::after/);
  });

  it("long notes fade out instead of ending in an ellipsis", () => {
    expect(CSS).toMatch(/\.cand-note--long:not\(\.cand-note--open\) \.cand-note__text\s*{[^}]*mask-image/);
    expect(CSS).not.toMatch(/\.cand-note\s*{[^}]*-webkit-line-clamp/);
    expect(CSS).not.toMatch(/\.cand-cell-followup\.cand-note\s*{[^}]*text-overflow:\s*ellipsis/);
  });
});
