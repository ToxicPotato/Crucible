"""JSON-based storage for conversations."""

import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR

_CONVERSATIONS_DIR = Path(DATA_DIR)
_CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


def _conversation_path(conversation_id: str) -> Path:
    return _CONVERSATIONS_DIR / f"{conversation_id}.json"


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": [],
        "settled_facts": [],
    }

    with open(_conversation_path(conversation_id), 'w') as f:
        json.dump(conversation, f, indent=2)

    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found
    """
    path = _conversation_path(conversation_id)

    if not path.exists():
        return None

    with open(path) as f:
        return json.load(f)


def save_conversation(conversation: Dict[str, Any]):
    """
    Save a conversation to storage.

    Args:
        conversation: Conversation dict to save
    """
    with open(_conversation_path(conversation['id']), 'w') as f:
        json.dump(conversation, f, indent=2)


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts
    """
    conversations = []
    for path in _CONVERSATIONS_DIR.glob('*.json'):
        with open(path) as f:
            data = json.load(f)
            conversations.append({
                "id": data["id"],
                "created_at": data["created_at"],
                "title": data.get("title", "New Conversation"),
                "message_count": len(data["messages"])
            })

    conversations.sort(key=lambda x: x["created_at"], reverse=True)
    return conversations


def add_user_message(conversation_id: str, content: str):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({"role": "user", "content": content})
    save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    stage25: List[Dict[str, Any]] = None,
):
    """
    Add an assistant message with all pipeline stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        stage2: List of model rankings
        stage3: Final synthesized response
        stage25: Stage 2.5 verification verdicts (optional, defaults to empty list)
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "assistant",
        "stage1": stage1,
        "stage2": stage2,
        "stage25": stage25 or [],
        "stage3": stage3,
    })
    save_conversation(conversation)


def add_settled_facts(conversation_id: str, new_facts: List[Dict[str, Any]]):
    """
    Append newly VERIFIED facts to the conversation's settled_facts list.

    Deduplicates by claim text so repeated verifications across turns don't
    accumulate duplicates. Backward-compatible: old conversations without a
    settled_facts field are treated as having an empty list.

    Args:
        conversation_id: Conversation identifier
        new_facts: List of fact dicts with at least a 'text' key
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    existing_texts = {f["text"] for f in conversation.get("settled_facts", [])}
    for fact in new_facts:
        if fact.get("text") and fact["text"] not in existing_texts:
            conversation.setdefault("settled_facts", []).append(fact)
            existing_texts.add(fact["text"])

    save_conversation(conversation)


def get_prior_synthesis(conversation: Dict[str, Any]) -> Optional[str]:
    """
    Return the Chairman's synthesis text from the most recent assistant turn.

    Used by Stage 3 to inject prior context into the Chairman prompt.
    Returns None if this is the first turn or no prior synthesis exists.

    Args:
        conversation: Conversation dict (may be a pre-turn snapshot)
    """
    for msg in reversed(conversation.get("messages", [])):
        if msg.get("role") == "assistant":
            return msg.get("stage3", {}).get("response")
    return None


def build_new_settled_facts(
    verification_results: List[Dict[str, Any]],
    conversation: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Build the list of newly VERIFIED facts ready to persist after a completed turn.

    Counts prior assistant turns from the conversation snapshot to assign
    source_turn. The snapshot must be captured before add_assistant_message
    is called for the current turn so the count reflects prior turns only.

    Args:
        verification_results: Stage 2.5 verdict list for the current turn
        conversation: Pre-turn conversation snapshot
    """
    turn_number = (
        sum(1 for m in conversation.get("messages", []) if m.get("role") == "assistant")
        + 1
    )
    return [
        {
            "text": r["claim"],
            "source": r.get("source", ""),
            "source_turn": turn_number,
        }
        for r in verification_results
        if r.get("status") == "VERIFIED"
    ]


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["title"] = title
    save_conversation(conversation)
