export async function generateThreadTitleFromText(
  text: string,
  openaiApiKey?: string,
): Promise<string | null> {
  const cleaned = text.trim();
  if (!cleaned) return null;

  // Fallback: first sentence or first 8 words
  const fallback = (() => {
    const sentence = cleaned.split(/(?<=[.!?])\s+/)[0] || cleaned;
    const words = sentence.split(/\s+/).slice(0, 10).join(" ");
    return words.substring(0, 80);
  })();

  if (!openaiApiKey) return fallback;

  try {
    const res = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${openaiApiKey}`,
      },
      body: JSON.stringify({
        model: "gpt-4.1-mini",
        messages: [
          {
            role: "system",
            content:
              "You create concise, informative, and specific thread titles based on a user's request. 6-10 words. No quotes or code fences. No trailing punctuation.",
          },
          {
            role: "user",
            content: `Create a short title for this request.\n\n${cleaned}`,
          },
        ],
        temperature: 0.2,
        max_tokens: 24,
      }),
    });
    if (!res.ok) return fallback;
    const data = await res.json();
    const title: string | undefined =
      data?.choices?.[0]?.message?.content?.trim();
    if (!title) return fallback;
    return title.replace(/^["'`\s]+|["'`\s]+$/g, "");
  } catch {
    return fallback;
  }
}
