"""Unified Claude + Gemini chat client with tool calling.

Both providers expose a `chat(messages, tools, system)` method that returns a
dict with either `{text: str}` (final answer) or `{tool_calls: [...]}` (asking
us to execute tools). The higher-level `run_chat` function implements the
tool-use loop: keep calling the model, executing tool calls, and feeding
results back until the model produces a final text answer.

Supported models (defaults):
  * Anthropic Claude: claude-haiku-4-5     ($0.25 / $1.25 per M tokens)
  * Google Gemini   : gemini-2.5-flash     (~free tier, ~$0.10 / $0.40)

API keys read from environment:
  * ANTHROPIC_API_KEY
  * GOOGLE_API_KEY  (or GEMINI_API_KEY)

If no key is available, the module returns a clear error so the UI can prompt
the user to set one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ui.tools import (
    TOOL_SCHEMAS_ANTHROPIC, TOOL_SCHEMAS_GEMINI, TOOL_SCHEMAS_OPENAI, dispatch,
)


DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GITHUB_MODEL = "openai/gpt-4o-mini"
GITHUB_MODELS_BASE_URL = "https://models.github.ai/inference"

# Curated list of GitHub Models that reliably support function calling on the
# Low-tier rate limit (15 RPM / 150 RPD on Copilot Free). Add/remove as you
# experiment — full catalog at https://github.com/marketplace/models
GITHUB_MODEL_CHOICES = [
    "openai/gpt-4o-mini",         # default — fast + cheap rate limits
    "openai/gpt-4o",              # best quality (High tier: 10 RPM / 50 RPD)
    "openai/gpt-4.1",             # latest flagship (High tier)
    "openai/gpt-4.1-mini",        # mid-size GPT-4.1 (Low tier)
    "meta/Llama-3.3-70B-Instruct",  # open-weights flagship
    "mistral-ai/Mistral-Large-2411",
]

MAX_TOOL_ITERATIONS = 6


SYSTEM_PROMPT = """You are PSX Advisor, the chat assistant for a rules-based
Pakistan Stock Exchange trading system (Plan D, Phase 1: monthly momentum
rotation with a defensive overlay).

Your job is to help the user reason about their PSX trades. You MUST follow
these rules at all times:

1. **Never make up numbers.** Every price, momentum score, stop level, or
   portfolio value must come from a tool call. If a tool is not available for
   what the user asked, say so honestly.
2. **Use tools proactively.** For any question involving concrete numbers,
   symbols, or portfolio state, call the relevant tool first and THEN answer
   from the returned data.
3. **Be specific.** Quote prices to 2 decimals in PKR, percentages to 1-2
   decimals. Reference dates (as-of field) so the user knows how fresh the
   data is.
4. **Stay within the universe.** The system only trades 15 PSX blue chips.
   `list_universe` returns the full set. If a user asks about a symbol outside
   this universe, explain that the bot has no data for it.
5. **Don't give legal or investment advice.** The user is running paper trades
   and making their own decisions. Phrase recommendations as "the Phase 1 rule
   suggests X because Y" or "a trailing stop at Z PKR would limit downside
   to W%", not as commandments.
6. **Structure your BUY/SELL/HOLD decisions.** When the user asks about a
   specific position, end with a clearly-labelled ACTION line, e.g.:
       ACTION: HOLD — reason in one sentence
       SUGGESTED STOP: 212.50 PKR (12% below peak since entry)
7. **Respect the strategy.** You are not building a new strategy on the fly.
   You interpret the outputs of the Phase 1 rule. If the mechanical rule says
   CASH and the user wants to buy anyway, flag the disagreement but don't
   pretend the rule agrees.
8. **Brevity.** Answer in 4-10 lines unless the user explicitly asks for
   detail. No filler, no disclaimers every answer.

Tools available: see the tool list. Prefer the specialised tools:
- `analyze_position` for any "I bought X at Y" question (price + Phase 1
  signal + stop-loss math in one call).
- `get_todays_predictions` for "what should I buy today" — it returns
  all 15 predictions with gross, round-trip cost (~0.56%), net return
  after costs+CGT, and a `clears_cost_threshold` flag. Prefer filtering
  with `only_actionable=true` for concrete buy lists.
- `get_overnight_signals` for "what will the market do today" or any
  morning-gap / global-risk question — returns S&P/VIX/Nikkei/HSI plus
  the data-fitted PSX gap prior and 24h macro news tilt.
- `get_scored_sentiment` for any news-or-sentiment question. It returns a
  quantified tilt in [-1, +1] weighted by confidence and recency, plus the
  top 5 scored headlines. Prefer it over `get_recent_news` when the user
  asks "is news positive/negative" — raw headlines lie, scored numbers
  don't.
- `estimate_trade_net_return` whenever the user asks "is a 2% move worth
  it" or compares expected returns — ALWAYS apply costs before answering.

Cost awareness: every trade round-trip is ~0.56% + 15% CGT on gains.
A BUY/ADD only makes sense if expected gross 5d return >= ~1.6%. When
a pick looks bullish but falls below that bar, say HOLD, not BUY."""


# ==========================================================================
# Claude (Anthropic)
# ==========================================================================
class ClaudeClient:
    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL,
                 api_key: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _ensure(self):
        if self._client is not None:
            return
        if not self.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Provide it in the sidebar or via "
                "the environment variable.")
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise RuntimeError(f"anthropic package not installed: {e}")
        self._client = Anthropic(api_key=self.api_key)

    def run_chat(self, history: list[dict], system: str = SYSTEM_PROMPT,
                 max_tokens: int = 1024) -> dict:
        """Run one user turn through the Claude tool-use loop.

        `history` is a list of {role, content} dicts in Claude format.
        Returns {"text": str, "trace": list_of_tool_calls}.
        """
        self._ensure()
        messages = list(history)
        trace: list[dict] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                tools=TOOL_SCHEMAS_ANTHROPIC,
                messages=messages,
            )

            if resp.stop_reason != "tool_use":
                text = "".join(
                    getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", "") == "text"
                ).strip()
                return {"text": text, "trace": trace}

            # Collect all tool_use blocks and dispatch them
            assistant_blocks = [dict(b.model_dump() if hasattr(b, "model_dump")
                                     else b) for b in resp.content]
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_results = []
            for b in resp.content:
                if getattr(b, "type", "") != "tool_use":
                    continue
                name = b.name
                args = dict(b.input) if isinstance(b.input, dict) else {}
                result = dispatch(name, args)
                trace.append({"tool": name, "args": args, "result": result})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": tool_results})

        return {"text": "(too many tool iterations — stopping)", "trace": trace}


# ==========================================================================
# Gemini (Google) — uses the new google-genai SDK (>=1.0)
# ==========================================================================
class GeminiClient:
    def __init__(self, model: str = DEFAULT_GEMINI_MODEL,
                 api_key: Optional[str] = None):
        self.model = model
        self.api_key = (api_key or os.environ.get("GOOGLE_API_KEY")
                        or os.environ.get("GEMINI_API_KEY"))
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _ensure(self):
        if self._client is not None:
            return
        if not self.api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY) not set. Provide it in "
                "the sidebar or via the environment variable.")
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError(f"google-genai package not installed: {e}")
        self._client = genai.Client(api_key=self.api_key)

    def run_chat(self, history: list[dict], system: str = SYSTEM_PROMPT,
                 max_tokens: int = 1024) -> dict:
        """Gemini tool-use loop using the new google-genai SDK.

        `history` is a list of {role, content} dicts where role is 'user' or
        'assistant'. We translate to Gemini's native format.
        """
        self._ensure()
        from google.genai import types as gtypes

        # Build generation config with system prompt + tools
        config = gtypes.GenerateContentConfig(
            system_instruction=system,
            tools=[gtypes.Tool(function_declarations=TOOL_SCHEMAS_GEMINI)],
            temperature=0.2,
            max_output_tokens=max_tokens,
        )

        # Split history into prior turns (for chat creation) + current user msg
        prior = history[:-1] if history else []
        current = history[-1] if history else None
        if current is None or current.get("role") != "user":
            return {"text": "(internal: last history turn must be user)",
                    "trace": []}

        # Convert prior turns to google-genai Content objects (text-only)
        gemini_history: list[gtypes.Content] = []
        for h in prior:
            role = "user" if h["role"] == "user" else "model"
            content = h["content"]
            if isinstance(content, list):
                parts_text = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts_text.append(c.get("text", ""))
                    elif isinstance(c, str):
                        parts_text.append(c)
                content = "\n".join(parts_text)
            if not content:
                continue
            gemini_history.append(
                gtypes.Content(role=role,
                               parts=[gtypes.Part.from_text(text=str(content))])
            )

        chat = self._client.chats.create(
            model=self.model, config=config, history=gemini_history,
        )
        trace: list[dict] = []

        user_msg = current["content"]
        if isinstance(user_msg, list):
            user_msg = "\n".join(c.get("text", "") for c in user_msg
                                 if isinstance(c, dict) and c.get("type") == "text")
        resp = chat.send_message(str(user_msg))

        for _ in range(MAX_TOOL_ITERATIONS):
            fcs = _gemini_extract_function_calls(resp)
            if not fcs:
                text = _gemini_extract_text(resp)
                return {"text": text.strip(), "trace": trace}

            fn_response_parts = []
            for fc in fcs:
                args = dict(fc.args) if fc.args else {}
                result = dispatch(fc.name, args)
                trace.append({"tool": fc.name, "args": args, "result": result})
                fn_response_parts.append(
                    gtypes.Part.from_function_response(
                        name=fc.name, response={"result": result}
                    )
                )
            resp = chat.send_message(fn_response_parts)

        return {"text": "(too many tool iterations — stopping)", "trace": trace}


def _gemini_extract_function_calls(response) -> list:
    """Gemini puts function calls in response.candidates[0].content.parts."""
    out = []
    try:
        for cand in response.candidates or []:
            for part in (cand.content.parts or []):
                fc = getattr(part, "function_call", None)
                if fc is not None and getattr(fc, "name", None):
                    out.append(fc)
    except (AttributeError, IndexError, TypeError):
        pass
    return out


def _gemini_extract_text(response) -> str:
    try:
        t = response.text
        if t:
            return t
    except (AttributeError, ValueError):
        pass
    parts = []
    try:
        for cand in response.candidates or []:
            for part in (cand.content.parts or []):
                if getattr(part, "text", None):
                    parts.append(part.text)
    except (AttributeError, IndexError, TypeError):
        pass
    return "".join(parts)


# ==========================================================================
# GitHub Models (free tier — OpenAI-compatible API)
# Endpoint:  https://models.github.ai/inference/chat/completions
# Auth:      Authorization: Bearer <GITHUB_TOKEN>   (needs models:read scope)
# Format:    standard OpenAI Chat Completions + function calling
# Rate:      Copilot Free — Low tier: 15 RPM / 150 RPD
#                           High tier (gpt-4o, gpt-4.1): 10 RPM / 50 RPD
# ==========================================================================
class GithubModelsClient:
    def __init__(self, model: str = DEFAULT_GITHUB_MODEL,
                 api_key: Optional[str] = None):
        self.model = model
        # GITHUB_TOKEN is standard in CI; GH_TOKEN is what `gh` CLI sets.
        self.api_key = (api_key
                        or os.environ.get("GITHUB_TOKEN")
                        or os.environ.get("GH_TOKEN"))
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _ensure(self):
        if self._client is not None:
            return
        if not self.api_key:
            raise RuntimeError(
                "GITHUB_TOKEN not set. Create a fine-grained PAT with the "
                "'models:read' scope at https://github.com/settings/tokens "
                "and add it to the sidebar or your .env.")
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError(
                f"openai package not installed: {e}. "
                f"Install with: pip install openai")
        self._client = OpenAI(
            base_url=GITHUB_MODELS_BASE_URL,
            api_key=self.api_key,
            default_headers={"X-GitHub-Api-Version": "2022-11-28"},
        )

    def run_chat(self, history: list[dict], system: str = SYSTEM_PROMPT,
                 max_tokens: int = 1024) -> dict:
        """Run one user turn through the OpenAI-format tool-use loop.

        `history` is the provider-agnostic [{role, content}] list. We translate
        into OpenAI chat format with a system message prepended.
        """
        self._ensure()

        messages: list[dict] = [{"role": "system", "content": system}]
        for h in history:
            role = h.get("role", "user")
            content = h.get("content", "")
            # Our internal history uses flat strings; flatten list-of-blocks if
            # we ever see one (from a cross-provider switch mid-conversation).
            if isinstance(content, list):
                content = "\n".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            messages.append({"role": role, "content": str(content)})

        trace: list[dict] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS_OPENAI,
                tool_choice="auto",
                max_tokens=max_tokens,
                temperature=0.2,
            )
            choice = resp.choices[0]
            msg = choice.message
            tool_calls = getattr(msg, "tool_calls", None) or []

            # Final answer — no more tool calls needed.
            if choice.finish_reason != "tool_calls" or not tool_calls:
                text = (msg.content or "").strip()
                return {"text": text, "trace": trace}

            # Append the assistant message WITH its tool_calls so the model
            # sees its own request on the next turn. Per OpenAI spec the
            # content field can be null when tool_calls are present.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # Execute each requested tool and append tool-role messages.
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = dispatch(name, args)
                trace.append({"tool": name, "args": args, "result": result})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)[:8000],
                })

        return {"text": "(too many tool iterations — stopping)", "trace": trace}


# ==========================================================================
# Factory + unified entrypoint
# ==========================================================================
def get_client(provider: str, api_key: Optional[str] = None,
               model: Optional[str] = None):
    p = (provider or "").lower().replace("-", "_")
    if p in ("claude", "anthropic"):
        return ClaudeClient(model=model or DEFAULT_CLAUDE_MODEL, api_key=api_key)
    if p in ("gemini", "google"):
        return GeminiClient(model=model or DEFAULT_GEMINI_MODEL, api_key=api_key)
    if p in ("github", "github_models", "githubmodels"):
        return GithubModelsClient(model=model or DEFAULT_GITHUB_MODEL,
                                  api_key=api_key)
    raise ValueError(
        f"Unknown provider {provider!r}; "
        f"expected 'claude', 'gemini', or 'github'."
    )


if __name__ == "__main__":
    from rich import print
    for prov in ("claude", "gemini", "github"):
        c = get_client(prov)
        print(f"{prov} available: {c.available}")
        if not c.available:
            continue
        r = c.run_chat([{"role": "user", "content": "What is MCB trading at today?"}])
        print(r["text"])
        print(f"trace: {len(r['trace'])} tool calls")
