import React, { useEffect, useState } from "react";

export default function AiMailModelSelector({ request, refreshToken }) {
  const [data, setData] = useState(null);
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [tested, setTested] = useState("");

  useEffect(() => {
    let active = true;
    request("/api/ai-recruitment/mail-model").then((value) => {
      if (!active) return;
      if (!value.selection || !Array.isArray(value.nodes)) throw new Error("Model inventory unavailable");
      setData(value);
      // Mail events refresh the parent while an admin may be testing a draft.
      // Refresh the active configuration without replacing that unsaved choice.
      setModel((current) => current || value.selection.model);
    }).catch((error) => { if (active) setNotice(error.message); });
    return () => { active = false; };
  }, [request, refreshToken]);

  const run = async (action) => {
    setBusy(true);
    setNotice("");
    try {
      if (action === "test") {
        await request("/api/ai-recruitment/mail-model/test", { method: "POST", body: JSON.stringify({ model }) });
        setTested(model);
        setNotice("Structured mail JSON and failover passed. Selection is not saved yet.");
      } else {
        const value = await request("/api/ai-recruitment/mail-model", {
          method: "PUT", body: JSON.stringify({ model, revision: data.selection.revision }),
        });
        setData((old) => ({ ...old, selection: value.selection }));
        setTested("");
        setNotice("AI Mail model saved. New foreground and background classifications use this selection.");
      }
    } catch (error) {
      setTested("");
      setNotice(action === "save"
        ? `${error.message}. Save not confirmed; refresh to verify the active configuration.`
        : `${error.message}. Previous configured model retained.`);
    } finally { setBusy(false); }
  };
  const names = [...new Set((data?.nodes || []).flatMap((node) => node.models.map((entry) => entry.name)))];
  const selectedInstalled = names.some((name) => name.replace(/:latest$/, "") === model.replace(/:latest$/, ""));
  const last = data?.selection?.last_used;
  return <section className="sot-mail-model-selector" aria-label="AI Mail model selection">
    <label>AI Model{" "}
      <select aria-label="AI Mail model" value={model} disabled={busy || !data} onChange={(event) => {
        setModel(event.target.value); setTested(""); setNotice("");
      }}>
        {!names.includes(model) && <option value={model}>{model || "Loading models…"} — {selectedInstalled ? "available (configured alias)" : "unavailable"}</option>}
        {names.map((name) => {
          const holders = data.nodes.filter((node) => node.models.some((entry) => entry.name === name));
          const sizes = [...new Set(holders.flatMap((node) => node.models.filter((entry) => entry.name === name)
            .map((entry) => (entry.size_bytes / 1024 ** 3).toFixed(1))))].join("/");
          return <option key={name} value={name} title={holders.map((node) => node.label).join(", ")}>
            {name} — {sizes} GiB — {holders.some((node) => node.available) ? "available" : "offline"}
          </option>;
        })}
      </select>
    </label>{" "}
    <button disabled={busy || !data || !selectedInstalled} onClick={() => run("test")}>Test model &amp; failover</button>{" "}
    <button disabled={busy || !data || tested !== model} onClick={() => run("save")}>Save AI Mail model</button>{" "}
    {busy && <p role="status">Validating model availability and structured mail JSON…</p>}
    <p>Configured AI Mail model: <strong>{data?.selection?.model || "Loading…"}</strong></p>
    <p>Last actual AI Mail execution: {last ? `${last.model} on ${last.node_label || last.node_id}` : "Not recorded yet"}. Existing node preferences remain in effect.</p>
    <details><summary>Availability and validation</summary>
      <p>{(data?.nodes || []).map((node) => `${node.label}: ${node.available ? "online" : "offline"}`).join(" · ")}</p>
      <small>Activation requires relevance and classification JSON validation on two nodes. Mail safety validation, one-node decision affinity and the independent validator remain enabled.</small>
    </details>
    {notice && <p role="status">{notice}</p>}
  </section>;
}
