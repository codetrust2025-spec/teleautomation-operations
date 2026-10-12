/**
 * Candidate Gmail, 12 Oct: an expired mailbox is one table row. Its
 * "Reconnect Required" badge says so once, and Reconnect Gmail sits in the
 * row's own Actions cell beside the menu. The red row beneath that repeated
 * "Gmail connection expired. Reconnect to continue monitoring." under every
 * expired mailbox is gone. Reconnecting itself is unchanged: the button calls
 * the same onAction("reconnect", row). Names are invented.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MailboxTable } from "./RecruitmentMailPanelRedesign.jsx";

afterEach(cleanup);

function mailbox(id, name, uiStatus) {
  return {
    uiStatus,
    candidate: { id: `cand-${id}`, name, phone: "9000000123" },
    mailbox: { id: `mb-${id}`, email_address: `${id}@example.com`, monitoring_enabled: true, last_successful_sync_at: "2026-09-29T12:25:00Z" },
    stats: { latest_sync_status: "" },
  };
}

const rows = [
  mailbox("a", "Asha Fakename", "RECONNECT_REQUIRED"),
  mailbox("b", "Bala Fakename", "CONNECTED"),
  mailbox("c", "Chitra Fakename", "RECONNECT_REQUIRED"),
];

describe("an expired Gmail", () => {
  it("is one row: the badge, and Reconnect Gmail in its Actions cell", () => {
    render(<MailboxTable rows={rows} busy={false} onAction={() => {}} />);
    const bodyRows = document.querySelectorAll("tbody tr");
    expect(bodyRows).toHaveLength(3);
    expect(document.querySelector(".sot-reconnect-row")).toBeNull();
    expect(screen.queryByText(/Gmail connection expired/)).toBeNull();

    for (const name of ["Asha Fakename", "Chitra Fakename"]) {
      const tr = screen.getByText(name).closest("tr");
      expect(within(tr).getByText("Reconnect Required")).toBeTruthy();
      const actions = tr.querySelector('td[data-label="Actions"]');
      expect(within(actions).getAllByRole("button", { name: "Reconnect Gmail" })).toHaveLength(1);
      expect(actions.querySelector("details.sot-action-menu")).not.toBeNull();
    }
  });

  it("has no Reconnect button when the Gmail is healthy", () => {
    render(<MailboxTable rows={rows} busy={false} onAction={() => {}} />);
    const tr = screen.getByText("Bala Fakename").closest("tr");
    expect(within(tr).queryByRole("button", { name: "Reconnect Gmail" })).toBeNull();
    expect(within(tr).queryByText("Reconnect Required")).toBeNull();
  });

  it("reconnects exactly as before: onAction('reconnect', row)", () => {
    const onAction = vi.fn();
    render(<MailboxTable rows={rows} busy={false} onAction={onAction} />);
    const tr = screen.getByText("Chitra Fakename").closest("tr");
    fireEvent.click(within(tr).getByRole("button", { name: "Reconnect Gmail" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("reconnect", rows[2]);
  });

  it("is disabled while another action runs", () => {
    render(<MailboxTable rows={rows} busy={true} onAction={() => {}} />);
    for (const button of screen.getAllByRole("button", { name: "Reconnect Gmail" })) {
      expect(button).toBeDisabled();
    }
  });
});
