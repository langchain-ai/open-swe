import type { Provider } from "@types";

/**
 * Lightweight, defensive validation for API keys pasted by the user.
 *
 * The intent is NOT to enforce the exact format of every provider's keys —
 * those formats change over time and we don't want to lock users out of
 * legitimate new prefixes. We only catch the obvious "you pasted half of
 * your key" / "you pasted the wrong thing" cases that would otherwise
 * surface as a confusing 401 from the upstream API.
 *
 * Returns `null` on success, or a short human-readable reason on failure.
 */
export function validateApiKey(provider: Provider, key: string): string | null {
  if (!key) return "API key cannot be empty.";

  // Reject anything with whitespace or control characters — pasted keys
  // sometimes pick up bracketed-paste markers, focus-event sequences, or
  // a stray newline that the input layer failed to scrub.
  if (/[\s\u0000-\u001f\u007f]/.test(key)) {
    return "API key contains whitespace or control characters — please re-copy and paste again.";
  }
  // Bracketed-paste leftover: a `[200~`/`[201~` prefix means our paste
  // pipeline failed to scrub it. Surface a clear message instead of
  // shipping the corrupted key to the API.
  if (key.startsWith("[200~") || key.endsWith("[201~")) {
    return "API key looks like it includes a bracketed-paste marker. Re-paste the key.";
  }

  switch (provider) {
    case "openai":
      // OpenAI key prefixes seen in the wild: sk-, sk-proj-, sk-svcacct-,
      // sk-admin-, sk-org-. All start with "sk-".
      if (!key.startsWith("sk-")) {
        return 'OpenAI API keys start with "sk-". The pasted value does not — re-copy from https://platform.openai.com/api-keys.';
      }
      if (key.length < 40) {
        return "OpenAI API key looks too short — likely truncated during paste.";
      }
      return null;
    case "anthropic":
      if (!key.startsWith("sk-ant-")) {
        return 'Anthropic API keys start with "sk-ant-". The pasted value does not.';
      }
      if (key.length < 40) {
        return "Anthropic API key looks too short — likely truncated during paste.";
      }
      return null;
    case "google":
      // Google AI Studio keys typically start with "AIza" and are ~39 chars.
      if (!key.startsWith("AIza")) {
        return 'Google AI Studio API keys start with "AIza". The pasted value does not.';
      }
      if (key.length < 30) {
        return "Google API key looks too short — likely truncated during paste.";
      }
      return null;
    default:
      return null;
  }
}
