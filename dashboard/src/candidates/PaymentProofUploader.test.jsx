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

/** The AI node status stream for the upload most recently followed. */
const stream = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];

function push(status) {
  act(() =>
    stream().onmessage({ data: JSON.stringify({ analysed_by: [], failed_on: [], ...status }) }),
  );
}

function Harness({ onBusyChange = () => {}, proofs: initial = [] }) {
  const [proofs, setProofs] = useState(initial);
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
  FakeEventSource.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  vi.stubGlobal("EventSource", FakeEventSource);
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

    // The bytes are in; the AI node reading them is what the row shows now,
    // with the time it has taken, instead of "Processing screenshot…".
    act(() => xhr.upload.onload());
    expect(screen.queryByText("Processing screenshot…")).toBeNull();
    expect(screen.getByText("Waiting for AI node…")).toBeInTheDocument();
    expect(document.querySelector(".ai-node-progress__timer").textContent).toMatch(/^\d+\.\ds$/);
    const analysisId = xhr.body.get("analysis_id");
    expect(analysisId).toMatch(/^[0-9a-f]{32}$/);
    expect(stream().url).toBe(`/public/slots/analysis/${analysisId}/events`);

    push({ state: "running", node: "RTX 4060" });
    expect(document.querySelector(".ai-node-progress--active").textContent).toContain("● RTX 4060 · Analysing…");

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
      analysis: { state: "done", node: "RTX 4060", analysed_by: ["RTX 4060"], failed_on: [] },
    });
    act(() => xhr.onload());

    await waitFor(() =>
      expect(
        screen.getByText("Screenshot uploaded successfully"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeInTheDocument();
    expect(document.querySelector(".ai-node-progress__timer")).toBeNull();
    expect(stream().closed).toBe(true);
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenCalledWith(true);
    await waitFor(() =>
      expect(onBusyChange).toHaveBeenLastCalledWith(false),
    );
  });

  it("names the node a refused screenshot was read by, and a failover it made", async () => {
    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(["payment"], "refused.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];
    act(() => xhr.upload.onload());

    push({ state: "running", node: "Praveen" });
    push({ state: "waiting", node: null, failed_on: ["Praveen"] });
    expect(screen.getByText("Praveen failed · waiting for another AI node…")).toBeInTheDocument();
    push({ state: "running", node: "Jagadeesh", failed_on: ["Praveen"] });
    expect(document.querySelector(".ai-node-progress--active").textContent)
      .toContain("● Jagadeesh · Analysing… · switched from Praveen");

    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "error",
      message: "The receiver is not present in the configured receiver registry.",
      analysis: { state: "done", node: "Jagadeesh", analysed_by: ["Jagadeesh"], failed_on: ["Praveen"] },
    });
    fetch.mockResolvedValue({ ok: true, json: async () => ({ status: "ok", candidate: { payment_proofs: [] } }) });
    await act(async () => xhr.onload());

    await waitFor(() => expect(screen.getByText("Upload failed")).toBeInTheDocument());
    expect(screen.getByText(/not present in the configured receiver registry/)).toBeInTheDocument();
    expect(
      screen.getByText(/^✕ Not saved · Analysed by Jagadeesh in \d+\.\ds · switched from Praveen$/),
    ).toBeInTheDocument();
  });

  it("claims no node when the request never got an answer", async () => {
    const { container } = render(<Harness />);
    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [new File(["payment"], "lost.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];
    act(() => xhr.upload.onload());
    push({ state: "running", node: "RTX 4060" });

    fetch.mockResolvedValue({ ok: true, json: async () => ({ status: "ok", candidate: { payment_proofs: [] } }) });
    await act(async () => xhr.onerror());

    await waitFor(() => expect(screen.getByText("Upload failed")).toBeInTheDocument());
    expect(document.querySelector(".ai-node-progress")).toBeNull();
    expect(screen.queryByText(/RTX 4060/)).toBeNull();
  });

  it("follows the node re-reading a replacement for lost evidence", async () => {
    const broken = {
      id: "proof-9", attachment_type: "payment_proof", file_availability: "MISSING_FILE",
      url: "/candidates/candidate-1/proofs/proof-9",
    };
    let answer;
    fetch.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    const { container } = render(<Harness proofs={[broken]} />);

    fireEvent.click(screen.getByRole("button", { name: "Re-upload proof" }));
    const replaceInput = [...container.querySelectorAll('input[type="file"]')]
      .find((input) => input.accept.includes("application/pdf"));
    fireEvent.change(replaceInput, {
      target: { files: [new File(["payment"], "again.png", { type: "image/png" })] },
    });

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe("/candidates/candidate-1/proofs/proof-9/replace");
    expect(stream().url).toBe(`/public/slots/analysis/${options.body.get("analysis_id")}/events`);
    expect(screen.getByText("Waiting for AI node…")).toBeInTheDocument();
    push({ state: "running", node: "RTX 4060" });
    expect(document.querySelector(".ai-node-progress--active").textContent).toContain("● RTX 4060 · Analysing…");

    await act(async () => answer({
      ok: true,
      json: async () => ({
        status: "ok", candidate: { payment_proofs: [{ ...broken, file_availability: "AVAILABLE" }] },
        analysis: { state: "done", node: "RTX 4060", analysed_by: ["RTX 4060"], failed_on: [] },
      }),
    }));
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeInTheDocument();
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

  it("renders a compact thumbnail preview for tall mobile screenshot and opens full preview on click", async () => {
    const { container } = render(<Harness />);
    // Simulate a tall PhonePe / Google Pay mobile screenshot (e.g. 1080x2400)
    const tallScreenshot = new File(["phonepe_1080x2400_binary_data"], "phonepe_upi_receipt.png", {
      type: "image/png",
    });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [tallScreenshot] },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));

    // The job card, compact thumbnail button, and preview image must be present
    const card = container.querySelector(".cand-proof-upload-job");
    expect(card).toBeInTheDocument();
    const thumbBtn = card.querySelector(".cand-proof-upload-thumb");
    expect(thumbBtn).toBeInTheDocument();
    const previewImg = thumbBtn.querySelector(".cand-proof-upload-preview");
    expect(previewImg).toBeInTheDocument();
    expect(previewImg.src).toContain("blob:payment-proof");

    // Clicking the compact thumbnail must open the full-size preview lightbox
    expect(document.querySelector(".cand-proof-lightbox")).toBeNull();
    fireEvent.click(thumbBtn);

    const lightbox = document.querySelector(".cand-proof-lightbox");
    expect(lightbox).toBeInTheDocument();
    const lightboxImg = lightbox.querySelector("img");
    expect(lightboxImg.src).toContain("blob:payment-proof");

    // Close the lightbox
    const closeBtn = lightbox.querySelector(".cand-proof-lightbox-close");
    fireEvent.click(closeBtn);
    expect(document.querySelector(".cand-proof-lightbox")).toBeNull();
  });

  it("opens full-size lightbox preview on clicking View for a completed upload", async () => {
    const { container } = render(<Harness />);
    const file = new File(["gpay_data"], "gpay_success.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file] },
    });

    await waitFor(() => expect(FakeXMLHttpRequest.instances).toHaveLength(1));
    const xhr = FakeXMLHttpRequest.instances[0];

    act(() => xhr.upload.onload());

    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "ok",
      candidate: {
        id: "candidate-1",
        payment_proofs: [
          {
            id: "proof-gpay",
            attachment_type: "payment_proof",
            original_name: file.name,
            size: file.size,
            url: "/candidates/candidate-1/proofs/proof-gpay",
          },
        ],
      },
    });
    act(() => xhr.onload());

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "View" })).toBeInTheDocument(),
    );

    // Clicking View opens the full-size image in the lightbox
    fireEvent.click(screen.getByRole("button", { name: "View" }));
    const lightbox = document.querySelector(".cand-proof-lightbox");
    expect(lightbox).toBeInTheDocument();
    expect(lightbox.querySelector("img").src).toContain("/candidates/candidate-1/proofs/proof-gpay");

    // Close the lightbox
    fireEvent.click(lightbox.querySelector(".cand-proof-lightbox-close"));
    expect(document.querySelector(".cand-proof-lightbox")).toBeNull();
  });

  it("keeps multiple upload jobs as compact cards with header, size, and status", async () => {
    const { container } = render(<Harness />);
    const file1 = new File(["data1"], "phonepe.png", { type: "image/png" });
    const file2 = new File(["data2"], "gpay.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]'), {
      target: { files: [file1, file2] },
    });

    await waitFor(() =>
      expect(container.querySelectorAll(".cand-proof-upload-job")).toHaveLength(2),
    );

    const jobs = container.querySelectorAll(".cand-proof-upload-job");
    // Both jobs render compact thumbnail buttons and previews
    expect(jobs[0].querySelector(".cand-proof-upload-thumb")).toBeInTheDocument();
    expect(jobs[0].querySelector(".cand-proof-upload-preview")).toBeInTheDocument();
    expect(jobs[1].querySelector(".cand-proof-upload-thumb")).toBeInTheDocument();
    expect(jobs[1].querySelector(".cand-proof-upload-preview")).toBeInTheDocument();

    // Check numbering for multiple proofs
    expect(screen.getByText("Payment screenshot 1")).toBeInTheDocument();
    expect(screen.getByText("Payment screenshot 2")).toBeInTheDocument();
  });
});
