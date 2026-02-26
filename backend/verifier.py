"""Stage 2.5: Spot-check Verifier Agent.

Intercepts the top-2 ranked responses from Stage 2 before Chairman synthesis.
For each high-confidence (>75) factual claim, it:
  1. Generates a corroboration AND refutation search query (via LLM, single call)
  2. Executes both Tavily web searches in parallel
  3. Validates the claim against both result sets (via LLM)
  4. Produces a structured verdict: VERIFIED, CONTRADICTED, CONTESTED, or UNVERIFIABLE

Verdicts are grouped by source model in format_verification_context() so the
Chairman can assess per-model credibility rather than a flat fact list.
"""

import json as _json
import asyncio
import httpx
from collections import defaultdict
from typing import List, Dict, Any, Tuple

from .config import VERIFIER_MODEL, STAGE25_ENABLED, TAVILY_API_KEY
from .openrouter import query_model


# ---------------------------------------------------------------------------
# Stage 2.5 system prompt.
#
# CONTESTED is the key new verdict. It fires when credible evidence exists on
# both sides — the Chairman must report the controversy, not resolve it.
# UNVERIFIABLE remains the safe default: absence of corroboration ≠ contradiction.
# ---------------------------------------------------------------------------
VERIFIER_SYSTEM_PROMPT = """You are Stage 2.5 — a Spot-check Verifier in an LLM deliberation council.

Your task: Evaluate whether a single factual claim is supported or refuted by the provided web search results. You receive two sets of results: one from a corroboration search and one from a refutation search.

VERDICT RULES:
- VERIFIED: Corroboration results directly support the claim AND refutation search found no meaningful counter-evidence.
- CONTRADICTED: A refutation source explicitly and directly negates the claim. Direct evidence only.
- CONTESTED: Both corroboration and refutation searches returned credible, conflicting evidence. Legitimate sources disagree.
- UNVERIFIABLE: Results are insufficient, irrelevant, or the claim is philosophical/normative/predictive/opinion-based with no verifiable ground truth.

CRITICAL BIAS GUARD:
- Absence of corroboration is NOT evidence of contradiction.
- Only mark CONTRADICTED if a refutation result explicitly negates the claim.
- Only mark CONTESTED if BOTH sides have credible evidence — not if one side is weak or irrelevant.
- Philosophical, ethical, subjective, or opinion-based claims must always be UNVERIFIABLE.
- Predictive or causal claims requiring inference must be UNVERIFIABLE.

Return ONLY a valid JSON object (no markdown fences, no extra text):
{
  "claim": "<the original claim text, verbatim>",
  "status": "VERIFIED" | "CONTRADICTED" | "CONTESTED" | "UNVERIFIABLE",
  "source": "<URL from search results, or 'No source found'>",
  "delta": "<if CONTRADICTED: one-sentence discrepancy. If CONTESTED: one sentence summarizing the debate. Otherwise: empty string>"
}"""


def extract_verifiable_claims(
    top2_results: List[Dict[str, Any]],
    confidence_threshold: int = 75,
    max_claims: int = 4,
) -> List[Dict[str, Any]]:
    """
    Pull high-confidence verifiable facts from the top-2 ranked models.

    Priority order:
      1. `factual_claims` — specific, concrete facts the model explicitly asserts
         (primary verification target)
      2. `key_assumptions` — framing premises, fallback for old conversations
         that predate the factual_claims field

    Only targets claims where confidence > threshold (default 75).
    Caps at max_claims to bound API costs.
    """
    claims = []
    for result in top2_results:
        confidence = result.get("confidence") or 0
        if confidence <= confidence_threshold:
            continue

        base = {
            "model": result["model"],
            "confidence": confidence,
            "confidence_source": result.get("confidence_source"),
        }

        # Primary: factual_claims (specific, falsifiable facts)
        for fact in result.get("factual_claims") or []:
            if fact and fact.strip():
                claims.append({**base, "claim": fact.strip(), "claim_source": "factual_claims"})

        # Fallback: key_assumptions (for older conversations without factual_claims)
        if not result.get("factual_claims"):
            for assumption in result.get("key_assumptions") or []:
                if assumption and assumption.strip():
                    claims.append({**base, "claim": assumption.strip(), "claim_source": "key_assumptions"})

    return claims[:max_claims]


async def _generate_search_queries(claim: str, user_query: str) -> Tuple[str, str]:
    """
    Generate both a corroboration and refutation search query for a claim in a single LLM call.

    Returns (corroboration_query, refutation_query).
    Falls back to simple defaults if the LLM call fails.
    """
    prompt = (
        "For the factual claim below, generate two targeted web search queries (max 10 words each):\n"
        "1. A corroboration query that finds supporting evidence for the claim.\n"
        "2. A refutation query that finds counter-evidence, critiques, or alternative explanations.\n\n"
        "Return ONLY a JSON object — no markdown, no extra text:\n"
        "{\"corroboration\": \"...\", \"refutation\": \"...\"}\n\n"
        f"Original question context: {user_query}\n"
        f"Claim: {claim}"
    )
    response = await query_model(
        VERIFIER_MODEL,
        [{"role": "user", "content": prompt}],
        timeout=20.0,
    )
    if response is None:
        print(f"[stage25] query generation failed for claim '{claim[:60]}' — using default fallback queries")
        return claim, f"evidence against {claim}"

    raw = response.get("content", "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = _json.loads(raw[start:end + 1])
            corr = parsed.get("corroboration", claim).strip().strip("\"'")
            refu = parsed.get("refutation", f"evidence against {claim}").strip().strip("\"'")
            return corr, refu
        except (_json.JSONDecodeError, ValueError):
            print(f"[stage25] query generation returned unparseable output for claim '{claim[:60]}' — using default fallback queries")

    return claim, f"evidence against {claim}"


async def _search_tavily(query: str) -> Dict[str, Any]:
    """
    Execute a Tavily web search and return the raw response dict.

    Returns {"results": [], "error": "..."} on misconfiguration or failure
    so callers don't need to handle exceptions.
    """
    if not TAVILY_API_KEY:
        print("[stage25] Tavily search skipped — TAVILY_API_KEY not set in environment")
        return {"results": [], "error": "TAVILY_API_KEY not set in .env"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": 3,
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("results") and not data.get("answer"):
                print(f"[stage25] Tavily returned empty results for query '{query}' — quota may be exhausted")
            return data
    except Exception as e:
        print(f"[stage25] Tavily search failed for query '{query}': {type(e).__name__}: {e}")
        return {"results": [], "error": str(e)}


def _build_search_context(search_data: Dict[str, Any], label: str) -> str:
    """Format one set of search results into a compact context block for the verifier LLM."""
    results = search_data.get("results", [])
    direct_answer = search_data.get("answer", "")
    lines = []
    if direct_answer:
        lines.append(f"Direct answer: {direct_answer}")
    for r in results[:3]:
        lines.append(f"URL: {r.get('url', 'unknown')}")
        lines.append(f"Excerpt: {r.get('content', '')[:400]}")
    body = "\n".join(lines) if lines else "(no results)"
    return f"--- {label} ---\n{body}"


async def _validate_claim(
    claim_obj: Dict[str, Any],
    corroboration_data: Dict[str, Any],
    refutation_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Ask the verifier LLM to evaluate a single claim against both corroboration
    and refutation search result sets.

    Degrades gracefully to UNVERIFIABLE on empty results or LLM failure.
    """
    corr_has_data = bool(corroboration_data.get("results") or corroboration_data.get("answer"))
    refu_has_data = bool(refutation_data.get("results") or refutation_data.get("answer"))

    if not corr_has_data and not refu_has_data:
        return {
            "claim": claim_obj["claim"],
            "status": "UNVERIFIABLE",
            "source": "No search results returned",
            "delta": "",
            "model": claim_obj["model"],
            "original_confidence": claim_obj["confidence"],
        }

    corr_context = _build_search_context(corroboration_data, "CORROBORATION SEARCH")
    refu_context = _build_search_context(refutation_data, "REFUTATION SEARCH")

    prompt = (
        f"Claim to verify: {claim_obj['claim']}\n\n"
        f"{corr_context}\n\n"
        f"{refu_context}"
    )
    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = await query_model(VERIFIER_MODEL, messages, timeout=30.0)
    if response is None:
        return {
            "claim": claim_obj["claim"],
            "status": "UNVERIFIABLE",
            "source": "Verifier model unavailable",
            "delta": "",
            "model": claim_obj["model"],
            "original_confidence": claim_obj["confidence"],
        }

    raw = response.get("content", "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start:end + 1]

    try:
        parsed = _json.loads(raw)
        return {
            "claim": parsed.get("claim", claim_obj["claim"]),
            "status": parsed.get("status", "UNVERIFIABLE"),
            "source": parsed.get("source", "No source found"),
            "delta": parsed.get("delta", ""),
            "model": claim_obj["model"],
            "original_confidence": claim_obj["confidence"],
        }
    except (_json.JSONDecodeError, ValueError):
        return {
            "claim": claim_obj["claim"],
            "status": "UNVERIFIABLE",
            "source": "Verifier returned unparseable output",
            "delta": "",
            "model": claim_obj["model"],
            "original_confidence": claim_obj["confidence"],
        }


def format_verification_context(verification_results: List[Dict[str, Any]]) -> str:
    """
    Format Stage 2.5 validation blocks as injected context for the Chairman.

    Results are grouped by source model so the Chairman can assess per-model
    credibility — a model with 2 CONTRADICTED claims should be weighted lower
    than one with 2 VERIFIED claims.

    Design:
    - CONTRADICTED: strong [!] flag with correction instruction
    - CONTESTED:    [~] unresolved debate signal — Chairman must report controversy
    - VERIFIED:     [✓] settled fact signal
    - UNVERIFIABLE: excluded (noise reduction)
    """
    if not verification_results:
        return ""

    # Group by source model
    grouped: Dict[str, list] = defaultdict(list)
    for r in verification_results:
        grouped[r.get("model", "unknown")].append(r)

    model_sections = []
    for model, results in grouped.items():
        counts: Dict[str, int] = defaultdict(int)
        for r in results:
            counts[r.get("status", "UNVERIFIABLE")] += 1

        summary_parts = []
        if counts["VERIFIED"]:
            summary_parts.append(f"{counts['VERIFIED']} Verified")
        if counts["CONTRADICTED"]:
            summary_parts.append(f"{counts['CONTRADICTED']} Contradicted")
        if counts["CONTESTED"]:
            summary_parts.append(f"{counts['CONTESTED']} Contested")

        # Skip model section if it has only UNVERIFIABLE results
        if not summary_parts:
            continue

        lines = [f"[{model}]: {', '.join(summary_parts)}"]
        for r in results:
            status = r.get("status")
            claim = r.get("claim", "")
            if status == "CONTRADICTED":
                lines.append(
                    f"  [!] CONTRADICTED: \"{claim}\" — "
                    f"External found: '{r.get('delta')}' ({r.get('source')}). "
                    "Prioritize this external evidence unless the search result is clearly erroneous."
                )
            elif status == "CONTESTED":
                lines.append(
                    f"  [~] CONTESTED: \"{claim}\" — "
                    f"{r.get('delta', 'Sources disagree.')} ({r.get('source')}). "
                    "Treat as an open debate; describe both positions rather than resolving it."
                )
            elif status == "VERIFIED":
                lines.append(
                    f"  [✓] VERIFIED: \"{claim}\" confirmed by {r.get('source')}. "
                    "You may treat this as a settled fact."
                )

        model_sections.append("\n".join(lines))

    if not model_sections:
        return ""

    return (
        "### EXTERNAL FACT-CHECK RESULTS (grouped by source model)\n"
        "Use these to validate claims and assess per-model credibility:\n"
        + "\n\n".join(model_sections)
        + "\n"
    )


async def stage25_verify_claims(
    top2_results: List[Dict[str, Any]],
    user_query: str,
) -> List[Dict[str, Any]]:
    """
    Stage 2.5 orchestrator: extract → generate query pairs → search both → validate.

    Per claim: generates a (corroboration, refutation) query pair in one LLM call,
    then runs both Tavily searches in parallel before LLM validation.

    All steps across all claims are fully parallelized via asyncio.gather.
    Corroboration and refutation searches are interleaved as
    [corr0, refu0, corr1, refu1, ...] for a single gather call.

    Returns empty list if Stage 2.5 is disabled, no claims meet the confidence
    threshold, or search is not configured.
    """
    print(f"[stage25] verify_claims called — STAGE25_ENABLED={STAGE25_ENABLED}, models={[r['model'] for r in top2_results]}")
    if not STAGE25_ENABLED:
        return []

    claims = extract_verifiable_claims(top2_results)
    print(f"[stage25] extracted {len(claims)} claims (confidence_threshold=75)")
    if not claims:
        return []

    # Step 1: Generate (corroboration, refutation) query pairs for all claims in parallel
    query_pairs = await asyncio.gather(
        *[_generate_search_queries(c["claim"], user_query) for c in claims]
    )

    # Step 2: Execute all searches in parallel — interleaved [corr0, refu0, corr1, refu1, ...]
    search_tasks = []
    for corr_q, refu_q in query_pairs:
        search_tasks.append(_search_tavily(corr_q))
        search_tasks.append(_search_tavily(refu_q))
    flat_results = await asyncio.gather(*search_tasks)

    # Unzip: even indices = corroboration, odd indices = refutation
    corr_results = flat_results[0::2]
    refu_results = flat_results[1::2]

    # Step 3: Validate each claim against both result sets in parallel
    validations = await asyncio.gather(
        *[
            _validate_claim(c, cr, rr)
            for c, cr, rr in zip(claims, corr_results, refu_results)
        ]
    )

    return list(validations)
