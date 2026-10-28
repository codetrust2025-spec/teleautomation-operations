import React, { useEffect, useState } from "react";

export default function AiMailModelSelector({ request, refreshToken, onSaved, catalogData }) {
  const [localData, setData] = useState(null);
  const data = catalogData === undefined ? localData : catalogData;
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [tested, setTested] = useState("");

  useEffect(() => {
    if (catalogData !== undefined) {
      if (catalogData) setModel((current) => current || catalogData.selection.model);
      return;
    }
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
  }, [request, refreshToken, catalogData]);

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
        if (catalogData === undefined) setData((old) => ({ ...old, selection: value.selection }));
        setTested("");
        setNotice("AI Mail model saved. New foreground and background classifications use this selection.");
        onSaved?.(value.selection);
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
  const selectedEntries = (data?.nodes || []).flatMap((node) => node.models.filter((entry) =>
    entry.name.replace(/:latest$/, "") === model.replace(/:latest$/, "")));
  const sizes = [...new Set(selectedEntries.filter((entry) => entry.size_bytes > 0)
    .map((entry) => (entry.size_bytes / 1e9).toFixed(1)))];
  return <section className="sot-mail-model-selector" aria-label="AI Mail model selection" aria-busy={busy}>
    <div className="sot-mail-model-row">
      <label htmlFor="ai-mail-model">AI Model</label>
      <select id="ai-mail-model" aria-label="AI Mail model" title={model || "Select an installed model"}
        value={model} disabled={busy || !data} onChange={(event) => {
        setModel(event.target.value); setTested(""); setNotice("");
      }}>
        {!names.includes(model) && <option value={model}
          title={selectedInstalled ? "Installed under a configured alias" : "Not available on a reachable node"}>
          {model || "Loading models…"}
        </option>}
        {names.map((name) => {
          const holders = data.nodes.filter((node) => node.models.some((entry) => entry.name === name));
          return <option key={name} value={name} title={holders.map((node) => node.label).join(", ")}>
            {name}
          </option>;
        })}
      </select>
      <output className="sot-mail-model-size" aria-label="Selected model size"
        title="Installed model size on disk; decimal gigabytes">
        {sizes.length ? sizes.join("/") : "—"} GB
      </output>
      <button className="sot-mail-model-test" aria-label="Test model & failover"
        title="Test structured mail JSON and failover without saving"
        disabled={busy || !data || !selectedInstalled} onClick={() => run("test")}>Test</button>
      <button className="sot-mail-model-save" aria-label="Save AI Mail model"
        title="Revalidate and save the AI Mail model"
        disabled={busy || !data || tested !== model} onClick={() => run("save")}>Save</button>
    </div>
    <p className="sot-mail-model-current">Current: <strong>{data?.selection?.model || "Loading…"}</strong>
      <span> · {last ? `Last run: ${last.model} on ${last.node_label || last.node_id}` : "Node not recorded yet"}</span>
    </p>
    {busy && <p className="sot-mail-model-notice" role="status">Validating model availability and structured mail JSON…</p>}
    {notice && <p className="sot-mail-model-notice" role="status">{notice}</p>}
    <details className="sot-mail-model-details"><summary>Availability and validation</summary>
      <p>{(data?.nodes || []).map((node) => `${node.label}: ${node.available ? "online" : "offline"}`).join(" · ")}</p>
      <p>Selected model: {selectedInstalled ? "installed on a reachable node" : "unavailable"}.</p>
      <small>Activation requires relevance and classification JSON validation on two nodes. Mail safety validation, one-node decision affinity and the independent validator remain enabled.</small>
    </details>
  </section>;
}
