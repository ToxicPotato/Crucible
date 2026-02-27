import { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage25 from './Stage25';
import EpistemicSummary from './EpistemicSummary';
import Stage3Annotated from './Stage3Annotated';
import MinorityReport from './MinorityReport';
import EpistemicDrawer from './EpistemicDrawer';
import Phase0Review from './Phase0Review';
import './ChatInterface.css';

function ScrubIndicator({ original, reasoning }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="scrub-indicator">
      <button
        className={`scrub-badge${open ? ' scrub-badge--open' : ''}`}
        onClick={() => setOpen((o) => !o)}
        title="This prompt was scrubbed — click to see original"
      >
        ✦ scrubbed
      </button>
      {open && (
        <div className="scrub-detail">
          <div className="scrub-detail-row">
            <span className="scrub-detail-label">Original</span>
            <span className="scrub-detail-text">{original}</span>
          </div>
          {reasoning && (
            <div className="scrub-detail-row">
              <span className="scrub-detail-label">Reasoning</span>
              <span className="scrub-detail-text">{reasoning}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChatInterface({
  conversation,
  onSendMessage,
  isLoading,
  phase0State,
  onPhase0UseOriginal,
  onPhase0UseScrubbed,
  onPhase0Decline,
  errorMessage,
  onDismissError,
}) {
  const [input, setInput] = useState('');
  const [drawerOpenForIndex, setDrawerOpenForIndex] = useState(null);
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [conversation, phase0State]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      onSendMessage(input);
      setInput('');
    }
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  const drawerMsg =
    drawerOpenForIndex !== null ? conversation.messages[drawerOpenForIndex] : null;

  return (
    <div className="chat-interface">
      <div className="messages-container">
        {conversation.messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          conversation.messages.map((msg, index) => (
            <div key={index} className="message-group">
              {msg.role === 'user' ? (
                <div className="user-message">
                  <div className="message-label">You</div>
                  <div className="message-content">
                    <div className="markdown-content">
                      <ReactMarkdown>
                        {msg.usedScrubbed ? msg.scrubbedContent : msg.content}
                      </ReactMarkdown>
                    </div>
                  </div>
                  {msg.usedScrubbed && (
                    <ScrubIndicator original={msg.content} reasoning={msg.reasoning} />
                  )}
                </div>
              ) : (
                <div className="assistant-message">
                  <div className="message-label">LLM Council</div>

                  {/* Stage 1 */}
                  {msg.loading?.stage1 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 1: Collecting individual responses...</span>
                    </div>
                  )}
                  {msg.stage1 && <Stage1 responses={msg.stage1} />}

                  {/* Stage 2 */}
                  {msg.loading?.stage2 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 2: Peer rankings...</span>
                    </div>
                  )}
                  {msg.stage2 && (
                    <Stage2
                      rankings={msg.stage2}
                      labelToModel={msg.metadata?.label_to_model}
                      aggregateRankings={msg.metadata?.aggregate_rankings}
                    />
                  )}

                  {/* Stage 2.5 */}
                  {msg.loading?.stage25 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Stage 2.5: Verifying high-confidence claims...</span>
                    </div>
                  )}
                  {msg.stage25 && <Stage25 results={msg.stage25} />}

                  {/* Stage 3 with epistemic layer */}
                  {msg.loading?.stage3 && (
                    <div className="stage-loading">
                      <div className="spinner"></div>
                      <span>Running Stage 3: Final synthesis...</span>
                    </div>
                  )}
                  {msg.stage3 && (
                    <>
                      <EpistemicSummary
                        stage1={msg.stage1}
                        stage25={msg.stage25}
                        aggregateRankings={msg.metadata?.aggregate_rankings}
                      />
                      <Stage3Annotated
                        finalResponse={msg.stage3}
                        stage25={msg.stage25}
                        onOpenDrawer={() => setDrawerOpenForIndex(index)}
                      />
                      <MinorityReport
                        stage1={msg.stage1}
                        aggregateRankings={msg.metadata?.aggregate_rankings}
                      />
                    </>
                  )}
                </div>
              )}
            </div>
          ))
        )}

        {(phase0State.status === 'scrubbing' || phase0State.status === 'pending') && (
          <Phase0Review
            phase0State={phase0State}
            onUseOriginal={onPhase0UseOriginal}
            onUseScrubbed={onPhase0UseScrubbed}
            onDecline={onPhase0Decline}
          />
        )}

        {isLoading && phase0State?.status === 'idle' && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Drawer lives outside scroll container so it overlays the full viewport */}
      <EpistemicDrawer
        open={drawerOpenForIndex !== null && drawerMsg != null}
        onClose={() => setDrawerOpenForIndex(null)}
        stage25={drawerMsg?.stage25 ?? null}
        stage1={drawerMsg?.stage1 ?? null}
        aggregateRankings={drawerMsg?.metadata?.aggregate_rankings ?? null}
      />

      {errorMessage && (
        <div className="error-banner">
          <span>{errorMessage}</span>
          <button className="error-dismiss" onClick={onDismissError}>×</button>
        </div>
      )}

      {conversation.messages.length === 0 && (
        <form className="input-form" onSubmit={handleSubmit}>
          <textarea
            className="message-input"
            placeholder="Ask your question... (Shift+Enter for new line, Enter to send)"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            rows={3}
          />
          <button
            type="submit"
            className="send-button"
            disabled={!input.trim() || isLoading}
          >
            Send
          </button>
        </form>
      )}
    </div>
  );
}
