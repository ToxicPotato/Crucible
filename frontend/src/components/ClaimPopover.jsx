import { useEffect, useRef } from 'react';
import ReactDOM from 'react-dom';
import { getModelShortName } from '../utils';
import './Stage25.css';
import './ClaimPopover.css';

const VERDICT_CONFIG = {
  verified:     { label: '✓ Verified',     cls: 'verified' },
  contradicted: { label: '✗ Contradicted', cls: 'contradicted' },
  contested:    { label: '~ Contested',    cls: 'contested' },
  unverifiable: { label: '? Unverifiable', cls: 'unverifiable' },
};

export default function ClaimPopover({ claim, x, y, onClose }) {
  const ref = useRef(null);
  const statusKey = claim.status?.toLowerCase();
  const cfg = VERDICT_CONFIG[statusKey] ?? VERDICT_CONFIG.unverifiable;
  const hasSource = claim.source?.startsWith('http');

  // Edge clamp: don't overflow right edge
  const left = Math.min(x, window.innerWidth - 316);

  useEffect(() => {
    function onMouseDown(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    function onKeyDown(e) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose]);

  return ReactDOM.createPortal(
    <div
      ref={ref}
      className="claim-popover"
      style={{ left, top: y }}
      role="dialog"
      aria-label="Claim detail"
      aria-modal="true"
    >
      <div className="popover-arrow" />
      <div className="popover-header">
        <span className={`verdict-badge verdict-badge--${cfg.cls}`}>{cfg.label}</span>
        <span className="popover-model">{getModelShortName(claim.model)}</span>
        <span className="popover-confidence">{claim.original_confidence}/100</span>
      </div>
      <div className="popover-claim">"{claim.claim}"</div>
      {claim.delta && (
        <div className={`popover-delta popover-delta--${cfg.cls}`}>{claim.delta}</div>
      )}
      {hasSource && (
        <a
          href={claim.source}
          target="_blank"
          rel="noopener noreferrer"
          className="popover-source"
        >
          {claim.source}
        </a>
      )}
    </div>,
    document.body
  );
}
