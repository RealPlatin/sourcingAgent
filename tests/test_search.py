"""Temporary diagnostic script — V5.6 search test.

Calls Perplexity with the first 3 DACH archetypes for 'Wassertechnik'
and prints the RAW response BEFORE any filtering or JSON extraction.
Run: python test_search.py
"""
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")

CONFIG_FILE = "config.json"
with open(CONFIG_FILE) as f:
    config = json.load(f)

ARCHETYPES = config["prompts"]["DACH"]["discovery"]["search_archetypes"]
SYSTEM = config["prompts"]["DACH"]["discovery"]["system"]
USER_TEMPLATE = config["prompts"]["DACH"]["discovery"]["user_template"]

TEST_INDUSTRY = "Wassertechnik"
TEST_ARCHETYPES = ARCHETYPES[:3]  # first 3 only


def raw_perplexity_call(system_prompt: str, user_prompt: str) -> str | None:
    url = "https://api.perplexity.ai/chat/completions"
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def render(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


if __name__ == "__main__":
    print("=" * 60)
    print(f"  PERPLEXITY RAW SEARCH TEST — industry: '{TEST_INDUSTRY}'")
    print("=" * 60)

    for i, archetype_tpl in enumerate(TEST_ARCHETYPES, 1):
        archetype = render(archetype_tpl, industry=TEST_INDUSTRY, region="DACH")
        user_prompt = render(
            USER_TEMPLATE,
            archetype=archetype,
            discovery_count="5",
            discovery_extra="",
            region="DACH",
        )

        print(f"\n--- Archetype {i}: '{archetype}' ---")
        raw = raw_perplexity_call(SYSTEM, user_prompt)
        if raw is None:
            print("  !! No response (API error)")
        else:
            print(f"  RAW RESPONSE ({len(raw)} chars):")
            print(raw[:2000])
            print("  ..." if len(raw) > 2000 else "")

    print("\n" + "=" * 60)
    print("  TEST COMPLETE")
    print("=" * 60)
