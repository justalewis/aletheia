import type { ReactNode } from "react";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="status status-loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function ErrorMessage({ error, onRetry, title }: { error: Error | string; onRetry?: () => void; title?: string }) {
  const msg = typeof error === "string" ? error : error.message;
  return (
    <div className="notice notice-error" role="alert">
      {title && <p className="notice-title">{title}</p>}
      <p>{msg}</p>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
