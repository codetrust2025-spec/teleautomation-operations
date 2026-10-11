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

  it("Download, beside View, saves the stored file under its uploaded name", () => {
    renderModal({ resumes: [{ ...SAVED, note: "Latest CV" }] });
    const download = screen.getByRole("link", { name: /Download resume/ });
    // The file itself (served as an attachment), not the preview page.
    expect(download.getAttribute("href")).toMatch(/\/candidates\/cand-rs-1\/resumes\/r0$/);
    expect(download).toHaveAttribute("download", "Asha_Rao_Resume.pdf");
    expect(download.previousElementSibling).toBe(screen.getByRole("link", { name: /View resume/ }));
  });

  it("View and Download send nothing and change nothing", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderModal({ resumes: [SAVED] });
    fireEvent.click(screen.getByRole("link", { name: /View resume/ }));
    fireEvent.click(screen.getByRole("link", { name: /Download resume/ }));
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
    expect(screen.queryByRole("link", { name: /Download resume/ })).toBeNull();
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
    const download = screen.getByRole("link", { name: /Download resume/ });
    expect(download.getAttribute("href")).toMatch(/resumes\/r2$/);
    expect(download).toHaveAttribute("download", "Fresh.pdf");
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
    const { container } = renderModal({ email: "asha.rao@example.test" });
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
    expect(field("Phone")).toHaveValue("9000000999");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
    expect(field("Technology")).toHaveValue("ServiceNow");
    expect(screen.getByText("Filled from this resume: name, technology, phone, email.")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/public/slots/extract-resume-ai"))).toBe(true);
    // Read, not saved: a new candidate has no row to file it on, so no View or Download.
    expect(screen.getByText("Asha.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /View resume/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Download resume/ })).toBeNull();
  });

  it("keeps what was typed while the resume was being read", async () => {
    const upload = mockResumeUpload(reading({}), { hold: true });
    const { container } = renderModal(null);
    await uploadResume(container);
    fireEvent.change(field("Candidate name *"), { target: { value: "Asha Raghavan" } });
    await waitFor(() => expect(upload.release).toBeTypeOf("function"));
    upload.release();
    expect(await screen.findByText("Kept what you entered: name.")).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("Asha Raghavan");
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

describe("complete names and 10-digit phones (11 Oct: \"Linga Srinava\" / \"77995\" were kept)", () => {
  // Invented values in the shape of the reported resume: it reads
  // "Raghavendrarao Kolla", +91 90000 00777, React.js.
  const READ = {
    is_resume: true, candidate_name: "Raghavendrarao Kolla", phone: "+919000000777",
    email: "raghav.k@example.test", technology: "React.js", confidence_score: 85, extraction_source: "pdf_text_ai",
  };
  const ROW = {
    name: "Kolla Raghava", phone: "9000000777", email: "raghav.k@example.test", technology: "React JS",
  };
  const judged = (filled = {}, skipped = {}) => ({
    status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: READ, autofill: { filled, skipped, refused: "" },
  });

  it("the cut-short name the server completed, and a cut-short phone typed in the form, both end complete", async () => {
    mockResumeUpload(judged({ name: "Raghavendrarao Kolla" }));
    const { container } = renderModal(ROW);
    fireEvent.change(field("Phone"), { target: { value: "90000" } });
    await uploadResume(container);
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla"));
    expect(field("Phone")).toHaveValue("9000000777");
    expect(field("Email")).toHaveValue("raghav.k@example.test");
    expect(field("Technology")).toHaveValue("React JS");
    expect(screen.getByText("Filled from this resume: name, phone. Existing details were kept.")).toBeInTheDocument();
  });

  it("completes a name and phone cut short in the form even when the record is complete", async () => {
    mockResumeUpload(judged());
    const { container } = renderModal({ ...ROW, name: "Raghavendrarao Kolla" });
    fireEvent.change(field("Candidate name *"), { target: { value: "Kolla Raghava" } });
    fireEvent.change(field("Phone"), { target: { value: "900000077" } });
    await uploadResume(container);
    await waitFor(() => expect(field("Phone")).toHaveValue("9000000777"));
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
  });

  it.each(["Kolla Raghavendrarao", "Kolla Ragavendrarao"])(
    "keeps the same name in another order or spelling (%s)", async (stored) => {
      mockResumeUpload(judged());
      const { container } = renderModal({ ...ROW, name: stored });
      await uploadResume(container);
      await readingDone();
      expect(field("Candidate name *")).toHaveValue(stored);
      expect(screen.queryByText(/Filled from this resume/)).toBeNull();
      expect(screen.queryByText(/Kept what you typed/)).toBeNull();
    });

  it("keeps a valid phone the person typed, and says so", async () => {
    mockResumeUpload(judged());
    const { container } = renderModal({ ...ROW, name: "Raghavendrarao Kolla" });
    fireEvent.change(field("Phone"), { target: { value: "9000000888" } });
    await uploadResume(container);
    expect(await screen.findByText("Kept what you typed: phone.")).toBeInTheDocument();
    expect(field("Phone")).toHaveValue("9000000888");
  });

  it("does not complete a field the server would not fill", async () => {
    mockResumeUpload(judged({}, { phone: "that number belongs to another candidate" }));
    const { container } = renderModal({ ...ROW, name: "Raghavendrarao Kolla", phone: "" });
    await uploadResume(container);
    expect(await screen.findByText("Not filled: phone (that number belongs to another candidate).")).toBeInTheDocument();
    expect(field("Phone")).toHaveValue("");
  });

  it("a new candidate: an incomplete name or phone on the reading is not used; React.js is React JS", async () => {
    mockResumeUpload({ status: "ok", success: true, data: { ...READ, candidate_name: "Raghavendrarao K", phone: "+91 90000" } });
    const { container } = renderModal(null);
    await uploadResume(container);
    await waitFor(() => expect(field("Email")).toHaveValue("raghav.k@example.test"));
    expect(field("Candidate name *")).toHaveValue("");
    expect(field("Phone")).toHaveValue("");
    expect(field("Technology")).toHaveValue("React JS");
  });

  it("a new candidate: a cut-short name typed before the upload is completed", async () => {
    mockResumeUpload({ status: "ok", success: true, data: READ });
    const { container } = renderModal(null);
    fireEvent.change(field("Candidate name *"), { target: { value: "Kolla Raghava" } });
    fireEvent.change(field("Phone"), { target: { value: "77995" } });
    await uploadResume(container);
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla"));
    expect(field("Phone")).toHaveValue("9000000777");
  });
});

describe("after Save and reopen, the complete values stay; a save never cuts them short", () => {
  const READ = {
    is_resume: true, candidate_name: "Raghavendrarao Kolla", phone: "+919000000777",
    email: "raghav.k@example.test", technology: "React.js", confidence_score: 85, extraction_source: "pdf_text_ai",
  };
  const ROW = {
    ...CANDIDATE, name: "Kolla Raghava", phone: "9000000777", email: "raghav.k@example.test", technology: "React JS",
    follow_up: "Balance after the offer",
  };
  const dialogButton = (label) =>
    Array.from(document.querySelectorAll(".cm-btn")).find((b) => b.textContent.trim() === label);

  function openModal(row, onSave = vi.fn(async () => {}), onServerChange = vi.fn()) {
    const view = render(
      <ConfirmProvider>
        <CandidateEditModal initial={row} onClose={vi.fn()} onSave={onSave} onServerChange={onServerChange} isAdmin={true} />
      </ConfirmProvider>,
    );
    return { ...view, onSave, onServerChange };
  }
  const save = () => fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

  it("the completed name, the 10-digit phone, the email and the technology are what Save sends and what reopens", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: ROW, ai_extraction: READ,
      autofill: { filled: { name: "Raghavendrarao Kolla" }, skipped: {}, refused: "" } });
    const { container, onSave, onServerChange, unmount } = openModal(ROW);
    await uploadResume(container);
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla"));
    expect(onServerChange).toHaveBeenCalled();          // the list behind the form is reloaded
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const sent = onSave.mock.calls[0][0];
    expect([sent.name, sent.phone, sent.email, sent.technology]).toEqual(
      ["Raghavendrarao Kolla", "9000000777", "raghav.k@example.test", "React JS"]);
    unmount();

    // Reopened from what was saved: the same four values, and another upload changes nothing.
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: ROW, ai_extraction: READ,
      autofill: { filled: {}, skipped: {}, refused: "" } });
    const reopened = openModal({ ...ROW, ...sent });
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
    expect(field("Phone")).toHaveValue("9000000777");
    expect(field("Email")).toHaveValue("raghav.k@example.test");
    expect(field("Technology")).toHaveValue("React JS");
    await uploadResume(reopened.container);
    await readingDone();
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
    expect(screen.queryByText(/Filled from this resume/)).toBeNull();
  });

  it("a cut-short phone typed in the form is not saved", async () => {
    const { onSave } = openModal({ ...ROW, name: "Raghavendrarao Kolla" });
    fireEvent.change(field("Phone"), { target: { value: "900000077" } });
    save();
    expect(await screen.findByText("Enter the full 10-digit mobile number.")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("a saved incomplete number left as it is can still be saved", async () => {
    const { onSave } = openModal({ ...ROW, name: "Raghavendrarao Kolla", phone: "900000077" });
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  });

  it("a name the server finds cut short is saved only after the person confirms it", async () => {
    const refusal = Object.assign(new Error("“Kolla Raghava” looks like the saved name “Raghavendrarao Kolla” cut short."), { shorterName: true });
    const onSave = vi.fn(async (payload) => { if (!payload.allow_shorter_name) throw refusal; });
    openModal({ ...ROW, name: "Kolla Raghava" }, onSave);

    save();
    expect(await screen.findByText("Save a shorter name?")).toBeInTheDocument();
    fireEvent.click(dialogButton("Keep editing"));
    expect(await screen.findByText(refusal.message)).toBeInTheDocument();
    expect(onSave).toHaveBeenCalledTimes(1);

    save();
    await screen.findByText("Save a shorter name?");
    fireEvent.click(dialogButton("Save shorter name"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3));
    expect(onSave.mock.calls[2][0]).toMatchObject({ name: "Kolla Raghava", allow_shorter_name: true });
  });
});
