/**
 * Returns the short model name from an OpenRouter model identifier.
 * e.g. "openai/gpt-4o" -> "gpt-4o"
 * Falls back to the full string if no "/" is present.
 * @param {string} model
 * @returns {string}
 */
export function getModelShortName(model) {
  return model.split('/')[1] || model;
}
