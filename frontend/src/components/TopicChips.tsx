import { useState } from "react";

interface Props {
  topics: { topic: string; count?: number }[];
  selected: string[];
  onToggle: (topic: string) => void;
  limit?: number;
  label: string;
}

/** Toggle buttons for topics. Selected topics are listed first and never hidden. */
export function TopicChips({ topics, selected, onToggle, limit = 18, label }: Props) {
  const [showAll, setShowAll] = useState(false);
  const sel = new Set(selected);
  const known = new Set(topics.map((t) => t.topic));
  const ordered = [
    ...selected.filter((s) => !known.has(s)).map((topic) => ({ topic, count: undefined })),
    ...topics.filter((t) => sel.has(t.topic)),
    ...topics.filter((t) => !sel.has(t.topic)),
  ];
  const visible = showAll ? ordered : ordered.slice(0, Math.max(limit, selected.length));
  return (
    <div className="topic-chips" role="group" aria-label={label}>
      <ul className="chip-list">
        {visible.map((t) => (
          <li key={t.topic}>
            <button
              type="button"
              className="chip"
              aria-pressed={sel.has(t.topic)}
              onClick={() => onToggle(t.topic)}
            >
              {sel.has(t.topic) && (
                <span className="chip-check" aria-hidden="true">
                  ✓
                </span>
              )}
              {t.topic}
              {t.count !== undefined && <span className="chip-count"> {t.count}</span>}
            </button>
          </li>
        ))}
      </ul>
      {ordered.length > visible.length || showAll ? (
        <button type="button" className="link-button small" onClick={() => setShowAll((s) => !s)} aria-expanded={showAll}>
          {showAll ? "Show fewer topics" : `Show all ${ordered.length} topics`}
        </button>
      ) : null}
    </div>
  );
}
