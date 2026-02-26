import { getModelShortName } from '../utils';
import './Stage25.css';

const VERDICT_CONFIG = {
  VERIFIED:     { label: '✓ Verified',     cls: 'verified' },
  CONTRADICTED: { label: '✗ Contradicted', cls: 'contradicted' },
  CONTESTED:    { label: '~ Contested',    cls: 'contested' },
  UNVERIFIABLE: { label: '? Unverifiable', cls: 'unverifiable' },
};


export default function Stage25({ results }) {
  if (!results || results.length === 0) return null;

  // Show most actionable results first
  const sorted = [...results].sort((a, b) => {
    const order = { CONTRADICTED: 0, CONTESTED: 1, VERIFIED: 2, UNVERIFIABLE: 3 };
    return (order[a.status] ?? 4) - (order[b.status] ?? 4);
  });

  const contradictedCount = results.filter((r) => r.status === 'CONTRADICTED').length;
  const contestedCount = results.filter((r) => r.status === 'CONTESTED').length;
  const verifiedCount = results.filter((r) => r.status === 'VERIFIED').length;

  return (
    <div className="stage stage25">
      <h3 className="stage-title">Stage 2.5: External Fact-Check</h3>

      <div className="stage25-summary">
        <span>Checked {results.length} claim{results.length !== 1 ? 's' : ''} from top-2 models</span>
        {contradictedCount > 0 && (
          <span className="summary-pill summary-pill--contradicted">
            {contradictedCount} contradiction{contradictedCount !== 1 ? 's' : ''}
          </span>
        )}
        {contestedCount > 0 && (
          <span className="summary-pill summary-pill--contested">
            {contestedCount} contested
          </span>
        )}
        {verifiedCount > 0 && (
          <span className="summary-pill summary-pill--verified">
            {verifiedCount} verified
          </span>
        )}
      </div>

      <div className="verdict-list">
        {sorted.map((result, i) => {
          const cfg = VERDICT_CONFIG[result.status] ?? VERDICT_CONFIG.UNVERIFIABLE;
          const hasSource = result.source?.startsWith('http');

          return (
            <div key={i} className={`verdict-row verdict-row--${cfg.cls}`}>
              <span className={`verdict-badge verdict-badge--${cfg.cls}`}>
                {cfg.label}
              </span>

              <div className="verdict-body">
                <div className="verdict-meta">
                  <span className="verdict-model">{getModelShortName(result.model)}</span>
                  <span className="verdict-confidence">{result.original_confidence}/100</span>
                </div>

                <div className="verdict-claim">"{result.claim}"</div>

                {result.delta && (
                  <div className={`verdict-delta verdict-delta--${cfg.cls}`}>{result.delta}</div>
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
}
