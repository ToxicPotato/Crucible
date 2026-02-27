import { useState } from 'react';
import { calcPeerAlignment, calcConfidenceRange, calcVerdict } from '../utils/epistemic';
import './EpistemicSummary.css';

const CONSENSUS_CLASS = {
  Unanimous: 'consensus--unanimous',
  Majority: 'consensus--majority',
  Split: 'consensus--split',
  Unknown: 'consensus--unknown',
};

const RELIABILITY_CLASS = {
  Verified:  'reliability-badge--verified',
  Disputed:  'reliability-badge--disputed',
  Consensus: 'reliability-badge--consensus',
  Split:     'reliability-badge--split',
  Uncertain: 'reliability-badge--uncertain',
  Unknown:   'reliability-badge--unknown',
};

export default function EpistemicSummary({ stage1, stage25, aggregateRankings }) {
  const [open, setOpen] = useState(false);

  const peerAlignment = calcPeerAlignment(aggregateRankings);
  const verdict = calcVerdict(stage25, stage1);
  const confRange = calcConfidenceRange(stage1);
  const peerAlignmentCls = CONSENSUS_CLASS[peerAlignment] ?? CONSENSUS_CLASS.Unknown;
  const reliabilityCls = RELIABILITY_CLASS[verdict] ?? RELIABILITY_CLASS.Unknown;

  const contradicted = stage25?.filter((r) => r.status === 'CONTRADICTED').length ?? 0;
  const contested = stage25?.filter((r) => r.status === 'CONTESTED').length ?? 0;
  const verified = stage25?.filter((r) => r.status === 'VERIFIED').length ?? 0;
  const hasVerdicts = stage25 && (contradicted > 0 || contested > 0 || verified > 0);

  return (
    <div className="epistemic-summary">
      <button
        className="epistemic-summary-header"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <div className="epistemic-header-left">
          <span className="mode-badge">Council</span>
          <span className={`reliability-badge ${reliabilityCls}`}>{verdict}</span>
          {hasVerdicts && (
            <>
              {contradicted > 0 && (
                <span className="verdict-pill verdict-pill--contradicted">{contradicted} ✗</span>
              )}
              {contested > 0 && (
                <span className="verdict-pill verdict-pill--contested">{contested} ~</span>
              )}
              {verified > 0 && (
                <span className="verdict-pill verdict-pill--verified">{verified} ✓</span>
              )}
            </>
          )}
          {confRange && (
            <span className="confidence-range">{confRange.min}–{confRange.max} / 100</span>
          )}
        </div>
        <span className="epistemic-toggle">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="epistemic-summary-body">
          <div className="epistemic-grid">
            <div className="epistemic-col">
              <div className="epistemic-item">
                <span className="epistemic-label">Mode</span>
                <span className="mode-badge">Council</span>
              </div>
              <div className="epistemic-item">
                <span className="epistemic-label">Reliability</span>
                <span className={`reliability-badge ${reliabilityCls}`}>{verdict}</span>
              </div>
            </div>
            <div className="epistemic-col">
              {confRange && (
                <div className="epistemic-item">
                  <span className="epistemic-label">Confidence</span>
                  <span className="confidence-range-detail">{confRange.min}–{confRange.max} / 100</span>
                </div>
              )}
              {hasVerdicts && (
                <div className="epistemic-item">
                  <span className="epistemic-label">Fact-check</span>
                  <div className="verdict-pills">
                    {contradicted > 0 && (
                      <span className="verdict-pill verdict-pill--contradicted">
                        {contradicted} contradiction{contradicted !== 1 ? 's' : ''}
                      </span>
                    )}
                    {contested > 0 && (
                      <span className="verdict-pill verdict-pill--contested">
                        {contested} contested
                      </span>
                    )}
                    {verified > 0 && (
                      <span className="verdict-pill verdict-pill--verified">
                        {verified} verified
                      </span>
                    )}
                  </div>
                </div>
              )}
              <div className="epistemic-item">
                <span className="epistemic-label">Peer Alignment</span>
                <span className={`consensus-badge ${peerAlignmentCls}`}>{peerAlignment}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
