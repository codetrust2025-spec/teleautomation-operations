/**
 * Resume in Candidate Edit: Upload and View are separate, and a reading fills
 * the form by itself.
 *
 * "Fill profile fields" used to overwrite an existing candidate's name,
 * technology, phone and email with whatever the resume said, and later needed
 * a click. Now the reading is applied as it arrives:
 *  - an existing candidate: what the server saved (features/resume_autofill
 *    decides: blank or invalid values only, matching name, never another
 *    candidate's phone or email) is mirrored into the form, unless the person
 *    changed that field since opening the form;
 *  - a new candidate: the reading fills the empty fields.
 * A blank on the resume never replaces anything. A reading the server could not
 * judge, or one of low confidence, waits for "Fill empty fields".
 * View opens the saved resume in a new tab, changes nothing, and is shown only
 * when there is a saved resume.
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

const SAVED = {
  id: "r0",
  original_name: "Asha_Rao_Resume.pdf",
  size: 1258291,
  uploaded_at: "2026-09-25T06:00:00+00:00",
};

/** Every resume request answers `payload`; `hold` keeps it until released. */
function mockResumeUpload(payload, { hold = false } = {}) {
  const control = { release: null };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("resume")) {
        if (hold) await new Promise((resolve) => { control.release = resolve; });
        return { ok: true, status: 200, json: async () => payload };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }),
  );
  return control;
}

function renderModal(candidate = {}) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={candidate === null ? null : { ...CANDIDATE, ...candidate }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

const field = (label) => screen.getByText(label, { selector: ".cand-field-label" }).closest("label").querySelector("input, select");
const resumeInput = (container) =>
  Array.from(container.querySelectorAll('input[type="file"]')).find((n) => (n.getAttribute("accept") || "").includes("pdf"));

async function uploadResume(container, name = "resume.pdf") {
  const file = new File(["%PDF"], name, { type: "application/pdf" });
  fireEvent.change(resumeInput(container), { target: { files: [file] } });
}

/** The read is over: the upload button is back. */
const readingDone = () => screen.findByRole("button", { name: "Upload Resume (PDF)" });

beforeEach(() => {
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:r"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Upload and View are separate", () => {
  it("shows the saved resume with its size and date, and View opens it in a new tab", () => {
    renderModal({ resumes: [SAVED], resume_count: 1 });
    expect(screen.getByRole("button", { name: "Upload Resume (PDF)" })).toBeInTheDocument();
    expect(screen.getByText("Asha_Rao_Resume.pdf")).toBeInTheDocument();
    expect(screen.getByText("1.2 MB • Uploaded on 25 Sep 2026")).toBeInTheDocument();
    const view = screen.getByRole("link", { name: /View resume/ });
    expect(view.getAttribute("href")).toMatch(/\/candidates\/cand-rs-1\/resumes\/r0\/preview$/);
    expect(view).toHaveAttribute("target", "_blank");
    expect(view.getAttribute("rel")).toContain("noopener");
  });

  it("View sends nothing and changes nothing", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderModal({ resumes: [SAVED] });
    fireEvent.click(screen.getByRole("link", { name: /View resume/ }));
    expect(fetch).not.toHaveBeenCalled();
    expect(field("Email")).toHaveValue("");
  });

  it("shows the newest of several saved resumes", () => {
    renderModal({ resumes: [SAVED, { ...SAVED, id: "r9", original_name: "Newer.pdf", uploaded_at: "2026-10-01T06:00:00+00:00" }] });
    expect(screen.getByText("Newer.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View resume/ }).getAttribute("href")).toMatch(/resumes\/r9\/preview$/);
  });

  it("offers no View when there is no saved resume", () => {
    renderModal({ resumes: [] });
    expect(screen.queryByRole("link", { name: /View resume/ })).toBeNull();
    expect(screen.getByText(/Upload to auto-fill name, phone, email and technology/)).toBeInTheDocument();
  });

  it("a newly uploaded resume becomes the one View opens", async () => {
    mockResumeUpload({
      status: "ok", resume: { ...SAVED, id: "r2", original_name: "Fresh.pdf" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ resumes: [SAVED] });
    await uploadResume(container, "Fresh.pdf");
    expect(await screen.findByText("Fresh.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View resume/ }).getAttribute("href")).toMatch(/resumes\/r2\/preview$/);
  });
});

describe("an existing candidate: what the server filled is applied by itself", () => {
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
    expect(screen.queryByRole("button", { name: /Fill empty fields/ })).toBeNull();
    // Nothing the server did not fill moves.
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
  });

  it("replaces an invalid stored value the server corrected, when the person left it alone", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: { phone: "9000000999" }, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ phone: "12345" });
    await uploadResume(container);
    await waitFor(() => expect(field("Phone")).toHaveValue("9000000999"));
  });

  it("does not overwrite something typed while the resume was being read, and says so", async () => {
    const upload = mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: { email: "asha.rao@example.test" }, skipped: {}, refused: "" },
    }, { hold: true });
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.change(field("Email"), { target: { value: "typed@example.test" } });
    await waitFor(() => expect(upload.release).toBeTypeOf("function"));
    upload.release();
    expect(await screen.findByText("Kept what you typed: email.")).toBeInTheDocument();
    expect(field("Email")).toHaveValue("typed@example.test");
  });

  it("says why nothing was filled when the server refused the reading, and offers no way around it", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "the name on the resume does not match this candidate" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    expect(await screen.findByText(/Not filled automatically: the name on the resume does not match this candidate/)).toBeInTheDocument();
    expect(field("Email")).toHaveValue("");
    expect(screen.queryByRole("button", { name: /Fill empty fields/ })).toBeNull();
  });

  it("names a field it would not fill, and why", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: { email: "that email belongs to another candidate" }, refused: "" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    expect(await screen.findByText("Not filled: email (that email belongs to another candidate).")).toBeInTheDocument();
    expect(field("Email")).toHaveValue("");
  });

  it("stays quiet when there was nothing to fill", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    await readingDone();
    expect(screen.queryByText(/Filled from this resume/)).toBeNull();
    expect(screen.queryByText(/Not filled/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Fill empty fields/ })).toBeNull();
  });
});

describe("a reading the server could not judge waits for a click, and still never overwrites", () => {
  // Filed, but the server returned no auto-fill verdict.
  const unjudged = (reading) => ({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: reading });

  it("fills only the blank email and keeps the valid phone and technology", async () => {
    mockResumeUpload(unjudged(RESUME_READING));
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill empty fields/ }));

    expect(field("Email")).toHaveValue("asha.rao@example.test");
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
    expect(screen.getByText("Filled from this resume: email.")).toBeInTheDocument();
  });

  it("keeps an existing valid email", async () => {
    mockResumeUpload(unjudged({ ...RESUME_READING, email: "other@example.test" }));
    const { container } = renderModal({ email: "keep@example.test" });
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill empty fields/ }));
    expect(field("Email")).toHaveValue("keep@example.test");
  });

  it("never blanks a field because the resume left it empty", async () => {
    mockResumeUpload(unjudged({ ...RESUME_READING, phone: "", technology: "" }));
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill empty fields/ }));
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
  });

  it("does not replace the name of an existing candidate", async () => {
    mockResumeUpload(unjudged({ ...RESUME_READING, candidate_name: "Someone Entirely Else" }));
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.click(await screen.findByRole("button", { name: /Fill empty fields/ }));
    expect(field("Candidate name *")).toHaveValue("Asha Rao");
  });
});

describe("a new candidate: the reading fills the empty fields by itself", () => {
  const reading = (data) => ({ status: "ok", success: true, data: { ...RESUME_READING, ...data } });

  it("fills name, phone, email and technology, with progress and a note", async () => {
    mockResumeUpload(reading({ phone: "+91 90000 00999" }));
    const { container } = renderModal(null);
    await uploadResume(container, "Asha.pdf");
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Asha Rao"));
    expect(field("Phone")).toHaveValue("+919000000999");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
    expect(field("Technology")).toHaveValue("ServiceNow");
    expect(screen.getByText("Filled from this resume: name, technology, phone, email.")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/public/slots/extract-resume-ai"))).toBe(true);
    // Read, not saved: a new candidate has no row to file it on, so no View.
    expect(screen.getByText("Asha.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /View resume/ })).toBeNull();
  });

  it("keeps what was typed while the resume was being read", async () => {
    const upload = mockResumeUpload(reading({}), { hold: true });
    const { container } = renderModal(null);
    await uploadResume(container);
    fireEvent.change(field("Candidate name *"), { target: { value: "Asha R." } });
    await waitFor(() => expect(upload.release).toBeTypeOf("function"));
    upload.release();
    expect(await screen.findByText("Kept what you entered: name.")).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("Asha R.");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
  });

  it("leaves a field empty when the resume has nothing for it", async () => {
    mockResumeUpload(reading({ email: "", phone: "  " }));
    const { container } = renderModal(null);
    await uploadResume(container);
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Asha Rao"));
    expect(field("Email")).toHaveValue("");
    expect(field("Phone")).toHaveValue("");
  });

  it("a low-confidence reading is shown, not applied, until clicked", async () => {
    mockResumeUpload(reading({ confidence_score: 40 }));
    const { container } = renderModal(null);
    await uploadResume(container);
    const fill = await screen.findByRole("button", { name: /Fill empty fields/ });
    expect(screen.getByText(/Low-confidence reading/)).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("");
    fireEvent.click(fill);
    expect(field("Candidate name *")).toHaveValue("Asha Rao");
  });

  it("an unreadable file says so and fills nothing", async () => {
    mockResumeUpload({ status: "error", data: { error: "Not a resume" } });
    const { container } = renderModal(null);
    await uploadResume(container);
    expect(await screen.findByText("Not a resume")).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("");
  });
});
