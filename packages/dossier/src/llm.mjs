// Minimal OpenAI-compatible chat call. No SDK: openai, deepseek, ollama, vllm all
// speak this shape, and one less dependency is one less thing to break.

export async function chat({ llm = {}, system, user, maxTokens }) {
  const envName = llm.key_env || 'OPENAI_API_KEY';
  const key = process.env[envName];
  if (!key) throw new Error(`缺少 API key：请先 export ${envName}=...（或改用本地 OpenAI 兼容端点并设假 key）`);

  const base = String(llm.base_url || 'https://api.openai.com').replace(/\/+$/, '');
  const body = {
    model: llm.model || 'gpt-4o-mini',
    temperature: 0,
    max_tokens: maxTokens ?? llm.max_output_tokens ?? 2000,
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
  };

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), llm.timeout_ms ?? 90000);
  try {
    const res = await fetch(`${base}/chat/completions`, {
      method: 'POST',
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`LLM ${res.status}: ${text.slice(0, 300)}`);
    }
    const json = await res.json();
    return {
      text: json.choices?.[0]?.message?.content ?? '',
      usage: json.usage ?? {},
    };
  } finally {
    clearTimeout(timer);
  }
}
