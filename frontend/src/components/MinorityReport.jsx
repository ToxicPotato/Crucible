import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { getModelShortName } from '../utils';
import { getMinorityModel } from '../utils/epistemic';
import './MinorityReport.css';

const WORD_THRESHOLD = 80;

function ConfidenceBadge({ value, source }) {
  if (value == null) return null;
  const color = value >= 70 ? 'green' : value >= 50 ? 'amber' : 'red';
  return (
    <span className="confidence-group">
      <span className={`confidence-badge confidence-${color}`}>{value}/100</span>
      {source && (
        <span className={`source-pill source-${source}`}>{source}</span>
      )}
    </span>
  );
}

export default function MinorityReport({ stage1, aggregateRankings }) {
  const [open, setOpen] = useState(false);
  const [showFull, setShowFull] = useState(false);

  const minorityModel = getMinorityModel(aggregateRankings);
  if (!minorityModel || !stage1) return null;

  const modelData = stage1.find((r) => r.model === minorityModel);
  if (!modelData) return null;

  const words = modelData.response?.split(/\s+/).filter(Boolean).length ?? 0;
  const isLong = words > WORD_THRESHOLD;

  return (
    <div className="minority-report">
      <button
        className="minority-report-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>⚠ Minority Report: {getModelShortName(minorityModel)}</span>
        <span className="minority-toggle">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="minority-report-body">
          <div className="minority-model-name">{minorityModel}</div>

          <div className={`minority-response markdown-content${isLong && !showFull ? ' response-clamp' : ''}`}>
            <ReactMarkdown>{modelData.response}</ReactMarkdown>
          </div>

          {isLong && (
            <button className="show-more-btn" onClick={() => setShowFull((f) => !f)}>
              {showFull ? 'Show less ↑' : 'Show full ↓'}
            </button>
          )}

          {modelData.factual_claims && modelData.factual_claims.length > 0 && (
            <div className="minority-chips">
              {modelData.factual_claims.map((claim, i) => (
                <span key={i} className="chip chip-fact">{claim}</span>
              ))}
            </div>
          )}

          <ConfidenceBadge value={modelData.confidence} source={modelData.confidence_source} />
        </div>
      )}
    </div>
  );
}
