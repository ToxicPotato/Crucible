import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeRaw from 'rehype-raw';
import { getModelShortName } from '../utils';
import { annotateChairmanText } from '../utils/epistemic';
import ClaimPopover from './ClaimPopover';
import './Stage3.css';
import './Stage3Annotated.css';

export default function Stage3Annotated({ finalResponse, stage25, onOpenDrawer }) {
  const [popover, setPopover] = useState(null);

  if (!finalResponse) return null;

  const hasAnnotations = stage25 && stage25.length > 0;
  const annotatedText = hasAnnotations
    ? annotateChairmanText(finalResponse.response, stage25)
    : finalResponse.response;

  function handleClick(e) {
    const el = e.target.closest('.claim-annotation');
    if (!el) {
      setPopover(null);
      return;
    }
    const idx = parseInt(el.dataset.claimIdx, 10);
    const claim = stage25[idx];
    if (!claim) return;
    const rect = el.getBoundingClientRect();
    setPopover({ claim, x: rect.left, y: rect.bottom + 6 });
  }

  return (
    <div className="stage stage3">
      <h3 className="stage-title stage3-annotated-title">
        <span>Stage 3: Final Council Answer</span>
        {onOpenDrawer && (
          <button className="epistemic-view-btn" onClick={onOpenDrawer}>
            ⊞ Epistemic View
          </button>
        )}
      </h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {getModelShortName(finalResponse.model)}
        </div>
        <div
          className="final-text markdown-content"
          onClick={handleClick}
        >
          {hasAnnotations ? (
            <ReactMarkdown rehypePlugins={[rehypeRaw]}>{annotatedText}</ReactMarkdown>
          ) : (
            <ReactMarkdown>{annotatedText}</ReactMarkdown>
          )}
        </div>
      </div>

      {popover && (
        <ClaimPopover
          claim={popover.claim}
          x={popover.x}
          y={popover.y}
          onClose={() => setPopover(null)}
        />
      )}
    </div>
  );
}
