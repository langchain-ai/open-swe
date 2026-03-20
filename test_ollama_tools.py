from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:11434/v1",
    api_key="ollama",
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            }
        }
    }
]

resp = client.chat.completions.create(
    model="qwen2.5-coder:14b",
    messages=[
        {"role": "user", "content": "Read /etc/hostname using the tool."}
    ],
    tools=tools,
    tool_choice="auto",
)

print(resp.choices[0].message)
