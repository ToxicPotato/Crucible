import { useState } from 'react';
import { getModelShortName } from '../utils';
import { getMinorityModel } from '../utils/epistemic';
import CollapsibleText from './CollapsibleText';
import './MinorityReport.css';

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

  const minorityModel = getMinorityModel(aggregateRankings);
  if (!minorityModel || !stage1) return null;

  const modelData = stage1.find((r) => r.model === minorityModel);
  if (!modelData) return null;

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

          <CollapsibleText text={modelData.response} markdown={true} threshold={80} />

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
