"""Quick health check for Anthropic API. Exits 0 if alive, 1 if exhausted/down."""
import os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()

try:
    from anthropic import Anthropic
except ImportError:
    print("[anthropic] SDK not installed")
    sys.exit(2)

key = os.environ.get("ANTHROPIC_API_KEY")
if not key:
    print("[anthropic] ANTHROPIC_API_KEY missing")
    sys.exit(1)

try:
    client = Anthropic(api_key=key)
    # Minimal token use: 1 input token, 1 output token max
    resp = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4,
        messages=[{"role": "user", "content": "hi"}],
    )
    print(f"[anthropic] OK — returned {resp.usage.output_tokens} tokens, model={resp.model}")
    sys.exit(0)
except Exception as e:
    err = str(e)
    print(f"[anthropic] FAILED: {err[:300]}")
    if "credit" in err.lower() or "billing" in err.lower() or "exceeded" in err.lower():
        print("[anthropic] DIAGNOSIS: credits exhausted")
    elif "auth" in err.lower() or "401" in err:
        print("[anthropic] DIAGNOSIS: auth failure")
    elif "rate" in err.lower() or "429" in err:
        print("[anthropic] DIAGNOSIS: rate limited")
    sys.exit(1)
