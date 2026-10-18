import { useEffect, useId, useRef, type ReactNode } from "react";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  /** Shown under the title, outside the scrolling body. */
  toolbar?: ReactNode;
  wide?: boolean;
}

/**
 * Side drawer built on the native <dialog> element: showModal() gives us the focus trap,
 * Escape to close, an inert background, and focus return to the opener.
 */
export function Drawer({ open, onClose, title, children, toolbar, wide }: DrawerProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      opener.current = document.activeElement;
      d.showModal();
      document.body.classList.add("no-scroll");
      d.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    } else if (!open && d.open) {
      d.close();
    }
    if (!open) {
      document.body.classList.remove("no-scroll");
      const el = opener.current as HTMLElement | null;
      if (el && typeof el.focus === "function" && document.contains(el)) el.focus();
      opener.current = null;
    }
  }, [open]);

  useEffect(() => () => document.body.classList.remove("no-scroll"), []);

  return (
    <dialog
      ref={ref}
      className={`drawer${wide ? " drawer-wide" : ""}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        // A click on the backdrop lands on the <dialog> itself.
        if (e.target === ref.current) onClose();
      }}
    >
      {open && (
        <div className="drawer-inner">
          <header className="drawer-head">
            <h2 id={titleId} className="drawer-title">
              {title}
            </h2>
            <button type="button" className="btn btn-quiet drawer-close" onClick={onClose}>
              Close<span className="visually-hidden"> panel</span>
            </button>
          </header>
          {toolbar && <div className="drawer-toolbar">{toolbar}</div>}
          <div className="drawer-body">{children}</div>
        </div>
      )}
    </dialog>
  );
}
