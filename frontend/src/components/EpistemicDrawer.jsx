import { useEffect } from 'react';
import { getModelShortName } from '../utils';
import { calcPeerAlignment, calcVerdict, calcConfidenceRange, groupClaimsByVerdict } from '../utils/epistemic';
import './Stage25.css';
import './EpistemicDrawer.css';

const VERDICT_CONFIG = {
  VERIFIED:     { label: '✓ Verified',     cls: 'verified' },
  CONTRADICTED: { label: '✗ Contradicted', cls: 'contradicted' },
  CONTESTED:    { label: '~ Contested',    cls: 'contested' },
  UNVERIFIABLE: { label: '? Unverifiable', cls: 'unverifiable' },
};

const VERDICT_ORDER = ['CONTRADICTED', 'CONTESTED', 'VERIFIED', 'UNVERIFIABLE'];

const RELIABILITY_CLASS = {
  Verified:  'reliability-badge--verified',
  Disputed:  'reliability-badge--disputed',
  Consensus: 'reliability-badge--consensus',
  Split:     'reliability-badge--split',
  Uncertain: 'reliability-badge--uncertain',
  Unknown:   'reliability-badge--unknown',
};

export default function EpistemicDrawer({ open, onClose, stage25, stage1, aggregateRankings }) {
  const peerAlignment = calcPeerAlignment(aggregateRankings);
  const verdict = calcVerdict(stage25, stage1);
  const confRange = calcConfidenceRange(stage1);
  const grouped = groupClaimsByVerdict(stage25);
  const hasData = stage25 && stage25.length > 0;

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  const peerAlignmentCls = {
    Unanimous: 'consensus--unanimous',
    Majority:  'consensus--majority',
    Split:     'consensus--split',
    Unknown:   'consensus--unknown',
  }[peerAlignment] ?? 'consensus--unknown';

  const reliabilityCls = RELIABILITY_CLASS[verdict] ?? RELIABILITY_CLASS.Unknown;

  return (
    <>
      <div
        className={`epistemic-backdrop${open ? ' epistemic-backdrop--open' : ''}`}
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        className={`epistemic-drawer${open ? ' epistemic-drawer--open' : ''}`}
        role="dialog"
        aria-label="Epistemic View"
        aria-modal="true"
      >
        <div className="epistemic-drawer-header">
          <span className="epistemic-drawer-title">Epistemic View</span>
          <button className="epistemic-drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="epistemic-drawer-body">
          {/* Council summary */}
          <div className="drawer-section">
            <div className="drawer-section-title">Council Analysis</div>
            <div className="drawer-meta-row">
              <span className="drawer-meta-label">Reliability</span>
              <span className={`reliability-badge ${reliabilityCls}`}>{verdict}</span>
            </div>
            <div className="drawer-meta-row">
              <span className="drawer-meta-label">Peer Alignment</span>
              <span className={`consensus-badge ${peerAlignmentCls}`}>{peerAlignment}</span>
            </div>
            {confRange && (
              <div className="drawer-meta-row">
                <span className="drawer-meta-label">Confidence</span>
                <span className="drawer-meta-value">{confRange.min}–{confRange.max} / 100</span>
              </div>
            )}
          </div>

          {/* Claims grouped by verdict */}
          {hasData ? (
            VERDICT_ORDER.map((verdict) => {
              const claims = grouped[verdict];
              if (!claims || claims.length === 0) return null;
              const sectionLabel =
                verdict.charAt(0) + verdict.slice(1).toLowerCase() + ' Claims';
              return (
                <div key={verdict} className="drawer-section">
                  <div className="drawer-section-title">{sectionLabel}</div>
                  <div className="verdict-list">
                    {claims.map((result, i) => {
                      const cfg = VERDICT_CONFIG[result.status] ?? VERDICT_CONFIG.UNVERIFIABLE;
                      const hasSource = result.source?.startsWith('http');
                      return (
                        <div key={i} className={`verdict-row verdict-row--${cfg.cls}`}>
                          <span className={`verdict-badge verdict-badge--${cfg.cls}`}>
                            {cfg.label}
                          </span>
                          <div className="verdict-body">
                            <div className="verdict-meta">
                              <span className="verdict-model">
                                {getModelShortName(result.model)}
                              </span>
                              <span className="verdict-confidence">
                                {result.original_confidence}/100
                              </span>
                            </div>
                            <div className="verdict-claim">"{result.claim}"</div>
                            {result.delta && (
                              <div className={`verdict-delta verdict-delta--${cfg.cls}`}>
                                {result.delta}
                              </div>
                            )}
                            {hasSource && (
                              <a
                                href={result.source}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="verdict-source"
                              >
                                {result.source}
                              </a>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="drawer-empty">No verification data available</div>
          )}
        </div>
      </div>
    </>
  );
}
