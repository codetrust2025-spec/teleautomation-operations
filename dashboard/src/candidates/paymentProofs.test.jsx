import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { PaymentProofsModal } from "./candidatesModule.jsx";
import { normalizePaymentProofs } from "./paymentProofs.js";

function apiResponse(candidate) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({ status: "ok", candidate }),
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("normalizePaymentProofs", () => {
  it("normalizes current, legacy, nested, and alternate URL fields without duplicates", () => {
    const proofs = normalizePaymentProofs({
      candidate_id: "candidate-1",
      payment_proofs: [
        { id: "one", url: "/proofs/one" },
        { id: "deleted", url: "/proofs/deleted", deleted_at: "2026-07-28" },
      ],
      paymentScreenshots: [{ proofId: "two", imageUrl: "/proofs/two" }],
      payments: [
        {
          payment_id: "payment-3",
          screenshots: [
            { attachment_id: "three", signedUrl: "https://files.test/three" },
            { attachment_id: "duplicate", signedUrl: "https://files.test/three" },
          ],
        },
      ],
    });

    expect(proofs.map((proof) => proof.id)).toEqual(["one", "two", "three"]);
    expect(proofs[0].candidateId).toBe("candidate-1");
    expect(proofs[2]).toMatchObject({
      paymentId: "payment-3",
      url: "https://files.test/three",
    });
  });

  it("does not mix slot screenshots into payment proofs", () => {
    const proofs = normalizePaymentProofs({
      attachments: [
        { id: "payment", attachment_type: "payment_proof", fileUrl: "/payment" },
        {
          id: "slot",
          attachment_type: "slot_screenshot_proof",
          fileUrl: "/slot",
        },
      ],
    });

    expect(proofs).toHaveLength(1);
    expect(proofs[0].id).toBe("payment");
  });

  it("supports proof arrays returned directly under a response data field", () => {
    const proofs = normalizePaymentProofs({
      data: [
        { proof_id: "legacy-one", file_path: "/legacy/one.jpg" },
        { proof_id: "legacy-two", storagePath: "/legacy/two.jpg" },
      ],
    });

    expect(proofs.map((proof) => proof.id)).toEqual([
      "legacy-one",
      "legacy-two",
    ]);
  });
});

describe("PaymentProofsModal", () => {
  it("uses the normalized API records for both count and previews", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        apiResponse({
          id: "candidate-2",
          name: "Yamini",
          paymentProofs: [
            { id: "one", fileUrl: "/proofs/one" },
            { id: "two", screenshotUrl: "/proofs/two" },
          ],
        }),
      ),
    );

    const { container } = render(
      <PaymentProofsModal
        candidate={{ id: "candidate-2", name: "Yamini", proof_count: 2 }}
        onClose={() => {}}
      />,
    );

    expect(screen.getAllByText("Loading payment proofsâ€¦")).toHaveLength(2);
    await waitFor(() =>
      expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(2),
    );
    const countChunk = [...container.querySelectorAll(".cand-payout-chunk")].find(
      (element) => element.textContent.includes("screenshots on file"),
    );
    expect(countChunk?.textContent).toContain("2");
    expect(screen.queryByText(/No payment screenshots attached/)).not.toBeInTheDocument();
  });

  it("renders complete row proofs immediately while refreshing candidate detail", () => {
    const request = deferred();
    vi.stubGlobal("fetch", vi.fn(() => request.promise));
    const { container } = render(
      <PaymentProofsModal
        candidate={{
          id: "candidate-initial",
          name: "Initial",
          payment_proofs: [{ id: "initial-proof", url: "/initial-proof" }],
        }}
        onClose={() => {}}
      />,
    );

    expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1);
    expect(screen.queryByText(/No payment screenshots attached/)).not.toBeInTheDocument();
  });

  it("distinguishes an empty successful response from an API failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => apiResponse({ id: "empty", name: "Empty" })));
    const { rerender } = render(
      <PaymentProofsModal
        candidate={{ id: "empty", name: "Empty" }}
        onClose={() => {}}
      />,
    );
    await screen.findByText("No payment screenshots attached to this candidate yet.");

    fetch.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    rerender(
      <PaymentProofsModal
        candidate={{ id: "failed", name: "Failed" }}
        onClose={() => {}}
      />,
    );
    await screen.findByText("Unable to load payment proofs. Please try again.");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("ignores a late response after switching candidates", async () => {
    const first = deferred();
    const second = deferred();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
    );
    const { container, rerender } = render(
      <PaymentProofsModal
        candidate={{ id: "candidate-a", name: "A" }}
        onClose={() => {}}
      />,
    );
    rerender(
      <PaymentProofsModal
        candidate={{ id: "candidate-b", name: "B" }}
        onClose={() => {}}
      />,
    );

    second.resolve(await apiResponse({
      id: "candidate-b",
      name: "B",
      payment_proofs: [{ id: "proof-b", url: "/proof-b" }],
    }));
    await waitFor(() =>
      expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1),
    );
    first.resolve(await apiResponse({
      id: "candidate-a",
      name: "A",
      payment_proofs: [
        { id: "proof-a1", url: "/proof-a1" },
        { id: "proof-a2", url: "/proof-a2" },
      ],
    }));
    await Promise.resolve();
    expect(container.querySelectorAll(".cand-proof-card")).toHaveLength(1);
    expect(container.querySelector('img[src$="/proof-b"]')).toBeInTheDocument();
  });

  it("supports full-image navigation, zoom, backdrop close, and Escape", async () => {
    const candidate = {
      id: "candidate-gallery",
      name: "Gallery Candidate",
      payment_proofs: [
        { id: "proof-one", url: "/proof-one.jpg", note: "First proof" },
        { id: "proof-two", url: "/proof-two.jpg", note: "Second proof" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(() => apiResponse(candidate)));
    render(
      <PaymentProofsModal candidate={candidate} onClose={() => {}} />,
    );
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /Preview/ })).toHaveLength(2),
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview First proof" }));
    expect(screen.getByRole("dialog", { name: "Payment proof preview" })).toBeInTheDocument();
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next payment proof" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(
      within(
        screen.getByRole("dialog", { name: "Payment proof preview" }),
      ).getByAltText("Second proof"),
    ).toHaveAttribute(
      "src",
      expect.stringContaining("/proof-two.jpg"),
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Payment proof preview" })).not.toBeInTheDocument();
  });
});

import { dedupeProofsForDisplay, proofPresentation } from "./paymentProofs.js";

describe("proofPresentation", () => {
  it("reads status, amount and UTR off a verified proof", () => {
    const p = proofPresentation({
      verification_state: "VERIFIED_COMPANY_PAYMENT",
      verified_amount: 20000,
      utr_number: "060288603637",
      transaction_id: "T2609131812234060873256",
    });
    expect(p.status).toMatchObject({ key: "verified", label: "Verified", tone: "ok" });
    expect(p.amountText).toBe("₹20,000");
    expect(p.reference).toEqual({ label: "UTR", value: "060288603637" });
  });

  it("calls a transaction id stored in the UTR field a transaction id", () => {
    const p = proofPresentation({ utr_number: "T2609131812234060873256" });
    expect(p.reference).toEqual({ label: "Transaction ID", value: "T2609131812234060873256" });
  });

  it("falls back to the transaction id, then the reference, then nothing", () => {
    expect(proofPresentation({ transaction_id: "T26091318122340" }).reference.label).toBe("Transaction ID");
    expect(proofPresentation({ reference_number: "R-1" }).reference).toEqual({ label: "Reference", value: "R-1" });
    expect(proofPresentation({}).reference.value).toBe("");
  });

  it("labels every engine state a reviewer can meet", () => {
    const label = (s) => proofPresentation({ verification_state: s }).status.label;
    expect(label("UNKNOWN_RECEIVER")).toBe("Receiver not recognised");
    expect(label("PENDING_MANUAL_REVIEW")).toBe("Needs review");
    expect(label("EXTRACTION_FAILED")).toBe("Could not read");
    expect(label("DUPLICATE_PAYMENT")).toBe("Duplicate payment");
    expect(label("REJECTED")).toBe("Rejected");
    expect(label("")).toBe("Not reviewed");
    expect(label("SOMETHING_NEW")).toBe("Needs review");
  });

  it("never invents an amount", () => {
    expect(proofPresentation({ verification_state: "UNKNOWN_RECEIVER" }).amountText).toBe("Amount not read");
  });
});

describe("dedupeProofsForDisplay", () => {
  const verified = (id, utr, extra = {}) => ({
    id,
    verification_state: "VERIFIED_COMPANY_PAYMENT",
    verified_amount: 20000,
    utr_number: utr,
    uploaded_at: `2026-09-0${id.length}`,
    ...extra,
  });

  it("shows one card for the same UTR uploaded twice, preferring the verified copy", () => {
    const legacy = { id: "old", utr_number: "111111111111", uploaded_at: "2026-06-01" };
    const good = verified("new", "111111111111");
    const { shown, hidden } = dedupeProofsForDisplay([legacy, good]);
    expect(shown).toEqual([good]);
    expect(hidden).toEqual([legacy]);
  });

  it("groups by the same screenshot checksum even when no reference was read", () => {
    const a = { id: "a", sha256: "abc123", uploaded_at: "2026-09-01" };
    const b = { id: "b", sha256: "ABC123", uploaded_at: "2026-09-02" };
    expect(dedupeProofsForDisplay([a, b]).shown).toEqual([a]);
  });

  it("joins a transaction id stored in the UTR field of one copy", () => {
    const a = { id: "a", transaction_id: "T2609131812234060873256" };
    const b = { id: "b", utr_number: "T2609131812234060873256" };
    expect(dedupeProofsForDisplay([a, b]).shown).toHaveLength(1);
  });

  it("keeps two different payments of the same amount and date apart", () => {
    const a = verified("a1", "111111111111");
    const b = verified("b22", "222222222222");
    expect(dedupeProofsForDisplay([a, b]).shown).toEqual([a, b]);
  });

  it("keeps proofs with no identity at all, each on its own", () => {
    const a = { id: "a" };
    const b = { id: "b" };
    expect(dedupeProofsForDisplay([a, b]).shown).toEqual([a, b]);
  });

  it("alters and removes nothing", () => {
    const list = [verified("a1", "1"), verified("b22", "1")];
    const copy = JSON.parse(JSON.stringify(list));
    const { shown, hidden } = dedupeProofsForDisplay(list);
    expect(list).toEqual(copy);
    expect(shown.length + hidden.length).toBe(list.length);
  });

  it("copes with nothing", () => {
    expect(dedupeProofsForDisplay(undefined)).toEqual({ shown: [], hidden: [] });
  });
});
