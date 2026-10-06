/**
 * Earnings breakdown, desktop cleanup: layout only, no figure changes.
 *
 * Headings sit over their figures and are never cut, counts and amounts are
 * right-aligned, the complimentary note wraps instead of being cut, an open
 * handler says so (aria-expanded and a turned chevron), and Totals is set
 * apart from the handlers.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import EarningsBreakdown from "./EarningsBreakdown.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "EarningsBreakdown.css"), "utf8").replace(/\r\n/g, "\n");
const desktop = () => {
  const at = CSS.indexOf("/* ── Earnings, desktop cleanup");
  expect(at).toBeGreaterThan(-1);
  return CSS.slice(at);
};

const PERFORMER = {
  name: "Karthik", count: 14, completed: 3, revenue_total: 245000, commission_total: 61250,
  auto_earnings_total: 76250, salary_total: 15000, paid_out_total: 40000, net_payable: 36250,
  complimentary_total: 5000,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderOne() {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [] }) })));
  return render(
    <EarningsBreakdown
      stats={{ top_performers: [PERFORMER] }}
      month="2026-10"
      formatCurrency={(v) => `₹${Number(v).toLocaleString("en-IN")}`}
    />,
  );
}

describe("a handler row", () => {
  it("says whether its breakdown is open", () => {
    renderOne();
    const row = screen.getByText("Karthik").closest("tr");
    expect(row).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(row.className).toContain("earn-row--open");
    expect(CSS).toMatch(/\.earn-row--open \.earn-expand-icon \{ transform: rotate\(90deg\);/);
  });

  it("keeps every figure it showed before", () => {
    const { container } = renderOne();
    const cells = [...container.querySelector("tr.earn-row").querySelectorAll("td")].map((td) => td.textContent);
    expect(cells.slice(1, 4)).toEqual(["14", "3", "₹2,45,000"]);
    expect(cells[4]).toBe("₹61,250incl. ₹5,000 complimentary");
    expect(cells[6]).toBe("₹76,250");
    expect(cells[8]).toBe("+₹36,250");
    expect(container.querySelector(".earn-carry-fwd--comp")).not.toBeNull();
  });

  it("labels the Totals cell like a name cell, for its own styling", () => {
    const { container } = renderOne();
    expect(container.querySelector(".earn-foot td.earn-td--name").textContent).toBe("Totals");
  });
});

describe("desktop CSS", () => {
  it("sizes the columns to 100% and right-aligns counts with their headings", () => {
    const rules = desktop();
    const pct = (sel) => Number(rules.match(new RegExp(sel.replace(".", "\\.") + "\\s*\\{ width: ([\\d.]+)%"))[1]);
    expect(pct(".earn-th--name") + 2 * pct(".earn-th--num") + 6 * pct(".earn-th--money") + pct(".earn-th--status")).toBeCloseTo(100);
    expect(rules).toMatch(/\.earn-th--num\s*\{ width: [\d.]+%; text-align: right; \}/);
    expect(rules).toMatch(/\.earn-td--num \{ text-align: right; \}/);
  });

  it("wraps two-word headings and money notes instead of cutting them", () => {
    const rules = desktop();
    expect(rules).toMatch(/\.earn-table thead th \{[^}]*white-space: normal;/);
    expect(rules).toMatch(/\.earn-td--money \.earn-carry-fwd \{[^}]*white-space: normal;/);
  });

  it("puts Sort by beside its select at the height of Add expense", () => {
    const rules = desktop();
    expect(rules).toMatch(/\.earn-filter \{ flex-direction: row;/);
    expect(rules).toMatch(/\.earn-filter \.cand-input \{ height: 32px;/);
    expect(rules).toMatch(/\.earn-add-expense \{ min-height: 32px; height: 32px; \}/);
  });

  it("sets Totals apart and gives every status chip one size", () => {
    const rules = desktop();
    expect(rules).toMatch(/\.earn-foot td \{[^}]*border-top: 2px solid/);
    expect(rules).toMatch(/\.earn-status \{\s*min-width: 76px;/);
  });
});
