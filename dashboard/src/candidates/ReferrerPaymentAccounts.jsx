import { useCallback, useEffect, useMemo, useState } from "react";

const EMPTY_FORM = {
  account_holder_name: "",
  upi_id: "",
  bank_account_identifier: "",
  payment_phone_number: "",
  provider_name: "UPI",
};

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.status !== "ok") {
    throw new Error(payload.detail || payload.message || "Request failed");
  }
  return payload;
}

export async function fetchReferrerRegistryJson(apiBase, path, options = {}) {
  const base = String(apiBase || "").replace(/\/$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const candidates = [`${base}${normalizedPath}`];
  if (!base.endsWith("/api")) {
    candidates.push(`${base}/api${normalizedPath}`);
  }

  let lastError = new Error("Request failed");
  for (const url of [...new Set(candidates)]) {
    try {
      return await readJson(await fetch(url, {
        credentials: "include",
        ...options,
      }));
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

export default function ReferrerPaymentAccounts({
  apiBase = "",
  referrerName = "",
}) {
  const [referrers, setReferrers] = useState([]);
  const [accounts, setAccounts] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [effectiveMonth, setEffectiveMonth] = useState(
    () => new Date().toISOString().slice(0, 7),
  );
  // Active / Inactive / All filter for the referrer list shown when no single
  // referrer is selected. "all" shows everyone so departed referrers remain
  // visible and reactivatable.
  const [statusFilter, setStatusFilter] = useState("active");

  const referrer = useMemo(() => {
    const key = referrerName.trim().toLowerCase();
    return referrers.find((row) =>
      [row.name, ...(row.aliases || [])]
        .some((name) => String(name || "").trim().toLowerCase() === key)
    ) || null;
  }, [referrerName, referrers]);

  // Load the full set: the /referrers endpoint already returns every referrer
  // (its status filter defaults to "all"), so name resolution for a selected
  // referrer works even when that referrer is inactive. The Active/Inactive/All
  // control filters this set client-side, so switching needs no refetch.
  const loadReferrers = useCallback(async () => {
    const payload = await fetchReferrerRegistryJson(apiBase, "/referrers");
    setReferrers(payload.referrers || []);
  }, [apiBase]);

  const listedReferrers = useMemo(() => {
    const rows = [...referrers].sort((a, b) =>
      String(a.name || "").toLowerCase().localeCompare(String(b.name || "").toLowerCase())
    );
    if (statusFilter === "active") return rows.filter((r) => r.is_active);
    if (statusFilter === "inactive") return rows.filter((r) => !r.is_active);
    return rows;
  }, [referrers, statusFilter]);

  async function setReferrerLifecycle(referrerId, status) {
    const body = { status };
    if (status === "INACTIVE") {
      if (!effectiveMonth) {
        setError("Pick an effective month before marking inactive.");
        return;
      }
      body.effective_month = effectiveMonth;
    }
    setSaving(true);
    setError("");
    try {
      await fetchReferrerRegistryJson(
        apiBase,
        `/referrers/${encodeURIComponent(referrerId)}/lifecycle`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      await loadReferrers();
    } catch (err) {
      setError(err.message || "Could not update referrer status");
    } finally {
      setSaving(false);
    }
  }

  const loadAccounts = useCallback(async (referrerId) => {
    if (!referrerId) {
      setAccounts([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await fetchReferrerRegistryJson(
        apiBase,
        `/referrers/${encodeURIComponent(referrerId)}/payment-accounts`,
      );
      setAccounts(payload.accounts || []);
    } catch (err) {
      setError(err.message || "Could not load payment accounts");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    loadReferrers().catch((err) => setError(err.message || "Could not load referrers"));
  }, [loadReferrers]);

  useEffect(() => {
    loadAccounts(referrer?.id);
  }, [loadAccounts, referrer?.id]);

  function change(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function addAccount(event) {
    event.preventDefault();
    if (!referrer) return;
    setSaving(true);
    setError("");
    try {
      await fetchReferrerRegistryJson(
        apiBase,
        `/referrers/${encodeURIComponent(referrer.id)}/payment-accounts`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(form),
        },
      );
      setForm({ ...EMPTY_FORM, account_holder_name: referrer.name });
      await loadAccounts(referrer.id);
    } catch (err) {
      setError(err.message || "Could not add payment account");
    } finally {
      setSaving(false);
    }
  }

  async function updateAccount(accountId, changes) {
    setSaving(true);
    setError("");
    try {
      await fetchReferrerRegistryJson(
        apiBase,
        `/referrer-payment-accounts/${encodeURIComponent(accountId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(changes),
        },
      );
      await loadAccounts(referrer?.id);
    } catch (err) {
      setError(err.message || "Could not update payment account");
    } finally {
      setSaving(false);
    }
  }

  async function setLifecycle(status) {
    if (!referrer) return;
    await setReferrerLifecycle(referrer.id, status);
  }

  async function removeAccount(accountId) {
    setSaving(true);
    setError("");
    try {
      await fetchReferrerRegistryJson(
        apiBase,
        `/referrer-payment-accounts/${encodeURIComponent(accountId)}`,
        { method: "DELETE" },
      );
      await loadAccounts(referrer?.id);
    } catch (err) {
      setError(err.message || "Could not remove payment account");
    } finally {
      setSaving(false);
    }
  }

  // No single referrer selected: show the Active / Inactive / All referrer
  // list so an admin can review every referrer's lifecycle state and
  // reactivate a departed one (inactive referrers never appear in the
  // referrer picker, so this list is the only place they are visible).
  if (!referrerName || referrerName === "all") {
    return (
      <section className="ref-pay-accounts" aria-label="Referrer lifecycle">
        <header className="ref-pay-accounts__head">
          <div>
            <strong>Referrers</strong>
            <span>Mark a referrer inactive from a chosen month, or reactivate one. History is always preserved.</span>
          </div>
          {loading && <small>Loading…</small>}
        </header>

        <div className="ref-lifecycle-filter" role="group" aria-label="Filter referrers by status">
          {["active", "inactive", "all"].map((value) => (
            <button
              key={value}
              type="button"
              className={`cand-btn cand-btn--xs ${statusFilter === value ? "cand-btn--primary" : "cand-btn--ghost"}`}
              aria-pressed={statusFilter === value}
              onClick={() => setStatusFilter(value)}
            >
              {value === "active" ? "Active" : value === "inactive" ? "Inactive" : "All"}
            </button>
          ))}
          <label htmlFor="ref-list-month" className="ref-lifecycle-filter__month-label">Effective month</label>
          <input
            id="ref-list-month"
            type="month"
            className="cand-input"
            value={effectiveMonth}
            onChange={(event) => setEffectiveMonth(event.target.value)}
          />
        </div>

        {listedReferrers.length === 0 && !loading && (
          <p className="ref-pay-accounts__empty">No {statusFilter === "all" ? "" : statusFilter} referrers.</p>
        )}

        <ul className="ref-lifecycle-list">
          {listedReferrers.map((row) => (
            <li className="ref-lifecycle-row" key={row.id}>
              <div>
                <strong>{row.name}</strong>
                <span className={`ref-pay-status ref-pay-status--${row.is_active ? "verified" : "rejected"}`}>
                  {row.is_active ? "Active" : "Inactive"}
                </span>
                {!row.is_active && row.inactive_effective_month && (
                  <small>Inactive from {row.inactive_effective_month}</small>
                )}
              </div>
              <div className="ref-lifecycle-row__actions">
                {row.is_active ? (
                  <button
                    type="button"
                    className="cand-btn cand-btn--xs cand-btn--danger-ghost"
                    disabled={saving}
                    onClick={() => setReferrerLifecycle(row.id, "INACTIVE")}
                  >
                    Mark inactive
                  </button>
                ) : (
                  <button
                    type="button"
                    className="cand-btn cand-btn--xs cand-btn--primary"
                    disabled={saving}
                    onClick={() => setReferrerLifecycle(row.id, "ACTIVE")}
                  >
                    Reactivate
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {error && <p className="ref-pay-accounts__error">{error}</p>}
      </section>
    );
  }

  return (
    <section className="ref-pay-accounts" aria-label="Referrer payment accounts">
      <header className="ref-pay-accounts__head">
        <div>
          <strong>Payment Accounts</strong>
          <span>Verified accounts can receive candidate payments for {referrerName}.</span>
        </div>
        {loading && <small>Loading…</small>}
      </header>

      {!referrer && !loading && (
        <p className="ref-pay-accounts__error">
          This name is not linked to the current referrer registry.
        </p>
      )}

      {referrer && (
        <div className="ref-lifecycle" aria-label="Referrer lifecycle">
          <span className={`ref-pay-status ref-pay-status--${referrer.is_active ? "verified" : "rejected"}`}>
            {referrer.is_active ? "Active" : "Inactive"}
          </span>
          {!referrer.is_active && referrer.inactive_effective_month && (
            <small>Inactive from {referrer.inactive_effective_month}</small>
          )}
          {referrer.is_active ? (
            <>
              <label htmlFor="ref-lifecycle-month">Effective month</label>
              <input
                id="ref-lifecycle-month"
                type="month"
                className="cand-input"
                value={effectiveMonth}
                onChange={(event) => setEffectiveMonth(event.target.value)}
              />
              <button
                type="button"
                className="cand-btn cand-btn--xs cand-btn--danger-ghost"
                disabled={saving}
                onClick={() => setLifecycle("INACTIVE")}
              >
                Mark inactive
              </button>
            </>
          ) : (
            <button
              type="button"
              className="cand-btn cand-btn--xs cand-btn--primary"
              disabled={saving}
              onClick={() => setLifecycle("ACTIVE")}
            >
              Reactivate
            </button>
          )}
        </div>
      )}

      {accounts.length > 0 && (
        <div className="ref-pay-accounts__list">
          {accounts.map((account) => (
            <article className="ref-pay-account" key={account.id}>
              <div>
                <strong>{account.account_holder_name}</strong>
                <span>
                  {account.masked_upi_id
                    || account.masked_bank_account_identifier
                    || account.masked_payment_phone_number}
                  {account.provider_name ? ` · ${account.provider_name}` : ""}
                </span>
                <small>
                  Added {String(account.created_at || "").slice(0, 10) || "—"}
                  {account.verified_at
                    ? ` · Verified ${String(account.verified_at).slice(0, 10)}`
                    : ""}
                </small>
              </div>
              <div className="ref-pay-account__status">
                <span className={`ref-pay-status ref-pay-status--${String(account.verification_status).toLowerCase()}`}>
                  {account.verification_status}
                </span>
                <span>{account.is_active ? "Active" : "Inactive"}</span>
              </div>
              <div className="ref-pay-account__actions">
                {account.verification_status !== "VERIFIED" && (
                  <button type="button" className="cand-btn cand-btn--xs cand-btn--primary" disabled={saving} onClick={() => updateAccount(account.id, { verification_status: "VERIFIED" })}>
                    Verify
                  </button>
                )}
                {account.verification_status !== "REJECTED" && (
                  <button type="button" className="cand-btn cand-btn--xs cand-btn--ghost" disabled={saving} onClick={() => updateAccount(account.id, { verification_status: "REJECTED" })}>
                    Reject
                  </button>
                )}
                <button type="button" className="cand-btn cand-btn--xs cand-btn--ghost" disabled={saving} onClick={() => updateAccount(account.id, { is_active: !account.is_active })}>
                  {account.is_active ? "Deactivate" : "Activate"}
                </button>
                {account.verification_status === "UNVERIFIED" && (
                  <button type="button" className="cand-btn cand-btn--xs cand-btn--danger-ghost" disabled={saving} onClick={() => removeAccount(account.id)}>
                    Remove
                  </button>
                )}
              </div>
              {(account.history || []).length > 0 && (
                <details className="ref-pay-account__history">
                  <summary>Account history</summary>
                  {(account.history || []).map((item, index) => (
                    <small key={`${item.at}-${index}`}>
                      {item.action} · {String(item.at || "").replace("T", " ").slice(0, 16)}
                      {item.by ? ` · ${item.by}` : ""}
                    </small>
                  ))}
                </details>
              )}
            </article>
          ))}
        </div>
      )}

      {referrer && (
        <form className="ref-pay-accounts__form" onSubmit={addAccount}>
          <input className="cand-input" value={form.account_holder_name} onChange={(event) => change("account_holder_name", event.target.value)} placeholder="Account-holder name" required />
          <input className="cand-input" value={form.upi_id} onChange={(event) => change("upi_id", event.target.value)} placeholder="UPI ID" />
          <input className="cand-input" value={form.bank_account_identifier} onChange={(event) => change("bank_account_identifier", event.target.value)} placeholder="Bank account (optional)" />
          <input className="cand-input" value={form.payment_phone_number} onChange={(event) => change("payment_phone_number", event.target.value)} placeholder="Payment phone (optional)" />
          <select className="cand-input" value={form.provider_name} onChange={(event) => change("provider_name", event.target.value)}>
            <option value="UPI">UPI</option>
            <option value="PhonePe">PhonePe</option>
            <option value="Google Pay">Google Pay</option>
            <option value="Bank transfer">Bank transfer</option>
          </select>
          <button type="submit" className="cand-btn cand-btn--sm cand-btn--primary" disabled={saving}>
            {saving ? "Saving…" : "Add account"}
          </button>
        </form>
      )}
      {error && <p className="ref-pay-accounts__error">{error}</p>}
    </section>
  );
}
