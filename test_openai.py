import os
from dotenv import load_dotenv

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("FAIL: OPENAI_API_KEY not found in .env")
    raise SystemExit(1)

print(f"Key found: {OPENAI_API_KEY[:8]}...{OPENAI_API_KEY[-4:]}")

import openai
client = openai.OpenAI(api_key=OPENAI_API_KEY)

try:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Say hello in one word."}],
        max_tokens=10,
    )
    print(f"SUCCESS: {response.choices[0].message.content.strip()}")
    print(f"Model used: {response.model}")
    print(f"Tokens used: {response.usage.total_tokens}")
except Exception as exc:
    print(f"FAIL: {type(exc).__name__}: {exc}")
