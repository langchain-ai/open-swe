from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1",
    api_key="ollama",
)

resp = client.chat.completions.create(
    model="qwen2.5-coder:14b",
    messages=[
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Write a Python function that returns the factorial of n."},
    ],
    temperature=0.2,
    max_tokens=200,
)

print(resp.choices[0].message.content)
