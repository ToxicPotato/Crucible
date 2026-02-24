import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

const PHASE0_IDLE = { status: 'idle', original: '', scrubbed: '', reasoning: '' };

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [phase0State, setPhase0State] = useState(PHASE0_IDLE);

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  // Phase 0: scrub prompt and show review card (no credits spent yet)
  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);

    // Optimistically add user message to UI
    setCurrentConversation((prev) => ({
      ...prev,
      messages: [...prev.messages, { role: 'user', content }],
    }));

    setPhase0State({ ...PHASE0_IDLE, status: 'scrubbing' });

    try {
      const result = await api.scrubPhase0(currentConversationId, content);
      setPhase0State({
        status: 'pending',
        original: result.original,
        scrubbed: result.scrubbed,
        reasoning: result.reasoning,
      });
    } catch (error) {
      console.error('Phase 0 failed:', error);
      // Scrubber failed — remove optimistic message and reset
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -1),
      }));
      setPhase0State(PHASE0_IDLE);
      setIsLoading(false);
    }
  };

  // Helper used by handlePhase0Accept to stream stages
  const runCouncilStream = async (originalContent, scrubbedContent) => {
    // Create a partial assistant message that will be updated progressively
    setCurrentConversation((prev) => ({
      ...prev,
      messages: [
        ...prev.messages,
        {
          role: 'assistant',
          stage1: null,
          stage2: null,
          stage3: null,
          metadata: null,
          loading: { stage1: false, stage2: false, stage3: false },
        },
      ],
    }));

    const updateLastMessage = (updater) => {
      setCurrentConversation((prev) => {
        const messages = [...prev.messages];
        updater(messages[messages.length - 1]);
        return { ...prev, messages };
      });
    };

    try {
      await api.sendMessageStream(
        currentConversationId,
        originalContent,
        (eventType, event) => {
          switch (eventType) {
            case 'stage1_start':
              updateLastMessage((msg) => { msg.loading.stage1 = true; });
              break;
            case 'stage1_complete':
              updateLastMessage((msg) => { msg.stage1 = event.data; msg.loading.stage1 = false; });
              break;
            case 'stage2_start':
              updateLastMessage((msg) => { msg.loading.stage2 = true; });
              break;
            case 'stage2_complete':
              updateLastMessage((msg) => { msg.stage2 = event.data; msg.metadata = event.metadata; msg.loading.stage2 = false; });
              break;
            case 'stage3_start':
              updateLastMessage((msg) => { msg.loading.stage3 = true; });
              break;
            case 'stage3_complete':
              updateLastMessage((msg) => { msg.stage3 = event.data; msg.loading.stage3 = false; });
              break;
            case 'title_complete':
              loadConversations();
              break;
            case 'complete':
              loadConversations();
              setIsLoading(false);
              break;
            case 'error':
              console.error('Stream error:', event.message);
              setIsLoading(false);
              break;
          }
        },
        scrubbedContent
      );
    } catch (error) {
      console.error('Failed to send message:', error);
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  };

  const handlePhase0UseOriginal = () => {
    const originalContent = phase0State.original;
    setPhase0State(PHASE0_IDLE);
    runCouncilStream(originalContent, null);
  };

  const handlePhase0UseScrubbed = () => {
    const { original, scrubbed, reasoning } = phase0State;
    // Stamp scrub metadata onto the optimistic user message so the chat can
    // display the scrubbed text with an indicator and let the user peek at the original.
    setCurrentConversation((prev) => {
      const messages = [...prev.messages];
      const lastUserIdx = messages.findLastIndex((m) => m.role === 'user');
      if (lastUserIdx !== -1) {
        messages[lastUserIdx] = {
          ...messages[lastUserIdx],
          scrubbedContent: scrubbed,
          reasoning,
          usedScrubbed: true,
        };
      }
      return { ...prev, messages };
    });
    setPhase0State(PHASE0_IDLE);
    runCouncilStream(original, scrubbed);
  };

  const handlePhase0Decline = () => {
    // Remove the optimistic user message and reset everything
    setCurrentConversation((prev) => ({
      ...prev,
      messages: prev.messages.slice(0, -1),
    }));
    setPhase0State(PHASE0_IDLE);
    setIsLoading(false);
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        phase0State={phase0State}
        onPhase0UseOriginal={handlePhase0UseOriginal}
        onPhase0UseScrubbed={handlePhase0UseScrubbed}
        onPhase0Decline={handlePhase0Decline}
      />
    </div>
  );
}

export default App;
