import React from "react";

export default function AiNodeManager({
  nodes,
  activity = {},
  busy,
  onMakePrimary,
  onUnload,
}) {
  // Open on every load. It was collapsed to keep the mailbox list above the
  // fold -- the grid is three cards tall and sits between the header and the
  // list -- but node health is read often enough here that hiding it behind a
  // click cost more than the scroll does. Still a disclosure: one click
  // collapses it for the rest of the visit.
  const online = nodes.filter((node) => node.endpoint_reachable).length;
  const primary = nodes.find((node) => node.primary);
  return (
    <details className="sot-ai-nodes" aria-label="Ollama AI nodes" open>
      <summary>
        <strong>AI nodes</strong>
        <span
          className={`sot-ai-nodes-glance${online < nodes.length ? " is-degraded" : ""}`}
        >
          {nodes.length
            ? `${online}/${nodes.length} online${primary ? ` · ${primary.label}` : ""}`
            : "none configured"}
        </span>
      </summary>
      <div className="sot-ai-node-grid">
        {nodes.length ? (
          nodes.map((node) => {
            // What the gateway says this node is running right now -- the
            // record the booking page names its node from, so the two agree.
            const serving = [
              ...new Set(
                (activity[node.id] || [])
                  .map((item) => item.label)
                  .filter(Boolean),
              ),
            ];
            return (
              <article
                className={`sot-ai-node is-${node.status || "offline"}`}
                key={node.id}
              >
                {/* One row per node, and only what an operator acts on. The host
                  * URL, Ollama version, acceleration split and last success and
                  * failure timestamps were diagnostics that pushed the three
                  * nodes into a tall stack; they are still on the health
                  * endpoint for anyone debugging. */}
                <span className="sot-ai-node-name">
                  <i aria-hidden="true" />
                  <strong>{node.label}</strong>
                  {node.primary && (
                    <span className="sot-ai-node-primary">PRIMARY</span>
                  )}
                </span>
                <span
                  className={`sot-ai-node-state is-${
                    node.endpoint_reachable ? "up" : "down"
                  }`}
                >
                  {node.endpoint_reachable ? "Online" : "Offline"}
                </span>
                <span className="sot-ai-node-state">
                  {/* Busy and Ready are different facts -- a model held in memory
                    * versus every required model installed -- so they are shown
                    * as one state rather than one label meaning either. A request
                    * running on the node says what it is busy with. */}
                  {serving.length
                    ? `Busy · ${serving.join(" + ")}`
                    : node.model_loaded
                      ? "Busy"
                      : node.ready
                        ? "Ready"
                        : "Not ready"}
                </span>
                <span className="sot-ai-node-latency">
                  {node.response_time_ms == null
                    ? "—"
                    : `${node.response_time_ms} ms`}
                </span>
                <span className="sot-ai-node-actions">
                  {!node.primary && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onMakePrimary(node)}
                      title={
                        node.ready
                          ? `Route AI work to ${node.label} for one hour`
                          : "This node is failing its model check — you will be asked to confirm"
                      }
                    >
                      {node.ready ? "Set primary" : "Set primary anyway"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="is-warning"
                    disabled={busy || !node.endpoint_reachable}
                    onClick={() => onUnload(node)}
                    title={`Unload AI models on ${node.label}`}
                  >
                    Unload
                  </button>
                </span>
              </article>
            );
          })
        ) : (
          <p className="sot-empty">Node health has not loaded yet.</p>
        )}
      </div>
    </details>
  );
}

