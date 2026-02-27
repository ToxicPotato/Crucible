import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { getModelShortName } from '../utils';
import './Stage1.css';

const WORD_THRESHOLD = 80;

function CollapsibleResponse({ text }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text && text.split(/\s+/).filter(Boolean).length > WORD_THRESHOLD;

  return (
    <div className="response-text">
      <div className={`markdown-content${isLong && !expanded ? ' response-clamp' : ''}`}>
        <ReactMarkdown>{text}</ReactMarkdown>
      </div>
      {isLong && (
        <button className="show-more-btn" onClick={() => setExpanded((e) => !e)}>
          {expanded ? 'Show less ↑' : 'Show more ↓'}
        </button>
      )}
    </div>
  );
}

function ConfidenceBadge({ value, source }) {
  if (value == null) return null;
  const color =
    value >= 70 ? 'green' :
    value >= 50 ? 'amber' :
    'red';
  return (
    <span className="confidence-group">
      <span className={`confidence-badge confidence-${color}`}>
        {value}/100
      </span>
      {source && (
        <span className={`source-pill source-${source}`}>
          {source}
        </span>
      )}
    </span>
  );
}

function MetaChips({ items, variant }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="meta-chips">
      {items.map((item, i) => (
        <span key={i} className={`chip chip-${variant}`}>{item}</span>
      ))}
    </div>
  );
}

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!responses || responses.length === 0) {
    return null;
  }

  const active = responses[activeTab];
  const hasMetadata =
    active.confidence != null ||
    (active.factual_claims && active.factual_claims.length > 0) ||
    (active.key_assumptions && active.key_assumptions.length > 0) ||
    (active.known_unknowns && active.known_unknowns.length > 0);

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {getModelShortName(resp.model)}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="model-name">{active.model}</div>
        <CollapsibleResponse key={activeTab} text={active.response} />

        {hasMetadata && (
          <div className="meta-section">
            {active.confidence != null && (
              <div className="meta-row">
                <span className="meta-label">Confidence</span>
                <ConfidenceBadge
                  value={active.confidence}
                  source={active.confidence_source}
                />
              </div>
            )}
            {active.factual_claims && active.factual_claims.length > 0 && (
              <div className="meta-row">
                <span className="meta-label">Facts</span>
                <MetaChips items={active.factual_claims} variant="fact" />
              </div>
            )}
            {active.key_assumptions && active.key_assumptions.length > 0 && (
              <div className="meta-row">
                <span className="meta-label">Assumptions</span>
                <MetaChips items={active.key_assumptions} variant="assumption" />
              </div>
            )}
            {active.known_unknowns && active.known_unknowns.length > 0 && (
              <div className="meta-row">
                <span className="meta-label">Unknowns</span>
                <MetaChips items={active.known_unknowns} variant="unknown" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
