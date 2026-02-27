import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './CollapsibleText.css';

export default function CollapsibleText({ text, threshold = 80, markdown = false }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text && text.split(/\s+/).filter(Boolean).length > threshold;

  const content = markdown
    ? <div className="markdown-content"><ReactMarkdown>{text}</ReactMarkdown></div>
    : text;

  return (
    <div className="collapsible-text">
      <div className={`collapsible-body${isLong && !expanded ? ' collapsible-clamp' : ''}`}>
        {content}
        {isLong && !expanded && (
          <div className="collapsible-fade" />
        )}
      </div>
      {isLong && (
        <button
          className="collapsible-btn"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? 'Show less  \u25b4' : 'Show more  \u25be'}
        </button>
      )}
    </div>
  );
}
