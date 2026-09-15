import React, { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

describe("PaymentProofUploader", () => {
  it("shows actual byte progress, processing, and success while syncing proof count", async () => {
    const onBusyChange = vi.fn();
    const { container } = render(<Harness onBusyChange={onBusyChange} />);
    const file = new File(["payment"], "receipt.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];

    act(() => {
      xhr.upload.onprogress({
        lengthComputable: true,
        loaded: 42,
        total: 100,
      });
    });
    expect(screen.getByText("Uploading screenshot… 42%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "42",
    );

    act(() => xhr.upload.onload());
    expect(screen.getByText("Processing screenshot…")).toBeInTheDocument();

    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "ok",
      candidate: {
        id: "candidate-1",
        payment_proofs: [
          {
            id: "proof-1",
            attachment_type: "payment_proof",
            original_name: "receipt.png",
            size: file.size,
            url: "/candidates/candidate-1/proofs/proof-1",
          },
        ],
      },
    });
    act(() => xhr.onload());

    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenCalledWith(true);
    await waitFor(() =>
      expect(onBusyChange).toHaveBeenLastCalledWith(false),
    );
  });

  it("never shows the uploaded file's name, while uploading or after", async () => {
    // A phone names a screenshot after a timestamp or a hash; it tells an
    // operator nothing and was printed, and put in hover titles and labels.
    const name = "IMG-20260915-WA0012.png";
    const exposed = () =>
      document.body.textContent.includes(name) ||
      [...document.querySelectorAll("[aria-label], [title], [alt]")].some((el) =>
        ["aria-label", "title", "alt"].some((attr) => (el.getAttribute(attr) || "").includes(name)),
      );
    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(["payment"], name, { type: "image/png" })] },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];
    act(() => xhr.upload.onprogress({ lengthComputable: true, loaded: 10, total: 100 }));
    expect(screen.getByText("Payment screenshot")).toBeInTheDocument();
    expect(exposed()).toBe(false);

    act(() => xhr.upload.onload());
    expect(exposed()).toBe(false);

    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "ok",
      candidate: {
        id: "candidate-1",
        payment_proofs: [{ id: "proof-1", attachment_type: "payment_proof", original_name: name,
                           url: "/candidates/candidate-1/proofs/proof-1" }],
      },
    });
    act(() => xhr.onload());
    await waitFor(() => expect(screen.getByText("Screenshot uploaded successfully")).toBeInTheDocument());
    expect(exposed()).toBe(false);
  });

  it("cancels an active request without saving a proof", async () => {
    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: {
        files: [new File(["payment"], "cancel.png", { type: "image/png" })],
      },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.getByText("Upload cancelled")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Screenshot uploaded successfully")).toBeNull();
  });

  it("refreshes before retry so a timed-out committed proof is not duplicated", async () => {
    const { container } = render(<Harness />);
    const file = new File(["payment"], "saved.png", { type: "image/png" });
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    act(() => FakeXMLHttpRequest.instances[0].ontimeout());
    await waitFor(() =>
      expect(screen.getByText("Upload failed")).toBeInTheDocument(),
    );

    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        candidate: {
          payment_proofs: [
            {
              id: "already-saved",
              attachment_type: "payment_proof",
              original_name: file.name,
              size: file.size,
              url: "/candidates/candidate-1/proofs/already-saved",
            },
          ],
        },
      }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );
    expect(FakeXMLHttpRequest.instances).toHaveLength(1);
    expect(fetch).toHaveBeenCalledWith("/candidates/candidate-1", {
      credentials: "include",
    });
  });
});
