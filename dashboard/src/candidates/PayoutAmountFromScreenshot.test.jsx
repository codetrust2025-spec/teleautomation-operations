/**
 * Add Referrer Expense: the amount comes from the screenshot.
 *
 * Attaching the payment screenshot reads the amount off it and fills Expense
 * Amount; nothing is typed. The figure is shown for confirmation and is the
 * amount saved. If the screenshot cannot be read, or reads ambiguously, there is
 * no amount and nothing can be saved: a guessed amount is worse than none.
 *
 * A reading takes about a minute -- as long as the proxy in front of the server
 * waits -- so the server answers the request that starts it at once and the page
 * collects the answer by polling.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PayoutModal from "./PayoutModal.jsx";

class FakeEventSource {
  constructor(url) { this.url = url; }
  close() {}
}

const inr = (value) => `₹${Number(value).toLocaleString("en-IN")}`;
const json = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
const OWED = 42500;

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

/**
 * The modal's API. Reading a screenshot is started by one request and collected by
 * others. `reads` is the answer to each reading, in turn (the last one repeats): an
 * answer, or a function returning a promise. `pending` is how many polls say "not
 * yet" first; `pollFailures` how many polls fail outright first; `start` replaces
 * the answer to the request that starts a reading.
 */
function stubServer({
  reads = [{ status: "ok", amount: 42500 }], pending = 1, pollFailures = 0, start = null, saveAnswer = null,
} = {}) {
  const queue = [...reads];
  const answers = {};
  const polled = {};
  const server = { extracts: [], polls: [], saves: [], store: [] };
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const value = String(url);
    if (value.endsWith("/referrers")) {
      return json({ status: "ok", referrers: [
        { id: "referrer-thrilok", name: "Thrilok", aliases: [] },
        { id: "referrer-venu", name: "Venu", aliases: [] },
      ] });
    }
    if (value.includes("/candidates/stats?")) {
      const paid = server.store.reduce((sum, row) => sum + row.amount, 0);
      return json({ status: "ok", stats: { top_performers: [{ name: "Thrilok", net_payable: OWED - paid }] } });
    }
    if (options.method === "POST" && value.endsWith("/handler-expenses/extract")) {
      server.extracts.push(options.body);
      if (start) return start();
      const id = `read-${server.extracts.length}`;
      answers[id] = queue.length > 1 ? queue.shift() : queue[0];
      return json({ status: "pending", read_id: id });
    }
    const poll = value.match(/\/handler-expenses\/extract\/([^/?]+)$/);
    if (poll) {
      const id = poll[1];
      server.polls.push(id);
      polled[id] = (polled[id] || 0) + 1;
      if (polled[id] <= pollFailures) return Promise.reject(new TypeError("Failed to fetch"));
      if (polled[id] - pollFailures <= pending) return json({ status: "pending" });
      const answer = answers[id];
      return typeof answer === "function" ? answer() : json(answer);
    }
    if (options.method === "POST" && value.endsWith("/handler-expenses")) {
      server.saves.push(options.body);
      if (saveAnswer) return json(saveAnswer);
      const form = options.body;
      const row = {
        id: `e-${server.store.length + 1}`, reference: form.get("reference"), amount: Number(form.get("amount")),
        category: form.get("category"), note: form.get("note"), date: form.get("date"), proofs: [],
      };
      server.store.push(row);
      return json({ status: "ok", expense: row });
    }
    return json({ status: "ok", available_months: [], expenses: server.store });
  }));
  return server;
}

async function openModal(expectedOwed = OWED) {
  render(
    <PayoutModal
      handlerNames={["Thrilok", "Venu"]}
      ownedSummary={{}}
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
  await waitFor(() => expect(screen.getByText(inr(expectedOwed), { selector: ".payout-modal__summary strong" })).toBeInTheDocument());
}

function attach(name = "receipt.png") {
  fireEvent.change(document.querySelector('input[type="file"]'), {
    target: { files: [new File([name], name, { type: "image/png" })] },
  });
}

const amountField = () => screen.getByLabelText("Expense amount (₹) *");
const saveButton = () => screen.getByRole("button", { name: "Save expense" });
const readingBox = () => document.querySelector(".payout-modal__reading");
const banner = () => document.querySelector(".payout-modal__success")?.textContent;

const realSetTimeout = globalThis.setTimeout;

beforeEach(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  window.__TA_CONFIRM_VALUE__ = { confirm: vi.fn().mockResolvedValue(true) };
  // The wait between polls for a reading is two seconds; here it is instant.
  vi.spyOn(globalThis, "setTimeout").mockImplementation((fn, ms, ...args) => realSetTimeout(fn, ms === 2000 ? 0 : ms, ...args));
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete window.__TA_CONFIRM_VALUE__;
});

describe("attaching the screenshot", () => {
  it("reads the amount off it and fills Expense Amount without anything being typed", async () => {
    const server = stubServer();
    await openModal();
    expect(amountField().value).toBe("");
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    expect(server.extracts).toHaveLength(1);
  });

  it("starts the reading at once and collects it by the id the server gave", async () => {
    const server = stubServer({ pending: 2 });
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    expect(server.extracts).toHaveLength(1);
    expect(server.polls).toEqual(["read-1", "read-1", "read-1"]);
  });

  it("asks the server to read it for this referrer, afresh", async () => {
    const server = stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(server.extracts).toHaveLength(1));
    const form = server.extracts[0];
    expect(form.get("reference")).toBe("Thrilok");
    expect(form.get("fresh")).toBe("1");
    expect(form.get("file")).toBeInstanceOf(File);
    expect(form.get("analysis_id")).toMatch(/^[0-9a-f]{32}$/);
  });

  it("says it is reading, and cannot be saved until it has", async () => {
    const gate = deferred();
    stubServer({ reads: [() => gate.promise] });
    await openModal();
    attach();
    expect(await screen.findByText("Reading the amount from the screenshot…")).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    expect(amountField().value).toBe("");
    await act(async () => gate.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", amount: 42500 }) }));
    await waitFor(() => expect(saveButton()).toBeEnabled());
  });

  it("shows the figure that was read, for confirmation", async () => {
    stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(readingBox()).toHaveTextContent("Read from the screenshot: ₹42,500"));
    expect(readingBox()).toHaveTextContent("Check that it matches the payment before saving");
  });

  it("says when the same amount appears more than once on the receipt", async () => {
    stubServer({ reads: [{ status: "ok", amount: 42500, corroborated: true }] });
    await openModal();
    attach();
    await waitFor(() => expect(readingBox()).toHaveTextContent("appears more than once"));
  });

  it("makes the amount field read-only", async () => {
    stubServer();
    await openModal();
    expect(amountField()).toHaveAttribute("readonly");
    expect(amountField()).toHaveAttribute("placeholder", "Read from the screenshot");
  });

  it("lets the operator save straight away, with nothing typed", async () => {
    const server = stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(server.saves).toHaveLength(1));
  });
});

describe("the saved amount is the one read", () => {
  it("sends the amount that was read, and says that was deducted", async () => {
    const server = stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(server.saves[0].get("amount")).toBe("42500");
    expect(banner()).toContain(`${inr(42500)} was deducted from the amount owed`);
    expect(server.store.map((row) => row.amount)).toEqual([42500]);
  });

  it("is not changed by anything typed into the field", async () => {
    const server = stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    fireEvent.change(amountField(), { target: { value: "999" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(server.saves).toHaveLength(1));
    expect(server.saves[0].get("amount")).toBe("42500");
  });

  it("names the amount read in the confirmation the operator must accept", async () => {
    stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(window.__TA_CONFIRM_VALUE__.confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: `${inr(42500)} (read from the screenshot) will be deducted from Thrilok’s outstanding amount. Continue?`,
    })));
  });

  it("saves nothing when the operator declines the confirmation", async () => {
    const server = stubServer();
    window.__TA_CONFIRM_VALUE__ = { confirm: vi.fn().mockResolvedValue(false) };
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(window.__TA_CONFIRM_VALUE__.confirm).toHaveBeenCalled());
    expect(server.saves).toHaveLength(0);
  });

  it("is still checked against what is owed", async () => {
    const server = stubServer({ reads: [{ status: "ok", amount: 50000 }] });
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("50000"));
    fireEvent.submit(document.querySelector("form.payout-modal__form-section"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Expense amount cannot exceed the current outstanding amount of ${inr(OWED)}.`,
    );
    expect(server.saves).toHaveLength(0);
  });

  it("shows the server's refusal when the amount is no longer the one on the screenshot", async () => {
    const server = stubServer({ saveAnswer: {
      status: "error",
      message: "The amount confirmed (₹42,500) is not the amount on the screenshot (₹4,250). Nothing was saved.",
    } });
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(document.querySelector(".payout-modal__error")).toHaveTextContent("Nothing was saved"));
    expect(banner()).toBeUndefined();
    expect(server.store).toHaveLength(0);
  });
});

describe("when the amount cannot be read", () => {
  const cannotRead = "The payment amount could not be read from this screenshot. Attach a clearer one.";

  it("shows why, leaves the amount empty and blocks saving", async () => {
    const server = stubServer({ reads: [{ status: "error", message: cannotRead }] });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent(cannotRead);
    expect(readingBox()).toHaveTextContent("Nothing can be saved until the amount is read");
    expect(amountField().value).toBe("");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("cannot be got round by submitting the form anyway", async () => {
    const server = stubServer({ reads: [{ status: "error", message: cannotRead }] });
    await openModal();
    attach();
    await screen.findByRole("alert");
    fireEvent.submit(document.querySelector("form.payout-modal__form-section"));
    await waitFor(() => expect(document.querySelector(".payout-modal__error")).toHaveTextContent(cannotRead));
    expect(server.saves).toHaveLength(0);
  });

  it("blocks an ambiguous amount, naming the doubt, and fills nothing in", async () => {
    const doubt = "A visible amount of ₹42,500 is exactly ten times the parsed ₹4,250. A digit was probably dropped. Attach a clearer screenshot.";
    const server = stubServer({ reads: [{ status: "error", message: doubt }] });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("exactly ten times the parsed ₹4,250");
    expect(amountField().value).toBe("");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("blocks when the server does not answer the request that starts the reading", async () => {
    const server = stubServer({ start: () => Promise.reject(new TypeError("Failed to fetch")) });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("did not answer");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("blocks when the answer to starting it is not the server's own (an error page)", async () => {
    const server = stubServer({ start: () => Promise.resolve({ ok: false, status: 504, json: () => Promise.reject(new SyntaxError("Unexpected token <")) }) });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("did not answer");
    expect(amountField().value).toBe("");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("shows a refusal of the request itself straight away, without waiting for a reading", async () => {
    const server = stubServer({ start: () => json({ status: "error", message: "Select one registered referrer before attaching the screenshot." }) });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("Select one registered referrer");
    expect(server.polls).toEqual([]);
    expect(saveButton()).toBeDisabled();
  });

  it("blocks an answer that claims success but gives no usable amount", async () => {
    const server = stubServer({ reads: [{ status: "ok" }] });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be read");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("refuses a receipt that is already recorded as soon as it is attached", async () => {
    const server = stubServer({ reads: [{
      status: "error",
      message: "This payment is already recorded as a handler expense on 2026-10-06 (same bank reference).",
      duplicate_of: { record_id: "2da6b933", matched_on: "external_id" },
    }] });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("already recorded as a handler expense");
    expect(saveButton()).toBeDisabled();
    expect(server.saves).toHaveLength(0);
  });

  it("is recovered from by attaching a clearer screenshot, which is read afresh", async () => {
    const server = stubServer({ reads: [{ status: "error", message: cannotRead }, { status: "ok", amount: 42500 }] });
    await openModal();
    attach("blurry.png");
    await screen.findByRole("alert");
    attach("clear.png");
    await waitFor(() => expect(amountField().value).toBe("42500"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(saveButton()).toBeEnabled();
    expect(server.extracts.map((form) => form.get("fresh"))).toEqual(["1", "1"]);
  });
});

describe("a newer screenshot supersedes an older one", () => {
  it("is not overwritten by the earlier one's slow answer", async () => {
    const slow = deferred();
    const server = stubServer({ reads: [() => slow.promise, { status: "ok", amount: 5000 }] });
    await openModal();
    attach("first.png");
    await waitFor(() => expect(server.polls).toContain("read-1"));
    attach("second.png");
    await waitFor(() => expect(amountField().value).toBe("5000"));
    await act(async () => slow.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "ok", amount: 42500 }) }));
    expect(amountField().value).toBe("5000");
    expect(readingBox()).toHaveTextContent("₹5,000");
  });

  it("stops collecting the earlier reading as soon as a newer screenshot is attached", async () => {
    const server = stubServer({ reads: [{ status: "ok", amount: 42500 }, { status: "ok", amount: 5000 }] });
    await openModal();
    attach("first.png");
    attach("second.png");
    await waitFor(() => expect(amountField().value).toBe("5000"));
    expect(server.polls).not.toContain("read-1");
  });
});

describe("collecting the reading", () => {
  it("keeps asking until the reading is finished, however long that takes", async () => {
    const server = stubServer({ pending: 40 });
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    expect(server.polls).toHaveLength(41);
    expect(server.extracts).toHaveLength(1);
  });

  it("is not ended by one poll that fails", async () => {
    stubServer({ pollFailures: 1 });
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is ended by a run of failed polls, which it says", async () => {
    const server = stubServer({ pollFailures: Infinity });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("stopped answering while the screenshot was being read");
    expect(server.polls).toHaveLength(5);
    expect(amountField().value).toBe("");
    expect(saveButton()).toBeDisabled();
  });

  it("gives up when the reading never finishes, and says so", async () => {
    const server = stubServer({ pending: Infinity });
    await openModal();
    attach();
    // 150 polls, each waiting on a real (if shortened) timer.
    expect(await screen.findByRole("alert", {}, { timeout: 10000 })).toHaveTextContent("taking too long");
    expect(server.polls).toHaveLength(150);
    expect(saveButton()).toBeDisabled();
  }, 15000);

  it("tells the operator when the server no longer has the reading", async () => {
    stubServer({ reads: [{ status: "error", message: "This reading is no longer available. Attach the screenshot again." }] });
    await openModal();
    attach();
    expect(await screen.findByRole("alert")).toHaveTextContent("no longer available");
    expect(saveButton()).toBeDisabled();
  });
});

describe("changing the referrer", () => {
  it("drops the reading with the screenshot, so the amount cannot outlive it", async () => {
    stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(amountField().value).toBe("42500"));
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "referrer-venu" } });
    await waitFor(() => expect(amountField().value).toBe(""));
    expect(readingBox()).toBeNull();
    expect(saveButton()).toBeDisabled();
  });
});

describe("after a save", () => {
  it("starts clean: no amount, no reading, nothing to save until the next screenshot", async () => {
    stubServer();
    await openModal();
    attach();
    await waitFor(() => expect(saveButton()).toBeEnabled());
    fireEvent.click(saveButton());
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(amountField().value).toBe("");
    expect(readingBox()).toBeNull();
    expect(saveButton()).toBeDisabled();
  });
});

describe("editing an expense", () => {
  it("keeps a typed amount, and attaching a replacement screenshot reads nothing", async () => {
    const server = stubServer();
    server.store.push({ id: "e-0", reference: "Thrilok", amount: 3000, category: "commission", note: "", date: "2026-10-06", proofs: [] });
    await openModal(OWED - 3000);
    fireEvent.click(await screen.findByTitle("Edit"));
    expect(amountField()).not.toHaveAttribute("readonly");
    expect(amountField().value).toBe("3000");
    attach();
    expect(server.extracts).toHaveLength(0);
    expect(readingBox()).toBeNull();
    fireEvent.change(amountField(), { target: { value: "3500" } });
    expect(amountField().value).toBe("3500");
  });
});
