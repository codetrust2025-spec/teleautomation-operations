/**
 * Add Referrer Expense: a save whose answer never arrives.
 *
 * What happened to a real ₹42,500 payment: saving verifies the receipt first,
 * which took about a minute, and the proxy in front of the server gave up with
 * a 504 error page. The page could not read it, reported a failure, and the
 * server went on to save the expense seconds later. The payment looked unsaved,
 * so it was filed again -- four times. Each save was dated in October while the
 * history on screen was September's, which can never show it.
 *
 * Now an answer that is not the server's own is checked against what the server
 * holds: if the expense is there it is reported as saved, once; if it is not,
 * the operator is told not to save blindly. The balance also says when more is
 * recorded as paid than was owed, instead of reading ₹0.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PayoutModal from "./PayoutModal.jsx";

class FakeEventSource {
  static instances = [];
  constructor(url) { this.url = url; FakeEventSource.instances.push(this); }
  close() {}
}

const inr = (value) => `₹${Number(value).toLocaleString("en-IN")}`;
const json = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
// What the proxy returns when it gives up: an HTML page, not the server's JSON.
const gatewayTimeout = () => Promise.resolve({
  ok: false,
  status: 504,
  json: () => Promise.reject(new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON")),
});

// What the next screenshot shows: the amount is read off it, never typed in.
const receipt = { amount: 0 };

// Earned before any payout. September's two payouts leave ₹42,500 owed.
const GROSS = 57500;
const SEPT = [
  { id: "e-1", reference: "Thrilok", amount: 5000, category: "commission", note: "need", date: "2026-09-11", proofs: [] },
  { id: "e-2", reference: "Thrilok", amount: 10000, category: "commission", note: "need for Docs", date: "2026-09-15", proofs: [] },
];
const THE_PAYMENT = { amount: 42500, date: "2026-10-06" };

/**
 * The modal's API. `outcome` is what happens to the save request:
 *   saved      the server answers normally
 *   gateway    the server saves it, the proxy answers 504
 *   dropped    the server saves it, the connection is cut
 *   late       the server saves it only after `lateAfter` look-ups; the proxy answers 504
 *   lost       the proxy answers 504 and the expense never exists
 *   refused    the server answers with an error of its own
 */
function stubServer({ outcome = "saved", lateAfter = 3, preloaded = [], refusal = "Select one registered referrer.", gross = GROSS } = {}) {
  const store = [...SEPT, ...preloaded].map((row) => ({ ...row }));
  const server = { store, posts: [], lookups: 0, lookupsAfterPost: 0 };
  let pending = null;
  const closing = (month) => gross - store
    .filter((row) => month === "all" || row.date.slice(0, 7) <= month)
    .reduce((sum, row) => sum + row.amount, 0);
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const value = String(url);
    const params = new URL(value, "http://localhost").searchParams;
    const month = params.get("month") || "all";
    if (value.endsWith("/referrers")) {
      return json({ status: "ok", referrers: [{ id: "referrer-thrilok", name: "Thrilok", aliases: [] }] });
    }
    if (value.includes("/candidates/stats?")) {
      return json({ status: "ok", stats: { top_performers: [{ name: "Thrilok", net_payable: closing(month) }] } });
    }
    // The server starts a reading and answers at once; the answer is collected.
    if (options.method === "POST" && value.endsWith("/handler-expenses/extract")) {
      return json({ status: "pending", read_id: "read-1" });
    }
    if (value.endsWith("/handler-expenses/extract/read-1")) {
      return json({ status: "ok", amount: receipt.amount });
    }
    if (options.method === "POST") {
      const form = options.body;
      const row = {
        id: `e-${store.length + 100}`, reference: form.get("reference"), amount: Number(form.get("amount")),
        category: form.get("category"), note: form.get("note"), date: form.get("date"), proofs: [],
      };
      server.posts.push(row);
      if (outcome === "refused") return json({ status: "error", message: refusal });
      if (outcome === "lost") return gatewayTimeout();
      if (outcome === "late") { pending = row; return gatewayTimeout(); }
      store.push(row);
      if (outcome === "gateway") return gatewayTimeout();
      if (outcome === "dropped") return Promise.reject(new TypeError("Failed to fetch"));
      return json({ status: "ok", expense: row });
    }
    if (params.get("reference")) {
      server.lookups += 1;
      if (server.posts.length) {
        server.lookupsAfterPost += 1;
        if (pending && server.lookupsAfterPost >= lateAfter) { store.push(pending); pending = null; }
      }
    }
    return json({
      status: "ok", available_months: [],
      expenses: store.filter((row) => month === "all" || row.date.startsWith(month)),
    });
  }));
  return server;
}

async function openModal(initialMonth = "2026-09", expectedOwed = 42500) {
  render(
    <PayoutModal
      handlerNames={["Thrilok"]}
      ownedSummary={{}}
      initialMonth={initialMonth}
      onClose={() => {}}
      onChanged={() => {}}
      apiBase=""
      categories={[]}
      categoryLabels={{}}
      formatCurrency={inr}
      formatDate={(value) => value}
    />,
  );
  await waitFor(() => expect(screen.getByRole("option", { name: "Thrilok" })).toBeInTheDocument());
  fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "referrer-thrilok" } });
  await waitFor(() => expect(owed()).toBe(inr(expectedOwed)));
}

const summary = () => document.querySelector(".payout-modal__summary")?.textContent || "";
const owed = () => summary().match(/(?:Currently owed|Owed at end of [^:]+):\s*(₹[\d,]+)/)?.[1];
const ownedLabel = () => summary().match(/(Currently owed|Owed at end of [^:]+):/)?.[1];
const history = () => ({
  count: document.querySelector(".payout-modal__history-summary span")?.textContent,
  total: document.querySelector(".payout-modal__history-summary strong")?.textContent,
  amounts: [...document.querySelectorAll(".payout-modal__table .payout-col--amount-positive")].map((cell) => cell.textContent),
});
const banner = () => document.querySelector(".payout-modal__success")?.textContent;
const failure = () => document.querySelector(".payout-modal__error")?.textContent;

async function fileExpense({ amount, date }) {
  receipt.amount = amount;
  if (date) fireEvent.change(screen.getByLabelText("Expense date *"), { target: { value: date } });
  fireEvent.change(document.querySelector('input[type="file"]'), {
    target: { files: [new File(["receipt"], "receipt.png", { type: "image/png" })] },
  });
  // The amount is read off the screenshot; saving waits for it.
  await waitFor(() => expect(screen.getByLabelText("Expense amount (₹) *").value).toBe(String(amount)));
  fireEvent.click(screen.getByRole("button", { name: "Save expense" }));
}

const realSetTimeout = globalThis.setTimeout;

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  window.__TA_CONFIRM_VALUE__ = { confirm: vi.fn().mockResolvedValue(true) };
  // "Today" is 6 October 2026. Only the clock is faked.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T10:00:00"));
  // The waits between look-ups (four seconds) and between polls for a reading (two) are instant here.
  vi.spyOn(globalThis, "setTimeout").mockImplementation((fn, ms, ...args) => realSetTimeout(fn, ms === 4000 || ms === 2000 ? 0 : ms, ...args));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete window.__TA_CONFIRM_VALUE__;
});

describe("the proxy gives up, but the server saves the payment", () => {
  it("reports it as saved once, with one row in October's history and nothing owed", async () => {
    const server = stubServer({ outcome: "gateway" });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(banner()).toContain(`${inr(42500)} was deducted from the amount owed`);
    expect(banner()).toContain("slow to answer, but the expense was saved once");
    expect(banner()).toContain("Oct 2026");
    expect(failure()).toBeUndefined();
    expect(server.posts).toHaveLength(1);
    await waitFor(() => expect(history().amounts).toEqual([inr(42500)]));
    expect(history().count).toBe("1 entry");
    expect(owed()).toBe(inr(0));
  });

  it("tells the operator not to save it again while it checks", async () => {
    stubServer({ outcome: "late", lateAfter: 3 });
    await openModal();
    await fileExpense(THE_PAYMENT);
    expect(await screen.findByText(/Do not save it again/)).toBeInTheDocument();
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(screen.queryByText(/Do not save it again/)).toBeNull();
  });

  it("finds a save that lands several look-ups after the timeout", async () => {
    const server = stubServer({ outcome: "late", lateAfter: 6 });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(banner()).toContain("was deducted from the amount owed"));
    expect(server.posts).toHaveLength(1);
    expect(server.store.filter((row) => row.amount === 42500)).toHaveLength(1);
    await waitFor(() => expect(history().amounts).toEqual([inr(42500)]));
  });

  it("treats a dropped connection the same way", async () => {
    const server = stubServer({ outcome: "dropped" });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(banner()).toContain("slow to answer, but the expense was saved once"));
    expect(server.posts).toHaveLength(1);
    expect(failure()).toBeUndefined();
  });

  it("never files it a second time on its own", async () => {
    const server = stubServer({ outcome: "gateway" });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(server.posts).toHaveLength(1);
    expect(server.store.filter((row) => row.amount === 42500)).toHaveLength(1);
  });

  it("does not mistake an earlier identical expense for this save", async () => {
    // The same amount, referrer and date already exists: the timeout cannot be
    // read as that one having been saved just now.
    const earlier = { id: "e-earlier", reference: "Thrilok", amount: 42500, category: "commission", note: "", date: "2026-10-06", proofs: [] };
    const server = stubServer({ outcome: "lost", preloaded: [earlier], gross: GROSS + 42500 });
    await openModal("2026-10", 42500);
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(failure()).toContain("did not confirm"));
    expect(banner()).toBeUndefined();
    expect(server.posts).toHaveLength(1);
  });
});

describe("the payment really was not saved", () => {
  it("says it could not confirm the save and does not claim success", async () => {
    const server = stubServer({ outcome: "lost" });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(failure()).toContain("did not confirm"));
    expect(failure()).toContain("check the history before saving it again");
    expect(failure()).toContain("same receipt cannot be saved twice");
    expect(banner()).toBeUndefined();
    expect(server.posts).toHaveLength(1);
    expect(server.store.filter((row) => row.amount === 42500)).toHaveLength(0);
  });

  it("leaves the form as it was and lets the operator try again", async () => {
    stubServer({ outcome: "lost" });
    await openModal();
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(failure()).toBeTruthy());
    expect(screen.getByLabelText("Expense amount (₹) *").value).toBe("42500");
    expect(screen.getByRole("button", { name: "Save expense" })).toBeEnabled();
  });
});

describe("a refusal from the server is shown straight away", () => {
  it("passes the server's own message through without looking the save up", async () => {
    const server = stubServer({
      outcome: "refused",
      refusal: "This payment is already recorded (same bank reference).",
    });
    await openModal();
    const lookupsBefore = server.lookups;
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(failure()).toContain("already recorded (same bank reference)"));
    // One look-up before the save, to know what was already there; none after.
    expect(server.lookups - lookupsBefore).toBe(1);
    expect(server.lookupsAfterPost).toBe(0);
    expect(failure()).not.toContain("did not confirm");
  });
});

describe("more recorded as paid than was owed", () => {
  const FOUR = [1, 2, 3, 4].map((n) => ({
    id: `dup-${n}`, reference: "Thrilok", amount: 42500, category: "commission",
    note: n === 1 ? "Final settlemnet" : "", date: "2026-10-06", proofs: [],
  }));

  it("says so instead of reading ₹0, when one payment was filed four times", async () => {
    stubServer({ preloaded: FOUR });
    await openModal("2026-10", 0);
    expect(summary()).toContain(`Overpaid by ${inr(127500)}`);
    expect(summary()).toContain("Check for a duplicate expense");
    expect(screen.getByRole("alert")).toHaveTextContent(`Overpaid by ${inr(127500)}`);
  });

  it("says nothing of the kind when the payment is recorded once", async () => {
    stubServer({ preloaded: FOUR.slice(0, 1) });
    await openModal("2026-10", 0);
    expect(summary()).not.toContain("Overpaid");
    expect(owed()).toBe(inr(0));
    expect(history().amounts).toEqual([inr(42500)]);
  });
});

describe("what the balance is called", () => {
  it("is the balance at the end of September when looking at September", async () => {
    stubServer();
    await openModal("2026-09");
    expect(ownedLabel()).toBe("Owed at end of Sept 2026");
    expect(owed()).toBe(inr(42500));
  });

  it("is what is currently owed when looking at the current month", async () => {
    stubServer({ preloaded: [{ id: "paid", reference: "Thrilok", amount: 42500, category: "commission", note: "", date: "2026-10-06", proofs: [] }] });
    await openModal("2026-10", 0);
    expect(ownedLabel()).toBe("Currently owed");
  });

  it("changes to the current month's label once an October payment moves the modal there", async () => {
    stubServer({ outcome: "saved" });
    await openModal("2026-09");
    expect(ownedLabel()).toBe("Owed at end of Sept 2026");
    await fileExpense(THE_PAYMENT);
    await waitFor(() => expect(ownedLabel()).toBe("Currently owed"));
    await waitFor(() => expect(owed()).toBe(inr(0)));
    expect(banner()).toContain("Currently owed: ₹0");
  });
});
