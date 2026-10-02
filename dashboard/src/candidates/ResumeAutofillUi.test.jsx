/**
 * Resume auto-fill in Candidate Edit.
 *
 * "Fill profile fields" used to overwrite an existing candidate's name,
 * technology, phone and email with whatever the resume said. Now only a blank
 * field is filled, a valid value is never replaced, and what the server filled
 * on upload is shown and mirrored into the form so a later Save cannot write
 * the blank back.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const CANDIDATE = {
  id: "cand-rs-1",
  name: "Asha Rao",
  phone: "9000000901",
  email: "",
  technology: "Salesforce",
  reference: "Test Owner",
  stage: "in_progress",
  service_type: "profile_service",
  ctc_percentage: 10,
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
};

const RESUME_READING = {
  is_resume: true,
  candidate_name: "Asha Rao",
  phone: "9000000999",
  email: "asha.rao@example.test",
  technology: "ServiceNow",
  confidence_score: 90,
  extraction_source: "pdf_text_ai",
};

function mockResumeUpload(payload) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("/resumes")) {
        return { ok: true, status: 200, json: async () => payload };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }),
  );
}

function renderModal(candidate = {}) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={{ ...CANDIDATE, ...candidate }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

const field = (label) => screen.getByText(label, { selector: ".cand-field-label" }).closest("label").querySelector("input, select");

async function uploadResume(container) {
  const input = Array.from(container.querySelectorAll('input[type="file"]')).find((n) =>
    (n.getAttribute("accept") || "").includes("pdf"),
  );
  const file = new File(["%PDF"], "resume.pdf", { type: "application/pdf" });
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:r"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Fill profile fields never overwrites a valid value", () => {
  it("fills only the blank email and keeps the valid phone and technology", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING });
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill profile fields/ }));

    expect(field("Email")).toHaveValue("asha.rao@example.test");
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
  });

  it("keeps an existing valid email", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: { ...RESUME_READING, email: "other@example.test" } });
    const { container } = renderModal({ email: "keep@example.test" });
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill profile fields/ }));
    expect(field("Email")).toHaveValue("keep@example.test");
  });

  it("never blanks a field because the resume left it empty", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: { ...RESUME_READING, phone: "", technology: "" } });
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill profile fields/ }));
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
  });

  it("does not replace the name of an existing candidate", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: { ...RESUME_READING, candidate_name: "Someone Entirely Else" } });
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill profile fields/ }));
    expect(field("Candidate name *")).toHaveValue("Asha Rao");
  });
});

describe("what the server filled on upload", () => {
  it("is named, and mirrored into the form so Save cannot write the blank back", async () => {
    mockResumeUpload({
      status: "ok",
      resume: { id: "r1" },
      candidate: { ...CANDIDATE, email: "asha.rao@example.test" },
      ai_extraction: RESUME_READING,
      autofill: { filled: { email: "asha.rao@example.test" }, skipped: {}, refused: "" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    await waitFor(() => expect(field("Email")).toHaveValue("asha.rao@example.test"));
    expect(await screen.findByText(/Filled from this resume: email/)).toBeInTheDocument();
    expect(screen.getByText(/existing details were kept/i)).toBeInTheDocument();
  });

  it("does not overwrite something typed while the resume was being read", async () => {
    let release;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        if (String(url).includes("/resumes")) {
          await new Promise((resolve) => { release = resolve; });
          return {
            ok: true, status: 200,
            json: async () => ({
              status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
              autofill: { filled: { email: "asha.rao@example.test" }, skipped: {}, refused: "" },
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.change(field("Email"), { target: { value: "typed@example.test" } });
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(field("Email")).toHaveValue("typed@example.test");
  });

  it("says why nothing was filled when the server refused the reading", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "the name on the resume does not match this candidate" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    expect(await screen.findByText(/Not filled automatically: the name on the resume does not match this candidate/)).toBeInTheDocument();
    expect(field("Email")).toHaveValue("");
  });

  it("stays quiet when there was nothing to fill", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    await screen.findByRole("button", { name: /Fill profile fields/ });
    expect(screen.queryByText(/Filled from this resume/)).toBeNull();
    expect(screen.queryByText(/Not filled automatically/)).toBeNull();
  });
});
