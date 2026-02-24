"""3-stage LLM Council orchestration."""

import re
from collections import defaultdict
from typing import List, Dict, Any, Tuple
from .openrouter import query_models_parallel, query_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL, SCRUBBER_MODEL, PHASE0_ENABLED


# ---------------------------------------------------------------------------
# TODO: Write the scrubber system prompt below.
#
# This is Phase 0's core logic — the instructions that define what the scrubber
# agent actually does. Consider these trade-offs:
#
#   - AGGRESSIVE: Rewrites everything into neutral academic language.
#     Pro: maximum bias removal. Con: may lose the user's actual intent.
#
#   - CONSERVATIVE: Only removes leading adjectives and loaded framing,
#     preserves domain, specificity, and structure.
#     Pro: stays faithful to what the user wanted to ask.
#     Con: subtle framing bias may survive.
#
#   - TRANSPARENCY: The prompt should instruct the model to return a JSON
#     object with "scrubbed" (the new question) and "reasoning" (what changed
#     and why). This is used in the frontend review card.
#
# Suggested structure to fill in (~8 lines):
#   "You are a prompt sanitization agent for an AI council deliberation system.
#    Your job is to rewrite the user's question so that it is [YOUR RULE HERE].
#    [WHAT TO PRESERVE], [WHAT TO REMOVE], [WHAT FORMAT TO USE].
#    Always return a JSON object: { \"scrubbed\": \"...\", \"reasoning\": \"...\" }"
# ---------------------------------------------------------------------------
SCRUBBER_SYSTEM_PROMPT = """You are the Phase 0 Scrubber Agent. Your job is to neutralize framing bias in user questions without altering the user's core intent, goal, or constraints.

1. ANALYZE: Identify leading language, loaded terms, binary framing, and hidden assumptions. Distinction: Scrutinize the *framing* (how it is asked), but accept the *operative context* (topic, specific actors, user identity, and simulation parameters) as inviolable data.

2. REWRITE:
   - NEUTRALIZE: Remove emotional/manipulative language. Turn leading presuppositions (e.g., "Why do all idiots...") into open questions, EXCEPT when the presupposition is the specific subject the user wants explained (e.g., "Why is this racist?"). In those cases, maintain the stance but use descriptive phrasing (e.g., "Explain why this is considered racist"). Ensure you do not alter the semantic truth-conditions of user-defined simulation parameters (e.g., do not change "hoax" to "not occurring" in a hypothetical context).
   - PRESERVE the user's stance/goal: If they ask to "argue for X," do not change it to "analyze X."
   - PRESERVE style: Do not remove modifiers, intensifiers, or adjectives describing the desired output style (e.g., "distinctively gritty," "very harsh") unless the words themselves are hate speech.
   - PRESERVE constraints: Keep format requirements, audience definitions, and technical details exactly as requested.
   - PRESERVE content: Do not scrub text provided for analysis (quoted text) or explicit creative style instructions.
   - PRESERVE safety: Do not soften, clinicalize, or euphemize words indicating harm, urgency, risk, or illegality. Keep the user's raw, direct language (e.g., keep "terrified" or "hurt myself"; do not change to "distress" or "self-harm").
   - NO ADDITIONS: Do not add semantic content. Do not insert new requests, pleasantries, questions, or "calls for help" that were not present in the original input. The output must be logically entailed by the input.

   EXCEPTIONS & OVERRIDES (these take priority over all REWRITE rules above):
   - CRISIS OVERRIDE: If the input contains explicit self-harm or suicide ideation, do not scrub anything. Pass the raw input unchanged to ensure the downstream model receives full diagnostic context.
   - PRESERVE SUBJECTIVE EXPERIENCE: Do not neutralize statements of first-person perception or identity (e.g., "feels racist to me," "As a Black woman") when that perception is the specific subject the user wants explained.
   - PRESERVE OPERATIVE ENTITIES: In scrutiny or fear contexts, preserve the specific actors named by the user (e.g., keep "neighbors," "boss," "government"). Only neutralize the certainty of the accusation (e.g., change "prove they are hacking" to "investigate whether they are accessing").
   - PRESERVE SIMULATION PARAMETERS: If the user creates a hypothetical scenario (e.g., "Assume X is a hoax"), preserve the constraint wording verbatim, UNLESS the parameter itself constitutes hate speech or promotes severe harm (e.g., "Assume [Group] is evil"). In hate speech cases, flag the input rather than passing it through.
   - DO NOT RESOLVE CONTRADICTIONS: If the user provides conflicting instructions (e.g., "Make it professional" AND "Keep this insult"), do not attempt to resolve the conflict. Pass the text through unchanged so the downstream council can address the logical inconsistency.

3. OUTPUT: Return ONLY a JSON object: {"scrubbed": "<the string>", "reasoning": "<brief explanation>"}.
   - If the prompt is already neutral or purely instructional (e.g., code fix), return it unchanged."""


async def phase0_scrub_prompt(user_query: str) -> Dict[str, str]:
    """
    Phase 0: Neutralize the user's prompt to remove implicit bias and leading framing.

    Args:
        user_query: The raw user question

    Returns:
        Dict with 'original', 'scrubbed', and 'reasoning' keys
    """
    if not PHASE0_ENABLED:
        return {
            "original": user_query,
            "scrubbed": user_query,
            "reasoning": "Phase 0 is disabled — scrubber prompt not yet configured.",
        }

    import json as _json

    scrub_prompt = f"""User question to sanitize:

                    {user_query}

                    Return ONLY a valid JSON object with no markdown or extra text:
                    {{"scrubbed": "<neutralized version of the question>", "reasoning": "<what you changed and why>"}}"""

    messages = [
        {"role": "system", "content": SCRUBBER_SYSTEM_PROMPT},
        {"role": "user", "content": scrub_prompt},
    ]

    response = await query_model(SCRUBBER_MODEL, messages, timeout=30.0)

    if response is None:
        return {
            "original": user_query,
            "scrubbed": user_query,
            "reasoning": "Scrubber unavailable — original prompt used unchanged.",
        }

    raw = response.get("content", "").strip()

    # Extract JSON by bracket position — handles fences, preamble, and trailing text
    # (failure mode #6: cheap models often add "Here is your JSON: {...}")
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start : end + 1]

    try:
        parsed = _json.loads(raw)
        return {
            "original": user_query,
            "scrubbed": parsed.get("scrubbed", user_query),
            "reasoning": parsed.get("reasoning", "No reasoning provided."),
        }
    except (_json.JSONDecodeError, ValueError):
        # Model didn't return valid JSON — use original prompt
        return {
            "original": user_query,
            "scrubbed": user_query,
            "reasoning": "Scrubber returned unparseable output — original prompt used.",
        }


# ---------------------------------------------------------------------------
# Stage 1 system prompt — designed and refined by the LLM Council itself.
#
# Design decisions (council consensus, v2):
#   1. TRANSPARENCY: models are told their answer will be peer-reviewed.
#      Framed as "verification" to prime for defensibility over rhetoric.
#   2. CONFIDENCE: hard source-based ceilings replace the old "90+ subtract"
#      rule, which models bypassed by anchoring below the trigger threshold.
#      Ceilings: recalled ≤90, reasoned ≤75, speculative ≤60.
#      50 is anchored as the "unsure but leaning" baseline so 75 reads as
#      a high score rather than a penalty.
#   3. "RECALLED A DEBATE" RULE: if the answer is contested/unresolved,
#      the source must be "reasoned" regardless of what the model remembers.
#   4. ANONYMITY: only counts (not text) of assumptions/unknowns are shown
#      in Stage 2 to prevent de-anonymization via phrasing style.
# ---------------------------------------------------------------------------
STAGE1_SYSTEM_PROMPT = """You are a Stage 1 model in a deliberation council. Your answer will be rigorously peer-reviewed and verified by other models. Favor precision and stated uncertainty over authoritative tone.

1. First, answer the user's question clearly and concisely in natural prose.
2. Second, add a blank line and append a single raw JSON object (no markdown fences, no extra text) containing metadata about your answer.

Use this JSON schema:
{
  "confidence": <integer 0-100>,
  "confidence_source": "recalled" | "reasoned" | "speculative",
  "key_assumptions": ["<str>", "<str>", "<str>"],
  "known_unknowns": ["<str>", "<str>", "<str>"]
}

METADATA DEFINITIONS:
- "confidence": Your subjective probability that your factual claims are correct.
  * Hard ceilings: "recalled" max 90, "reasoned" max 75, "speculative" max 60.
  * Treat 50 as the default "unsure but leaning" baseline; 75 signals a strong reasoned position.
  * Any score above 70 requires your key_assumptions to be written for a skeptical expert — no inline citations.
  * Scale: 50=unsure but leaning; 75=solid reasoned position; 90=stable recalled fact; 99=axiomatic truth.
- "confidence_source":
  * "recalled"    = stable, uncontested fact (APIs, historical dates, physical constants). If the topic is actively debated or unresolved, use "reasoned" instead — even if you remember reading about it.
  * "reasoned"    = derived via logic/inference, OR the correct answer is contested in the field.
  * "speculative" = best guess based on limited information.
- "key_assumptions": List 1-3 load-bearing premises. Include framing choices and falsifiable facts the answer depends on. If these are wrong, your answer collapses.
- "known_unknowns": List 1-3 specific missing pieces of information that, if known, would meaningfully improve your answer.

NOTE: To preserve anonymity in peer review, only the numeric counts of your assumptions and unknowns will be shown to other models — not their text. Your confidence score alone must reflect the full strength of your position."""


def parse_stage1_metadata(raw: str) -> tuple:
    """
    Extract prose and JSON metadata from a Stage 1 model response.

    Models are instructed to append a JSON block at the end of their prose answer.
    This function splits the two and returns both.

    Args:
        raw: Full model response (prose + JSON block at end)

    Returns:
        (prose: str, metadata: dict) — prose is text before the JSON block.
        On parse failure, returns (raw, empty metadata dict).
    """
    import json as _json

    empty_meta = {
        "confidence": None,
        "confidence_source": None,
        "key_assumptions": [],
        "known_unknowns": [],
    }

    # Use rfind to get the LAST { and } — prose may contain JSON-like text,
    # but the metadata block is always the final one appended by the model.
    start = raw.rfind("{")
    end = raw.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return raw, empty_meta

    json_str = raw[start : end + 1]
    prose = raw[:start].rstrip()

    # Normalise confidence_source to one of the three allowed values
    _valid_sources = {"recalled", "reasoned", "speculative"}

    try:
        parsed = _json.loads(json_str)
        raw_source = parsed.get("confidence_source", "")
        meta = {
            "confidence": parsed.get("confidence"),
            "confidence_source": raw_source if raw_source in _valid_sources else None,
            "key_assumptions": parsed.get("key_assumptions", []),
            "known_unknowns": parsed.get("known_unknowns", []),
        }
        return prose, meta
    except (_json.JSONDecodeError, ValueError):
        return raw, empty_meta


async def stage1_collect_responses(user_query: str) -> List[Dict[str, Any]]:
    """
    Stage 1: Collect individual responses from all council models.

    Args:
        user_query: The user's question

    Returns:
        List of dicts with 'model' and 'response' keys
    """
    messages = [
        {"role": "system", "content": STAGE1_SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
    ]

    # Query all models in parallel
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    # Format results — parse prose and JSON metadata from each response
    stage1_results = []
    for model, response in responses.items():
        if response is not None:  # Only include successful responses
            raw = response.get('content', '')
            prose, meta = parse_stage1_metadata(raw)
            stage1_results.append({
                "model": model,
                "response": prose,
                "confidence": meta["confidence"],
                "confidence_source": meta["confidence_source"],
                "key_assumptions": meta["key_assumptions"],
                "known_unknowns": meta["known_unknowns"],
            })

    return stage1_results


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses.

    Args:
        user_query: The original user query
        stage1_results: Results from Stage 1

    Returns:
        Tuple of (rankings list, label_to_model mapping)
    """
    # Create anonymized labels for responses (Response A, Response B, etc.)
    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, ...

    # Create mapping from label to model name
    label_to_model = {
        f"Response {label}": result['model']
        for label, result in zip(labels, stage1_results)
    }

    # Build the ranking prompt.
    # Anonymity rule: show confidence + source (numeric signal) but only the
    # *count* of assumptions/unknowns — never their text. Showing the full
    # text would let rankers identify models by phrasing style and rank
    # strategically rather than objectively.
    response_blocks = []
    for label, result in zip(labels, stage1_results):
        block = f"Response {label}:\n{result['response']}"
        meta_lines = []
        if result.get("confidence") is not None:
            src = result.get("confidence_source")
            src_tag = f" ({src})" if src else ""
            meta_lines.append(f"Confidence: {result['confidence']}/100{src_tag}")
        n_assumptions = len(result.get("key_assumptions") or [])
        n_unknowns = len(result.get("known_unknowns") or [])
        if n_assumptions > 0:
            meta_lines.append(f"Assumptions listed: {n_assumptions}")
        if n_unknowns > 0:
            meta_lines.append(f"Unknowns listed: {n_unknowns}")
        if meta_lines:
            block += "\n\n" + "\n".join(meta_lines)
        response_blocks.append(block)
    responses_text = "\n\n---\n\n".join(response_blocks)

    ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]

    # Get rankings from all council models in parallel
    responses = await query_models_parallel(COUNCIL_MODELS, messages)

    # Format results
    stage2_results = []
    for model, response in responses.items():
        if response is not None:
            full_text = response.get('content', '')
            parsed = parse_ranking_from_text(full_text)
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parsed
            })

    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2

    Returns:
        Dict with 'model' and 'response' keys
    """
    # Build comprehensive context for chairman — include metadata per model
    stage1_blocks = []
    for result in stage1_results:
        lines = [f"Model: {result['model']}", f"Response: {result['response']}"]
        if result.get("confidence") is not None:
            src = result.get("confidence_source")
            src_tag = f" ({src})" if src else ""
            lines.append(f"Confidence: {result['confidence']}/100{src_tag}")
        if result.get("key_assumptions"):
            lines.append(f"Key assumptions: {result['key_assumptions']}")
        if result.get("known_unknowns"):
            lines.append(f"Known unknowns: {result['known_unknowns']}")
        stage1_blocks.append("\n".join(lines))
    stage1_text = "\n\n".join(stage1_blocks)

    stage2_text = "\n\n".join([
        f"Model: {result['model']}\nRanking: {result['ranking']}"
        for result in stage2_results
    ])

    chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    # Query the chairman model
    response = await query_model(CHAIRMAN_MODEL, messages)

    if response is None:
        # Fallback if chairman fails
        return {
            "model": CHAIRMAN_MODEL,
            "response": "Error: Unable to generate final synthesis."
        }

    return {
        "model": CHAIRMAN_MODEL,
        "response": response.get('content', '')
    }


def parse_ranking_from_text(ranking_text: str) -> List[str]:
    """
    Parse the FINAL RANKING section from the model's response.

    Args:
        ranking_text: The full text response from the model

    Returns:
        List of response labels in ranked order
    """
    # Look for "FINAL RANKING:" section
    if "FINAL RANKING:" in ranking_text:
        # Extract everything after "FINAL RANKING:"
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            ranking_section = parts[1]
            # Try to extract numbered list format (e.g., "1. Response A")
            # This pattern looks for: number, period, optional space, "Response X"
            numbered_matches = re.findall(r'\d+\.\s*Response [A-Z]', ranking_section)
            if numbered_matches:
                # Extract just the "Response X" part
                return [re.search(r'Response [A-Z]', m).group() for m in numbered_matches]

            # Fallback: Extract all "Response X" patterns in order
            matches = re.findall(r'Response [A-Z]', ranking_section)
            return matches

    # Fallback: try to find any "Response X" patterns in order
    matches = re.findall(r'Response [A-Z]', ranking_text)
    return matches


def calculate_aggregate_rankings(
    stage2_results: List[Dict[str, Any]],
    label_to_model: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Calculate aggregate rankings across all models.

    Args:
        stage2_results: Rankings from each model
        label_to_model: Mapping from anonymous labels to model names

    Returns:
        List of dicts with model name and average rank, sorted best to worst
    """
    # Track positions for each model
    model_positions = defaultdict(list)

    for ranking in stage2_results:
        ranking_text = ranking['ranking']

        # Parse the ranking from the structured format
        parsed_ranking = parse_ranking_from_text(ranking_text)

        for position, label in enumerate(parsed_ranking, start=1):
            if label in label_to_model:
                model_name = label_to_model[label]
                model_positions[model_name].append(position)

    # Calculate average position for each model
    aggregate = []
    for model, positions in model_positions.items():
        if positions:
            avg_rank = sum(positions) / len(positions)
            aggregate.append({
                "model": model,
                "average_rank": round(avg_rank, 2),
                "rankings_count": len(positions)
            })

    # Sort by average rank (lower is better)
    aggregate.sort(key=lambda x: x['average_rank'])

    return aggregate


async def generate_conversation_title(user_query: str) -> str:
    """
    Generate a short title for a conversation based on the first user message.

    Args:
        user_query: The first user message

    Returns:
        A short title (3-5 words)
    """
    title_prompt = f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""

    messages = [{"role": "user", "content": title_prompt}]

    # Use gemini-2.5-flash for title generation (fast and cheap)
    response = await query_model("google/gemini-2.5-flash", messages, timeout=30.0)

    if response is None:
        # Fallback to a generic title
        return "New Conversation"

    title = response.get('content', 'New Conversation').strip()

    # Clean up the title - remove quotes, limit length
    title = title.strip('"\'')

    # Truncate if too long
    if len(title) > 50:
        title = title[:47] + "..."

    return title


async def run_full_council(user_query: str) -> Tuple[List, List, Dict, Dict]:
    """
    Run the complete 3-stage council process.

    Args:
        user_query: The user's question

    Returns:
        Tuple of (stage1_results, stage2_results, stage3_result, metadata)
    """
    # Stage 1: Collect individual responses
    stage1_results = await stage1_collect_responses(user_query)

    # If no models responded successfully, return error
    if not stage1_results:
        return [], [], {
            "model": "error",
            "response": "All models failed to respond. Please try again."
        }, {}

    # Stage 2: Collect rankings
    stage2_results, label_to_model = await stage2_collect_rankings(user_query, stage1_results)

    # Calculate aggregate rankings
    aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)

    # Stage 3: Synthesize final answer
    stage3_result = await stage3_synthesize_final(
        user_query,
        stage1_results,
        stage2_results
    )

    # Prepare metadata
    metadata = {
        "label_to_model": label_to_model,
        "aggregate_rankings": aggregate_rankings
    }

    return stage1_results, stage2_results, stage3_result, metadata
