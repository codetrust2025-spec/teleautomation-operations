import React, { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../components/ui/Icon.jsx";

/**
 * The ••• button on the Candidates toolbar (owner's mockup, 25 Nov): the
 * less-used page actions -- Download active CSV and, for admins, Total
 * expenditure -- in one small menu, so the toolbar keeps Filters, Sort and
 * Active on one line. Each item keeps the action's own name and runs it
 * exactly as its old button did.
 */
export function CandidatesMoreMenu({ items }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const menuId = useId();
  const shown = items.filter(Boolean);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!shown.length) return null;
  return (
    <div className="cand-more" ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        className={`cand-bar__more${open ? " cand-bar__more--open" : ""}`}
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title="More actions"
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="more-vertical" size={18} className="cand-bar__more-icon" />
      </button>
      {open && (
        <div className="cand-more__menu" id={menuId} role="menu" aria-label="More actions">
          {shown.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              className="cand-more__item"
              aria-label={item.ariaLabel}
              title={item.title}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              <Icon name={item.icon} size={15} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** The shell header's page-action slot (App.jsx), once it is on the page. */
function useHeaderSlot() {
  const [slot, setSlot] = useState(null);
  useEffect(() => {
    setSlot(document.getElementById("page-header-actions"));
  }, []);
  return slot;
}

/**
 * Puts `children` in the shell header beside the account button when the page
 * runs inside the app shell; anywhere else (a panel rendered on its own) they
 * stay where they are written.
 */
export function HeaderAction({ children, fallback = true }) {
  const slot = useHeaderSlot();
  if (slot) return createPortal(children, slot);
  return fallback ? children : null;
}
