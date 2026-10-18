import { useId, useState } from "react";

/** A read-only link with a Copy button. */
export function CopyField({ label, value, help }: { label: string; value: string; help: string }) {
  const id = useId();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      (document.getElementById(id) as HTMLInputElement | null)?.select();
    }
  };
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="input-row">
        <input id={id} readOnly value={value} onFocus={(e) => e.currentTarget.select()} aria-describedby={`${id}-help`} />
        <button type="button" className="btn btn-small" onClick={copy}>
          {copied ? "Copied" : "Copy"}
          <span className="visually-hidden"> {label}</span>
        </button>
      </div>
      <p id={`${id}-help`} className="hint">
        {help}
      </p>
    </div>
  );
}
