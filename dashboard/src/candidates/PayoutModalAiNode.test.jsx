/**
 * Add / Edit Referrer Expense names the AI node verifying the screenshot.
 *
 * It used to show "Verifying screenshot" with rotating generic copy and no
 * node, and a refused screenshot left it spinning -- the timer still counting
 * beside the refusal -- because the refusal returned before the status was
 * ever told. Every path that sends the screenshot now finishes the shared node
 * status with the server's answer.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PayoutModal from "./PayoutModal.jsx";

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

const stream = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const push = (status) =>
  act(() => stream().onmessage({ data: JSON.stringify({ analysed_by: [], failed_on: [], ...status }) }));

const today = () => {
  const now = new Date();
  return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, "0"), String(now.getDate()).padStart(2, "0")].join("-");
};

const json = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

/** The modal's API; `proof` is the pending answer for the screenshot request. */
function stubServer() {
  const server = { proofRequests: [], patches: [] };
  let answerProof;
  server.proof = new Promise((resolve) => { answerProof = resolve; });
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const value = String(url);
    if (value.endsWith("/referrers")) {
      return json({ status: "ok", referrers: [{ id: "referrer-thrilok", name: "Thrilok", aliases: [] }] });
    }
    if (value.includes("/candidates/stats?")) {
      return json({ status: "ok", stats: { top_performers: [{ name: "Thrilok", net_payable: 20000 }] } });
    }
    if (options.method === "PATCH") {
      server.patches.push(value);
      return json({ status: "ok" });
    }
    if (options.method === "POST") {
      server.proofRequests.push({ url: value, body: options.body });
      return server.proof;
    }
    return json({
      status: "ok", available_months: [],
      expenses: [{ id: "exp-7", reference: "Thrilok", amount: 3000, category: "commission", note: "", date: today() }],
    });
  }));
  server.answer = (body) => act(async () => answerProof({ ok: true, json: () => Promise.resolve(body) }));
  return server;
}

async function openModal() {
  render(
    <PayoutModal
      handlerNames={["Thrilok"]}
      ownedSummary={{}}
      onClose={() => {}}
      onChanged={() => {}}
      apiBase=""
      categories={[]}
      categoryLabels={{}}
      formatCurrency={(value) => `₹${value}`}
      formatDate={(value) => value}
    />,
  );
  await waitFor(() => expect(screen.getByRole("option", { name: "Thrilok" })).toBeInTheDocument());
  fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "referrer-thrilok" } });
  await waitFor(() => expect(screen.getByText("₹20000")).toBeInTheDocument());
}

function attachScreenshot() {
  fireEvent.change(document.querySelector('input[type="file"]'), {
    target: { files: [new File(["receipt"], "receipt.png", { type: "image/png" })] },
  });
}

const RTX = { state: "done", node: "RTX 4060", analysed_by: ["RTX 4060"], failed_on: [] };

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  window.__TA_CONFIRM_VALUE__ = { confirm: vi.fn().mockResolvedValue(true) };
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.__TA_CONFIRM_VALUE__;
});

describe("add referrer expense", () => {
  it("follows the node verifying the screenshot, then says who verified it", async () => {
    const server = stubServer();
    await openModal();
    fireEvent.change(screen.getByLabelText("Expense amount (₹) *"), { target: { value: "5000" } });
    attachScreenshot();
    fireEvent.click(screen.getByRole("button", { name: "Save expense" }));

    await waitFor(() => expect(server.proofRequests).toHaveLength(1));
    const { url, body } = server.proofRequests[0];
    expect(url).toBe("/handler-expenses");
    expect(stream().url).toBe(`/public/slots/analysis/${body.get("analysis_id")}/events`);
    expect(screen.getByText("Waiting for AI node…")).toBeInTheDocument();
    expect(screen.queryByText(/verifying details|understanding your data/i)).toBeNull();

    push({ state: "running", node: "RTX 4060" });
    expect(document.querySelector(".payout-modal__ai-status").textContent).toContain("● RTX 4060 · Analysing…");

    await server.answer({ status: "ok", expense: { id: "exp-8" }, analysis: RTX });
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeInTheDocument();
    expect(screen.getByText("Expense added successfully. ₹5000 was deducted from the amount owed.")).toBeInTheDocument();
  });

  it("stops at a refusal: says it was not verified, by which node, and stops counting", async () => {
    const server = stubServer();
    await openModal();
    fireEvent.change(screen.getByLabelText("Expense amount (₹) *"), { target: { value: "5000" } });
    attachScreenshot();
    fireEvent.click(screen.getByRole("button", { name: "Save expense" }));
    await waitFor(() => expect(server.proofRequests).toHaveLength(1));
    push({ state: "running", node: "Jagadeesh" });

    await server.answer({
      status: "error",
      message: "The receiver is not present in the configured receiver registry.",
      analysis: { ...RTX, node: "Jagadeesh", analysed_by: ["Jagadeesh"] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("not present in the configured receiver registry");
    expect(screen.getByText(/^✕ Not verified · Analysed by Jagadeesh in \d+\.\ds$/)).toBeInTheDocument();
    expect(document.querySelector(".ai-node-progress__timer")).toBeNull();
    expect(document.querySelector(".ai-node-progress--active")).toBeNull();
  });
});

describe("edit referrer expense with a new screenshot", () => {
  it("follows the node verifying the replacement screenshot", async () => {
    const server = stubServer();
    await openModal();
    fireEvent.click(await screen.findByTitle("Edit"));
    attachScreenshot();
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(server.proofRequests).toHaveLength(1));
    expect(server.patches).toEqual(["/handler-expenses/exp-7"]);
    const { url, body } = server.proofRequests[0];
    expect(url).toBe("/handler-expenses/exp-7/proofs");
    expect(body.get("analysis_id")).toMatch(/^[0-9a-f]{32}$/);
    push({ state: "waiting", node: null, failed_on: ["Praveen"] });
    expect(screen.getByText("Praveen failed · waiting for another AI node…")).toBeInTheDocument();

    await server.answer({ status: "ok", proof: { id: "p-1" }, analysis: { ...RTX, failed_on: ["Praveen"] } });
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds · switched from Praveen$/)).toBeInTheDocument();
  });
});
