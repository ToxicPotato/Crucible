import './Phase0Review.css';

export default function Phase0Review({ phase0State, onUseOriginal, onUseScrubbed, onDecline }) {
  if (phase0State.status === 'scrubbing') {
    return (
      <div className="phase0-review phase0-review--loading">
        <div className="phase0-header">
          <span className="phase0-badge">Phase 0</span>
          <span className="phase0-title">Sanitizing prompt...</span>
          <div className="phase0-spinner" />
        </div>
      </div>
    );
  }

  if (phase0State.status !== 'pending') return null;

  const unchanged = phase0State.original.trim() === phase0State.scrubbed.trim();

  return (
    <div className="phase0-review">
      <div className="phase0-header">
        <span className="phase0-badge">Phase 0</span>
        <span className="phase0-title">Prompt Sanitization Review</span>
      </div>

      <div className="phase0-columns">
        <div className="phase0-col">
          <div className="phase0-col-label">Original</div>
          <div className="phase0-text">{phase0State.original}</div>
        </div>
        <div className="phase0-col">
          <div className="phase0-col-label">
            Scrubbed
            {unchanged && <span className="phase0-unchanged-tag">no changes</span>}
          </div>
          <div className={`phase0-text ${unchanged ? '' : 'phase0-text--changed'}`}>
            {phase0State.scrubbed}
          </div>
        </div>
      </div>

      {phase0State.reasoning && (
        <div className="phase0-reasoning">
          <span className="phase0-reasoning-label">Reasoning: </span>
          {phase0State.reasoning}
        </div>
      )}

      <div className="phase0-actions">
        <button className="phase0-btn phase0-btn--decline" onClick={onDecline}>
          Cancel
        </button>
        {unchanged ? (
          <button className="phase0-btn phase0-btn--accept" onClick={onUseOriginal}>
            Continue →
          </button>
        ) : (
          <>
            <button className="phase0-btn phase0-btn--original" onClick={onUseOriginal}>
              Use Original
            </button>
            <button className="phase0-btn phase0-btn--accept" onClick={onUseScrubbed}>
              Use Scrubbed →
            </button>
          </>
        )}
      </div>
    </div>
  );
}
