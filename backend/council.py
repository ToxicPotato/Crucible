"""3-stage LLM Council orchestration."""

import re
import asyncio
import json as _json
from collections import defaultdict
from typing import List, Dict, Any, Tuple
from .openrouter import query_models_parallel, query_model
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL, SCRUBBER_MODEL, PHASE0_ENABLED
from .verifier import stage25_verify_claims, format_verification_context


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


STAGE1_SYSTEM_PROMPT = """You are a Stage 1 model in a deliberation council. Your answer will be rigorously peer-reviewed and verified by other models. Favor precision and stated uncertainty over authoritative tone.

1. First, answer the user's question clearly and concisely in natural prose.
2. Second, add a blank line and append a single raw JSON object (no markdown fences, no extra text) containing metadata about your answer.

Use this JSON schema:
{
  "confidence": <integer 0-100>,
  "confidence_source": "recalled" | "reasoned" | "speculative",
  "key_assumptions": ["<str>", "<str>"],
  "factual_claims": ["<str>", "<str>"],
  "known_unknowns": ["<str>", "<str>"]
}

METADATA DEFINITIONS:
- "confidence": Your subjective probability that your factual claims are correct.
  * Hard ceilings: "recalled" max 90, "reasoned" max 75, "speculative" max 60.
  * Treat 50 as the default "unsure but leaning" baseline; 75 signals a strong reasoned position.
  * Any score above 70 requires your factual_claims to be written for a skeptical expert — no inline citations.
  * Scale: 50=unsure but leaning; 75=solid reasoned position; 90=stable recalled fact; 99=axiomatic truth.
- "confidence_source":
  * "recalled"    = stable, uncontested fact (APIs, historical dates, physical constants). If the topic is actively debated or unresolved, use "reasoned" instead — even if you remember reading about it.
  * "reasoned"    = derived via logic/inference, OR the correct answer is contested in the field.
  * "speculative" = best guess based on limited information.
- "key_assumptions": List 1-3 load-bearing FRAMING premises — how you're interpreting the question, what scope or context you're assuming, what the user probably means. These are interpretive choices, not verifiable facts.
- "factual_claims": List 0-3 specific, concrete, independently verifiable facts your answer asserts. Each must be falsifiable by web search. CORRECT: "Bell's patent 174465 was granted March 7, 1876". WRONG: "I interpret the question as asking about X". Leave empty [] if no specific facts are asserted.
- "known_unknowns": List 1-3 specific missing pieces of information that, if known, would meaningfully improve your answer.

NOTE: In peer review, your factual claims and framing assumptions will be shown to other models in style-neutralized form. Rankers will evaluate both your confidence score and whether your specific claims are proportionate to that confidence. Write factual_claims that are precise and falsifiable."""


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
    empty_meta = {
        "confidence": None,
        "confidence_source": None,
        "key_assumptions": [],
        "factual_claims": [],
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
            "factual_claims": parsed.get("factual_claims", []),
            "known_unknowns": parsed.get("known_unknowns", []),
        }
        return prose, meta
    except (_json.JSONDecodeError, ValueError):
        return raw, empty_meta


async def _scrub_metadata_texts(texts: List[str]) -> List[str]:
    """
    Batch-rewrite factual claims and assumptions in neutral, third-person, technical prose.

    Removes stylistic fingerprints (first-person phrasing, idiomatic expressions,
    hedging patterns) that high-capability rankers could use to de-anonymize peers.
    All texts are sent in a single LLM call to minimize latency.

    Falls back to the original texts if the LLM call fails or returns an unexpected
    shape — rankers will see unscrubed text rather than no text.
    """
    if not texts:
        return []

    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "Rewrite each of the following factual claims and framing assumptions in neutral, "
        "third-person, technical prose. Remove stylistic fingerprints (first-person phrasing, "
        "idiomatic expressions, hedging patterns) while preserving exact semantic content.\n\n"
        "Return ONLY a JSON array of rewritten strings in the same order as the input:\n"
        "[\"<rewritten 1>\", \"<rewritten 2>\", ...]\n\n"
        "Input items:\n" + numbered
    )
    response = await query_model(
        SCRUBBER_MODEL,
        [{"role": "user", "content": prompt}],
        timeout=30.0,
    )
    if response is None:
        print("[scrub] metadata scrub failed (LLM unavailable) — using original unscrubbed texts")
        return texts

    raw = response.get("content", "").strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        print("[scrub] metadata scrub returned unparseable output — using original unscrubbed texts")
        return texts

    try:
        rewritten = _json.loads(raw[start:end + 1])
        if isinstance(rewritten, list) and len(rewritten) == len(texts):
            return [str(s) for s in rewritten]
        print(f"[scrub] metadata scrub returned {len(rewritten) if isinstance(rewritten, list) else 'non-list'} items, expected {len(texts)} — using original unscrubbed texts")
    except (_json.JSONDecodeError, ValueError):
        print("[scrub] metadata scrub returned invalid JSON — using original unscrubbed texts")

    return texts


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
                "factual_claims": meta["factual_claims"],
                "known_unknowns": meta["known_unknowns"],
            })
        else:
            print(f"[stage1] model {model} returned no response — excluded from council")

    print(f"[stage1] {len(stage1_results)}/{len(COUNCIL_MODELS)} models responded")
    return stage1_results


async def _build_scrubbed_metadata(
    stage1_results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, List[str]]]:
    """
    Batch-scrub all factual claims and key assumptions across all Stage 1 models.

    Builds a flat list, sends a single LLM call via _scrub_metadata_texts, then
    reconstructs per-model data using positional tracking.

    Returns a dict mapping model_id -> {factual_claims: [...], key_assumptions: [...]}.
    """
    all_texts: List[str] = []
    text_positions: Dict[tuple, int] = {}  # (model, field, idx) -> flat index

    for result in stage1_results:
        model = result["model"]
        for i, claim in enumerate(result.get("factual_claims") or []):
            text_positions[(model, "factual_claims", i)] = len(all_texts)
            all_texts.append(claim)
        for i, assumption in enumerate(result.get("key_assumptions") or []):
            text_positions[(model, "key_assumptions", i)] = len(all_texts)
            all_texts.append(assumption)

    scrubbed_texts = await _scrub_metadata_texts(all_texts)

    scrubbed_meta: Dict[str, Dict[str, List[str]]] = {}
    for result in stage1_results:
        model = result["model"]
        scrubbed_meta[model] = {
            "factual_claims": [
                scrubbed_texts[text_positions[(model, "factual_claims", i)]]
                for i in range(len(result.get("factual_claims") or []))
            ],
            "key_assumptions": [
                scrubbed_texts[text_positions[(model, "key_assumptions", i)]]
                for i in range(len(result.get("key_assumptions") or []))
            ],
        }
    return scrubbed_meta


async def _query_ranker(
    ranker_model: str,
    stage1_results: List[Dict[str, Any]],
    labels: List[str],
    model_to_label: Dict[str, str],
    scrubbed_meta: Dict[str, Dict[str, List[str]]],
    user_query: str,
) -> Tuple[str, Any]:
    """
    Build a ranking prompt for one ranker model and query it.

    Each ranker sees all peer responses except its own (self-exclusion to
    eliminate self-serving bias). Returns (ranker_model, response_or_None).
    """
    own_label = model_to_label.get(ranker_model)
    visible = [
        (label, result)
        for label, result in zip(labels, stage1_results)
        if f"Response {label}" != own_label
    ]

    if not visible:
        return ranker_model, None

    response_blocks = []
    for label, result in visible:
        block = f"Response {label}:\n{result['response']}"
        meta_lines = []
        if result.get("confidence") is not None:
            src = result.get("confidence_source")
            src_tag = f" ({src})" if src else ""
            meta_lines.append(f"Confidence: {result['confidence']}/100{src_tag}")

        sm = scrubbed_meta.get(result["model"], {})
        if sm.get("factual_claims"):
            claims_str = "; ".join(f'"{c}"' for c in sm["factual_claims"])
            meta_lines.append(f"Factual claims: {claims_str}")
        if sm.get("key_assumptions"):
            assumptions_str = "; ".join(f'"{a}"' for a in sm["key_assumptions"])
            meta_lines.append(f"Framing assumptions: {assumptions_str}")

        n_unknowns = len(result.get("known_unknowns") or [])
        if n_unknowns > 0:
            meta_lines.append(f"Unknowns listed: {n_unknowns}")

        if meta_lines:
            block += "\n\n" + "\n".join(meta_lines)
        response_blocks.append(block)

    responses_text = "\n\n---\n\n".join(response_blocks)

    ranking_prompt = f"""You are evaluating responses to the following question:

Question: {user_query}

Here are {len(visible)} responses from anonymous models (your own response has been excluded to eliminate self-serving bias):

{responses_text}

Your task:
1. Evaluate each response individually: what it does well and what it does poorly.
2. At the very end, provide a final ranking.

EPISTEMIC CALIBRATION — apply when ranking:
Treat all metadata (confidence scores, factual claims, assumptions) as TESTIMONY — a model's
self-assessment, not verified truth. Evaluate whether that self-assessment is appropriate.

Reward calibration:
- A model reporting "reasoned 65" on a contested topic shows better epistemic hygiene
  than one reporting "recalled 90" on the same topic.
- A model whose factual claims are specific, falsifiable, and proportionate to its stated
  confidence should be ranked UP.

Penalize Epistemic Arrogance: If a model claims ≥90 ("recalled") on any of:
  - Topics actively debated, evolving, or unresolved in the field
  - Philosophical, normative, or value-laden questions with no empirical ground truth
  - Causal predictions requiring inference, not stable memory
  - Specific claims that are vague or unfalsifiable despite a high confidence score
...rank that response DOWN for poor calibration. Overconfidence is more dangerous than stated uncertainty.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with "FINAL RANKING:" (all caps, with colon)
- List responses from best to worst as a numbered list
- Each line: number, period, space, response label ONLY (e.g., "1. Response A")
- Do not add explanations in the ranking section

Now provide your evaluation and ranking:"""

    messages = [{"role": "user", "content": ranking_prompt}]
    response = await query_model(ranker_model, messages)
    return ranker_model, response


async def stage2_collect_rankings(
    user_query: str,
    stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Stage 2: Each model ranks the anonymized responses of its peers (not itself).

    Self-exclusion eliminates self-serving bias. Metadata texts are style-scrubbed
    before being shown to rankers to prevent de-anonymization via phrasing fingerprints.
    calculate_aggregate_rankings() handles missing self-ranks by averaging only the
    positions each model actually receives.
    """
    labels = [chr(65 + i) for i in range(len(stage1_results))]  # A, B, C, D
    label_to_model = {
        f"Response {label}": result["model"]
        for label, result in zip(labels, stage1_results)
    }
    model_to_label = {v: k for k, v in label_to_model.items()}

    scrubbed_meta = await _build_scrubbed_metadata(stage1_results)

    responded_models = [r["model"] for r in stage1_results]
    ranking_responses = await asyncio.gather(
        *[
            _query_ranker(m, stage1_results, labels, model_to_label, scrubbed_meta, user_query)
            for m in responded_models
        ]
    )

    stage2_results = []
    for model, response in ranking_responses:
        if response is not None:
            full_text = response.get("content", "")
            stage2_results.append({
                "model": model,
                "ranking": full_text,
                "parsed_ranking": parse_ranking_from_text(full_text),
            })
        else:
            print(f"[stage2] ranker {model} returned no response — excluded from rankings")

    print(f"[stage2] {len(stage2_results)}/{len(responded_models)} rankers responded")
    return stage2_results, label_to_model


async def stage3_synthesize_final(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    verification_results: List[Dict[str, Any]] = None,
    settled_facts: List[Dict[str, Any]] = None,
    prior_synthesis: str = None,
) -> Dict[str, Any]:
    """
    Stage 3: Chairman synthesizes final response.

    Args:
        user_query: The original user query
        stage1_results: Individual model responses from Stage 1
        stage2_results: Rankings from Stage 2
        verification_results: Optional Stage 2.5 validation blocks (grouped by model)
        settled_facts: VERIFIED claims from prior turns in this session
        prior_synthesis: The Chairman's synthesis from the immediately preceding turn

    Returns:
        Dict with 'model' and 'response' keys
    """
    # Build [PRIOR COUNCIL CONTEXT] block if session memory is available
    prior_context_block = ""
    if settled_facts or prior_synthesis:
        prior_lines = ["[PRIOR COUNCIL CONTEXT]"]
        if settled_facts:
            prior_lines.append("Settled Facts (externally verified in this session):")
            for fact in settled_facts:
                source = f" (source: {fact['source']})" if fact.get("source") else ""
                prior_lines.append(f"  - \"{fact['text']}\"{source}")
        if prior_synthesis:
            prior_lines.append("\nMost Recent Chairman Synthesis:")
            prior_lines.append(prior_synthesis)
        prior_context_block = "\n".join(prior_lines) + "\n\n"

    # Build comprehensive Stage 1 context — Chairman sees full metadata
    stage1_blocks = []
    for result in stage1_results:
        lines = [f"Model: {result['model']}", f"Response: {result['response']}"]
        if result.get("confidence") is not None:
            src = result.get("confidence_source")
            src_tag = f" ({src})" if src else ""
            lines.append(f"Confidence: {result['confidence']}/100{src_tag}")
        if result.get("factual_claims"):
            lines.append(f"Factual claims: {result['factual_claims']}")
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

    # Inject Stage 2.5 verification context (grouped by model) if available
    verification_section = ""
    if verification_results:
        verification_context = format_verification_context(verification_results)
        if verification_context:
            verification_section = f"\n\nSTAGE 2.5 — External Fact-Check:\n{verification_context}\n"

    chairman_prompt = f"""{prior_context_block}You are the Chairman of an LLM Council. Multiple AI models have answered a question and peer-ranked each other.

SYNTHESIS PROTOCOL — apply in this order:
1. EXTERNAL PRIMACY: Claims marked [✓ VERIFIED] are externally confirmed — treat them as ground truth. Claims marked [! CONTRADICTED] must be corrected in your synthesis, not averaged away. Claims marked [~ CONTESTED] represent genuine disagreement — describe both positions; do not pick a winner.
2. DIAGNOSTIC MODE: If council models explicitly disagree on a factual claim AND Stage 2.5 returned UNVERIFIABLE or CONTESTED for that claim, report the disagreement explicitly rather than guessing a resolution.
3. SETTLED FACTS: If prior session context includes Settled Facts relevant to this query, anchor your synthesis with them.
4. CONSENSUS: Where models agree and no verification conflicts, synthesize normally.

Original Question: {user_query}

STAGE 1 — Individual Responses:
{stage1_text}

STAGE 2 — Peer Rankings:
{stage2_text}{verification_section}
Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

    messages = [{"role": "user", "content": chairman_prompt}]

    response = await query_model(CHAIRMAN_MODEL, messages)

    if response is None:
        return {
            "model": CHAIRMAN_MODEL,
            "response": "Error: Unable to generate final synthesis."
        }

    return {
        "model": CHAIRMAN_MODEL,
        "response": response.get("content", "")
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
        parsed_ranking = ranking['parsed_ranking']

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

    response = await query_model(SCRUBBER_MODEL, messages, timeout=30.0)

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


