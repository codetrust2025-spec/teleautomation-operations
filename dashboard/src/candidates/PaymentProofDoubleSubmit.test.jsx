/**
 * One chosen file must produce one upload, and a saved payment must never be
 * reported as a failure.
 *
 * Seen in production 2026-08-28. A ₹20,000 proof uploaded, verified and saved
 * correctly, and the panel then showed:
 *
 *     Upload failed
 *     This payment is already linked to an active or completed booking.
 *
 * Two requests had reached the server. nginx recorded:
 *
 *     09:15:10  POST /candidates/.../proofs  200  9933   <- created the proof
 *     09:16:08  POST /candidates/.../proofs  200   982   <- the error payload
 *
 * They look a minute apart only because the access log writes when the response
 * is sent. Both were dispatched together; the first spent ~40s in vision
 * extraction, and the second, arriving behind it, was refused by the fraud
 * check once the first request's proof existed. That message is only reachable
 * on the early-return path before a proof is stored, which is what proves a
 * second request happened rather than one request failing late.
 *
 * The record was right and the screen said it was wrong, which is the sort of
 * thing that gets a correct payment "fixed" by hand.
 *
 * The uploader does try to serialise: `M` returns early while the busy flag is
 * set. But that flag is React state, so it is stale for the rest of the tick.
 * Two change events dispatched before the re-render both read `false`, both
 * pass the guard, and both POST. The intent was right and the mechanism could
 * not carry it.
 *
 * Be clear about what the first two tests are worth. They pass against the
 * unfixed component too, and that is not for want of trying: firing the same
 * File twice is discarded by React's value tracking, `fireEvent` flushes state
 * between calls so the busy flag is already set for the second, and even a raw
 * dispatchEvent pair gets flushed by `act`. jsdom will not hand us the
 * single-task batching a browser does. They are guards on the intended
 * behaviour, not reproductions of the defect - do not read a green run here as
 * evidence the race is gone.
 *
 * The reconciliation test below is the one that failed before the fix, and it
 * is the one that pins the user-visible requirement: a payment that reached the
 * server must never be reported as a failure, whatever the transport did.
 *
 * Both halves are covered here: the second submit must not start, and if a
 * request fails anyway while the proof did reach the server, the panel must
 * reconcile to success rather than report a failure that did not happen. The
 * server-side entitlement and duplicate rules are untouched - a rejection with
 * no stored proof must still surface as an error, which is asserted last.
 */

import React, { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PaymentProofUploader } from "./candidatesModule.jsx";

class FakeXMLHttpRequest {
  static instances = [];

  constructor() {
    this.upload = {};
    this.status = 0;
    this.responseText = "";
    FakeXMLHttpRequest.instances.push(this);
  }

  open(method, url) {
    this.method = method;
    this.url = url;
  }

  send(body) {
    this.body = body;
  }

  abort() {
    this.onabort?.();
  }
}

function Harness({ onBusyChange = () => {} }) {
  const [proofs, setProofs] = useState([]);
  return (
    <PaymentProofUploader
      candidateId="candidate-1"
      proofs={proofs}
      onChange={setProofs}
      onBusyChange={onBusyChange}
    />
  );
}

const FILE = () =>
  new File(["payment"], "receipt.png", { type: "image/png" });

function storedProof(file, id = "proof-1") {
  return {
    id,
    attachment_type: "payment_proof",
    original_name: "receipt.png",
    size: file.size,
    url: `/candidates/candidate-1/proofs/${id}`,
    verification_state: "VERIFIED_COMPANY_PAYMENT",
  };
}

function okResponse(file) {
  return JSON.stringify({
    status: "ok",
    candidate: { id: "candidate-1", payment_proofs: [storedProof(file)] },
  });
}

beforeEach(() => {
  FakeXMLHttpRequest.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:payment-proof"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("payment proof double submit", () => {
  it("starts one upload when two selections arrive before a re-render", async () => {
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="file"]');

    // Dispatched natively and NOT through fireEvent: React Testing Library
    // flushes state between fireEvent calls, so the busy flag would already be
    // true for the second one and the race could never appear. A real browser
    // batches both handlers into one task with the flag still false, which is
    // the situation production hit. Distinct File objects keep React's value
    // tracking from discarding the second event.
    const dispatch = (file) => {
      Object.defineProperty(input, "files", {
        configurable: true,
        value: [file],
      });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };

    await act(async () => {
      dispatch(FILE());
      dispatch(FILE());
      await Promise.resolve();
    });

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(FakeXMLHttpRequest.instances).toHaveLength(1);
  });

  it("does not queue a second upload while one is still in flight", async () => {
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="file"]');
    const file = FILE();

    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));

    // A second selection arriving mid-flight must be ignored, not stacked.
    fireEvent.change(input, { target: { files: [file] } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(FakeXMLHttpRequest.instances).toHaveLength(1);
  });

  it("reports success when the request failed but the proof did reach the server", async () => {
    const file = FILE();
    globalThis.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        candidate: { id: "candidate-1", payment_proofs: [storedProof(file)] },
      }),
    });

    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));

    const xhr = FakeXMLHttpRequest.instances[0];
    xhr.status = 400;
    xhr.responseText = JSON.stringify({
      status: "error",
      message: "This payment is already linked to an active or completed booking.",
    });
    await act(async () => {
      xhr.onload();
    });

    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText("Upload failed")).not.toBeInTheDocument();
    expect(
      screen.queryByText(
        "This payment is already linked to an active or completed booking.",
      ),
    ).not.toBeInTheDocument();
  });

  it("still reports failure when nothing was stored", async () => {
    // The half that must not be weakened. A refusal with no proof behind it is
    // a real failure and has to stay visible, or a rejected payment reads as
    // accepted.
    const file = FILE();
    globalThis.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        candidate: { id: "candidate-1", payment_proofs: [] },
      }),
    });

    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));

    const xhr = FakeXMLHttpRequest.instances[0];
    xhr.status = 400;
    xhr.responseText = JSON.stringify({
      status: "error",
      message: "Duplicate payment screenshot.",
    });
    await act(async () => {
      xhr.onload();
    });

    await waitFor(() =>
      expect(screen.getByText("Upload failed")).toBeInTheDocument(),
    );
    expect(
      screen.getByText("Duplicate payment screenshot."),
    ).toBeInTheDocument();
  });

  it("a normal single upload still succeeds", async () => {
    const file = FILE();
    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));

    const xhr = FakeXMLHttpRequest.instances[0];
    act(() => xhr.upload.onload());
    xhr.status = 200;
    xhr.responseText = okResponse(file);
    await act(async () => {
      xhr.onload();
    });

    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );
    expect(FakeXMLHttpRequest.instances).toHaveLength(1);
    // No reconciliation request is needed when the upload itself succeeded.
    // The only other request is the AI node status the panel follows.
    const reconciliations = globalThis.fetch.mock.calls.filter(
      ([url]) => !String(url).includes("/public/slots/analysis/"),
    );
    expect(reconciliations).toHaveLength(0);
  });

  it("releases the guard so a later upload can still start", async () => {
    const file = FILE();
    const { container } = render(<Harness />);
    const input = container.querySelector('input[type="file"]');

    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];
    xhr.status = 200;
    xhr.responseText = okResponse(file);
    await act(async () => {
      xhr.onload();
    });
    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );

    const second = new File(["second"], "second.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [second] } });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(2));
  });
});
