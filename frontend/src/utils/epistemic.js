/**
 * Measures content agreement across council models.
 * Primary signal: overlap ratio of factual_claims across models.
 * Fallback (no claims): spread of confidence scores.
 *
 * @param {Array<{factual_claims: string[], confidence: number|null}>} stage1
 * @returns {"Unanimous"|"Majority"|"Split"|"Unknown"}
 */
export function calcPeerAlignment(stage1) {
  if (!stage1 || stage1.length < 2) return 'Unknown';

  function normalize(str) {
    return str.toLowerCase().replace(/[^\w\s]/g, '').trim();
  }

  const allClaims = stage1.map((r) =>
    Array.isArray(r.factual_claims) ? r.factual_claims.map(normalize) : []
  );

  const flat = allClaims.flat();
  const unique = [...new Set(flat)];

  if (unique.length > 0) {
    // Count claims shared by ≥ 2 models
    const overlapping = unique.filter(
      (claim) => allClaims.filter((list) => list.includes(claim)).length >= 2
    );
    const overlapRatio = overlapping.length / unique.length;
    if (overlapRatio >= 0.5) return 'Unanimous';
    if (overlapRatio >= 0.2) return 'Majority';
    return 'Split';
  }

  // Fallback: confidence convergence when no claims present
  const confidences = stage1.map((r) => r.confidence).filter((v) => v != null);
  if (confidences.length === 0) return 'Unknown';
  const spread = Math.max(...confidences) - Math.min(...confidences);
  if (spread <= 15) return 'Unanimous';
  if (spread <= 30) return 'Majority';
  return 'Split';
}

/**
 * Calculates the overall reliability verdict using an Epistemic Waterfall:
 * Step 1 — External Evidence (Stage 2.5): CONTRADICTED → Disputed; all VERIFIED → Verified
 * Step 2 — Factual Consensus (Stage 1): confidence + claim overlap → Consensus/Split/Uncertain/Unknown
 *
 * @param {Array|null} stage25 - Stage 2.5 verification results
 * @param {Array|null} stage1 - Stage 1 model responses (must have .confidence and .factual_claims)
 * @returns {"Verified"|"Disputed"|"Consensus"|"Split"|"Uncertain"|"Unknown"}
 */
export function calcVerdict(stage25, stage1) {
  // Step 1 — External Evidence
  if (stage25 && stage25.length > 0) {
    if (stage25.some((r) => r.status === 'CONTRADICTED')) {
      return 'Disputed';
    }
    const actionable = stage25.filter((r) =>
      r.status === 'VERIFIED' || r.status === 'CONTRADICTED' || r.status === 'CONTESTED'
    );
    if (actionable.length > 0 && actionable.every((r) => r.status === 'VERIFIED')) {
      return 'Verified';
    }
    // CONTESTED or mixed → fall through to Step 2
  }

  // Step 2 — Factual Consensus
  if (!stage1 || stage1.length === 0) return 'Unknown';

  const confidences = stage1.map((r) => r.confidence).filter((v) => v != null);
  const avgConf = confidences.length > 0
    ? confidences.reduce((sum, v) => sum + v, 0) / confidences.length
    : null;

  if (avgConf !== null && avgConf < 50) return 'Uncertain';

  function normalize(str) {
    return str.toLowerCase().replace(/[^\w\s]/g, '').trim();
  }

  const allClaims = stage1.map((r) =>
    Array.isArray(r.factual_claims) ? r.factual_claims.map(normalize) : []
  );

  // If no model has any claims, use confidence only
  if (allClaims.every((list) => list.length === 0)) {
    return (avgConf !== null && avgConf >= 65) ? 'Consensus' : 'Split';
  }

  // Count claims that appear in ≥ 2 models' lists (exact normalized match)
  const flat = allClaims.flat();
  const unique = [...new Set(flat)];
  const overlapping = unique.filter(
    (claim) => allClaims.filter((list) => list.includes(claim)).length >= 2
  );
  const overlapRatio = overlapping.length / unique.length;

  if (overlapRatio >= 0.5 && avgConf !== null && avgConf >= 65) return 'Consensus';

  return (avgConf !== null && avgConf < 55) ? 'Uncertain' : 'Split';
}

/**
 * Returns the min/max confidence range from Stage 1 responses.
 * Filters out null confidence values. Returns null if nothing to show.
 *
 * @param {Array<{confidence: number|null}>} stage1
 * @returns {{min: number, max: number}|null}
 */
export function calcConfidenceRange(stage1) {
  if (!stage1) return null;
  const values = stage1.map((r) => r.confidence).filter((v) => v != null);
  if (values.length === 0) return null;
  return { min: Math.min(...values), max: Math.max(...values) };
}

/**
 * Returns the worst-ranked model only if it's a meaningful outlier
 * (gap vs second-worst > 0.5). Otherwise returns null.
 *
 * @param {Array<{model: string, average_rank: number}>} aggregateRankings
 * @returns {string|null} model_id or null
 */
export function getMinorityModel(aggregateRankings) {
  if (!aggregateRankings || aggregateRankings.length < 2) return null;
  const sorted = [...aggregateRankings].sort((a, b) => b.average_rank - a.average_rank);
  const worst = sorted[0];
  const secondWorst = sorted[1];
  if (worst.average_rank - secondWorst.average_rank > 0.5) return worst.model;
  return null;
}

/**
 * Groups Stage 2.5 results by verdict.
 *
 * @param {Array} stage25Results
 * @returns {{CONTRADICTED: Array, CONTESTED: Array, VERIFIED: Array, UNVERIFIABLE: Array}}
 */
export function groupClaimsByVerdict(stage25Results) {
  const groups = { CONTRADICTED: [], CONTESTED: [], VERIFIED: [], UNVERIFIABLE: [] };
  if (!stage25Results) return groups;
  for (const result of stage25Results) {
    if (groups[result.status]) groups[result.status].push(result);
  }
  return groups;
}

/**
 * Injects HTML <span> markers into the chairman's markdown text for VERIFIED,
 * CONTRADICTED, and CONTESTED claims. Uses end-to-start string surgery to
 * preserve all character offsets.
 *
 * Algorithm:
 * 1. Filter to actionable verdicts (VERIFIED, CONTRADICTED, CONTESTED)
 * 2. Sort by claim length descending (longer matches first → avoids partial overlap)
 * 3. Find first non-overlapping case-insensitive match per claim
 * 4. Sort replacements by start position descending (safe string surgery)
 * 5. Inject spans from end to start
 *
 * Graceful degradation: unmatched claims are visible only in the drawer.
 *
 * @param {string} responseText - Raw markdown from the chairman
 * @param {Array} stage25Results - Full Stage 2.5 results array
 * @returns {string} Modified markdown with HTML span tags injected
 */
export function annotateChairmanText(responseText, stage25Results) {
  if (!stage25Results || stage25Results.length === 0) return responseText;

  const actionable = stage25Results
    .map((r, idx) => ({ ...r, originalIdx: idx }))
    .filter((r) => ['VERIFIED', 'CONTRADICTED', 'CONTESTED'].includes(r.status));

  if (actionable.length === 0) return responseText;

  // Longer claims matched first to prevent a short claim from "stealing" part of a longer one
  actionable.sort((a, b) => b.claim.length - a.claim.length);

  const replacements = [];
  const used = [];
  const lower = responseText.toLowerCase();

  for (const item of actionable) {
    const claimLower = item.claim?.toLowerCase();
    if (!claimLower) continue;
    let pos = 0;
    let found = -1;

    while ((found = lower.indexOf(claimLower, pos)) !== -1) {
      const end = found + item.claim.length;
      const overlaps = used.some(([s, e]) => found < e && end > s);
      if (!overlaps) {
        replacements.push({
          start: found,
          end,
          claimIdx: item.originalIdx,
          status: item.status.toLowerCase(),
        });
        used.push([found, end]);
        break;
      }
      pos = found + 1;
    }
  }

  if (replacements.length === 0) return responseText;

  // End-to-start application preserves all offsets
  replacements.sort((a, b) => b.start - a.start);

  let result = responseText;
  for (const { start, end, claimIdx, status } of replacements) {
    const slice = result.slice(start, end);
    const span = `<span class="claim-annotation claim-annotation--${status}" data-claim-idx="${claimIdx}" role="button" tabindex="0">${slice}</span>`;
    result = result.slice(0, start) + span + result.slice(end);
  }

  return result;
}
