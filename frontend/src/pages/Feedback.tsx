import { useId, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";
import { ErrorMessage } from "../components/Status";
import { useDocumentTitle } from "../hooks";

const MAX = 5000;

export default function Feedback() {
  useDocumentTitle("Provide feedback");
  const id = useId();
  const location = useLocation();
  // The nav link passes the page the visitor came from, so a bug report says where it happened.
  const from = (location.state as { from?: string } | null)?.from ?? "";
  const doneRef = useRef<HTMLHeadingElement>(null);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [website, setWebsite] = useState(""); // honeypot: hidden from people, filled in by bots
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [sent, setSent] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.sendFeedback({ name, email, message, page: from, website });
      setSent(true);
      window.setTimeout(() => doneRef.current?.focus(), 0);
    } catch (err) {
      setError(err as Error);
    } finally {
      setBusy(false);
    }
  };

  if (sent) {
    return (
      <div className="container page">
        <h1 ref={doneRef} tabIndex={-1}>
          Thank you
        </h1>
        <p className="prose">Your feedback reached the person who maintains Aletheia. If it calls for a reply, they'll write to {email}.</p>
        <div className="actions">
          <Link className="btn btn-primary" to="/">
            Back to the home page
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="container page">
      <h1>Provide feedback</h1>
      <p className="prose">
        Tell us how Aletheia worked for you: what helped, what was confusing, what's missing, or what broke. A person reads
        every message. We use your name and email only to reply.
      </p>

      <form className="panel" onSubmit={submit} aria-label="Feedback form">
        <div className="field">
          <label htmlFor={`${id}-name`}>Your name</label>
          <input id={`${id}-name`} type="text" value={name} required maxLength={120} autoComplete="name"
            onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-email`}>Your email</label>
          <input id={`${id}-email`} type="email" value={email} required maxLength={254} autoComplete="email"
            onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-message`}>Your feedback</label>
          <textarea id={`${id}-message`} rows={9} value={message} required minLength={10} maxLength={MAX}
            onChange={(e) => setMessage(e.target.value)} aria-describedby={`${id}-count`} />
          <p id={`${id}-count`} className="hint">
            {message.length.toLocaleString()} of {MAX.toLocaleString()} characters
            {from ? ` · Sent from ${from}` : ""}
          </p>
        </div>
        {/* Honeypot. Off-screen and out of the tab order, so people and screen readers skip it. */}
        <div aria-hidden="true" style={{ position: "absolute", left: "-10000px" }}>
          <label>
            Leave this empty
            <input type="text" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
          </label>
        </div>

        {error && <ErrorMessage error={error} title="Your feedback didn't send." />}
        <div className="actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Sending…" : "Send feedback"}
          </button>
        </div>
      </form>
    </div>
  );
}
