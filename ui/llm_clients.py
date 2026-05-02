"""Unified Claude + Gemini chat client with tool calling.

Both providers expose a ``run_chat(history, system, max_tokens)`` method that
returns ``{text, trace}``. The Claude client also accepts a ``thinking``
parameter so the master-strategist layer (``brain/master_strategist.py``) can
use Claude's extended-thinking mode — Claude is encouraged to spend a
configurable token budget on internal reasoning before producing the final
answer. That mode is what turns Claude from "narrow worker on a few
subtasks" into the **top-layer master mind** that ingests every signal the
bot has, reasons across them, and tells us what to do.

Model tiers (cost / capability)
-------------------------------

  * **Reasoning tier** (default for the master strategist + chatbot)
    ``claude-sonnet-4-5`` — flagship-tier reasoning at ~$3 / $15 per M
    tokens, supports extended thinking up to 64k token budget. This is
    the model the user told us to make the "good thinking model".

  * **Heavy-reasoning tier** (optional override)
    ``claude-opus-4-5`` — when the user explicitly wants the deepest
    analysis (e.g. the End-of-Quarter strategist run). 5-10x the cost
    of Sonnet, used sparingly.

  * **Utility tier** (small subtasks: news scoring, simple chat)
    ``claude-haiku-4-5`` — ~$0.25 / $1.25 per M tokens. Retained as a
    fallback / cheap-call provider.

Other providers:
  * Google Gemini : ``gemini-2.5-flash`` (~$0.10 / $0.40 per M tokens)
  * GitHub Models : ``openai/gpt-4o-mini`` (free tier with rate limits)

API keys read from environment:
  * ANTHROPIC_API_KEY
  * GOOGLE_API_KEY  (or GEMINI_API_KEY)
  * GITHUB_TOKEN    (or GH_TOKEN)

If no key is available, the module returns a clear error so the UI can
prompt the user to set one.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ui.tools import (
    TOOL_SCHEMAS_ANTHROPIC, TOOL_SCHEMAS_GEMINI, TOOL_SCHEMAS_OPENAI, dispatch,
)


# --- Claude tier defaults ---------------------------------------------------
# DEFAULT_CLAUDE_MODEL is the model the chatbot uses out-of-the-box. Promoted
# 2026-05-01 from claude-haiku-4-5 → claude-sonnet-4-5 so the analyst gets a
# real reasoning model on every chat answer (the user asked for the "good
# thinking model" by default, not Haiku).
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"

# Master-strategist defaults: the top-layer orchestrator in
# brain/master_strategist.py uses Sonnet 4.5 with a 12k-token thinking budget.
# Opus is available as a "deep dive" override.
MASTER_STRATEGIST_MODEL = "claude-sonnet-4-5"
MASTER_STRATEGIST_DEEP_MODEL = "claude-opus-4-5"
MASTER_STRATEGIST_THINKING_BUDGET = 12_000

# Cheap Haiku model — kept available for utility calls (news scoring,
# brain/overlay.py emergency-exit fallback, etc.) where reasoning depth
# does not justify the Sonnet cost premium.
HAIKU_MODEL = "claude-haiku-4-5"

# Curated list shown in the sidebar dropdown. Sonnet 4.5 sits at the top so
# clicking through is a one-liner upgrade; Opus is gated by the master
# strategist's "deep dive" toggle.
CLAUDE_MODEL_CHOICES = [
    "claude-sonnet-4-5",      # default — reasoning + extended thinking
    "claude-opus-4-5",        # heaviest reasoning, expensive
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-1-20250805",
    "claude-haiku-4-5",       # fast + cheap utility tier
]

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
4. **Stay within the universe.** The system only trades the curated set of
   PSX blue chips defined in `config/universe.py`. `list_universe` returns
   the full set. If a user asks about a symbol outside this universe,
   explain that the bot has no data for it.
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
- `get_watchlist` for any "what am I tracking" / "watchlist" / "near a
  target price" question. Returns live price, 1d/5d return, momentum rank,
  target upside, and alert-hit flags for every symbol the user is watching.
- `get_trade_journal` for "how have I been doing" / "past trades" / "show
  my win rate" questions. Returns realized gross and net P&L, win rate,
  best/worst trade, avg hold days, and the most recent closed trades.
- `get_value_signal` for "is X cheap/expensive", "what's the fair value
  of X", "is X overvalued", "intrinsic value", or any value-investing
  question on a SINGLE stock. Returns fair_value, upside %, BUY_VALUE /
  FAIR / SELL_VALUE / NO_SIGNAL with the formula breakdown. Always
  remind the user this is a SLOW signal (6-24 months) — do NOT use it
  to override a 5-day momentum/news call.
- `get_universe_value_book` for "what's cheap right now", "find me
  deep-value picks", "which of my holdings should I sell on valuation",
  "rank the universe by upside" — runs the model on all 15 and returns
  rows sorted from most-undervalued downward.
- `get_quality_score` (single stock) and `get_universe_quality_book`
  (all stocks) for "is X a quality business", "what's the ROE", "rank
  by quality", "is X over-leveraged". Quality blends ROE, debt/equity,
  EPS stability, and growth into a 0-100 score with bands HIGH/MEDIUM/
  LOW/JUNK. ALWAYS pair with the value signal: BUY_VALUE on a HIGH
  quality stock = real edge; BUY_VALUE on a JUNK stock = value trap —
  warn the user explicitly.
- `get_earnings_momentum` (single) and `get_universe_earnings_momentum`
  (all) for "is X's earnings improving?", "show me earnings momentum",
  "find accelerating EPS". Returns a flag (ACCELERATING / RECOVERING /
  STEADY / DECELERATING / EROSION), YoY %, prior-YoY %, acceleration
  in pp, 3y CAGR. Earnings momentum is one of the most-documented
  edges in equity research — use it to corroborate momentum-driven
  trade ideas.
- `get_earnings_calendar` for "what's reporting soon", "is X about
  to report", "should I exit before earnings", "show me upcoming
  events". CRITICAL: if a stock has `in_blackout_5d=true`, NEVER
  recommend opening a new BUY/ADD position on it — earnings days
  routinely produce 5-10% gaps that destroy short-term predictions.
  For events 6-14 days out, you may still recommend BUY/ADD but flag
  the event risk and suggest a tighter stop.
- `get_next_earnings` for a single stock's predicted next event date.

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
                 max_tokens: int = 1024,
                 thinking_budget: int | None = None,
                 max_tool_iterations: int = MAX_TOOL_ITERATIONS) -> dict:
        """Run one user turn through the Claude tool-use loop.

        Parameters
        ----------
        history : list of ``{role, content}`` dicts in Claude format.
        system : system prompt.
        max_tokens : upper bound on the model's response (per turn).
        thinking_budget : when set (>=1024), enables Claude's extended
            thinking mode with the given internal-reasoning token
            budget. Required for Sonnet 4.5+ master-strategist runs.
            Anthropic requires ``thinking_budget < max_tokens``; we
            silently bump ``max_tokens`` if the caller passed too small
            a value so the call still succeeds.
        max_tool_iterations : safety cap on the tool-use loop.

        Returns ``{"text": str, "trace": list_of_tool_calls,
                   "thinking": str, "stop_reason": str, "usage": dict}``.
        ``thinking`` is the concatenated internal-reasoning summary
        from all turns (empty string when ``thinking_budget`` is
        ``None`` or the model didn't emit one).
        """
        self._ensure()
        messages = list(history)
        trace: list[dict] = []
        thinking_chunks: list[str] = []
        last_usage: dict[str, Any] = {}
        last_stop_reason: str = ""

        # Build the per-call kwargs once. ``thinking`` and ``max_tokens``
        # have to be re-validated on every call because the master
        # strategist sometimes increases the budget mid-loop.
        extra_kwargs: dict[str, Any] = {}
        if thinking_budget and thinking_budget >= 1024:
            # Anthropic requires budget_tokens < max_tokens. Give the
            # model headroom for its actual response on top of thinking.
            response_room = max(2048, max_tokens)
            effective_max = max(thinking_budget + response_room, max_tokens)
            extra_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": int(thinking_budget),
            }
            # Extended thinking requires temperature=1 (Anthropic
            # constraint as of Sonnet 4.5).
            extra_kwargs["temperature"] = 1.0
        else:
            effective_max = max_tokens

        for _ in range(max_tool_iterations):
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=effective_max,
                system=system,
                tools=TOOL_SCHEMAS_ANTHROPIC,
                messages=messages,
                **extra_kwargs,
            )
            last_stop_reason = getattr(resp, "stop_reason", "") or ""
            usage = getattr(resp, "usage", None)
            if usage is not None:
                # Pydantic model on the SDK; capture the most recent
                # usage so the strategist can log cost.
                try:
                    last_usage = (usage.model_dump()
                                  if hasattr(usage, "model_dump")
                                  else dict(usage))
                except Exception:
                    last_usage = {}

            # Pull thinking blocks out of the response (always — the
            # SDK returns them inline with text + tool_use blocks).
            for b in resp.content:
                btype = getattr(b, "type", "")
                if btype == "thinking":
                    t = getattr(b, "thinking", "") or ""
                    if t:
                        thinking_chunks.append(t)
                elif btype == "redacted_thinking":
                    # Encrypted by Anthropic; record a marker so the
                    # caller knows reasoning happened.
                    thinking_chunks.append("[redacted thinking block]")

            if resp.stop_reason != "tool_use":
                text = "".join(
                    getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", "") == "text"
                ).strip()
                return {
                    "text": text, "trace": trace,
                    "thinking": "\n\n".join(thinking_chunks).strip(),
                    "stop_reason": last_stop_reason,
                    "usage": last_usage,
                }

            # Append the assistant message verbatim — IMPORTANT: when
            # extended thinking is on, we must keep the thinking blocks
            # in the assistant turn we send back, otherwise Anthropic
            # rejects the next call (the thinking signature is what
            # ties tool_use blocks back to the original reasoning).
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

        return {
            "text": "(too many tool iterations — stopping)",
            "trace": trace,
            "thinking": "\n\n".join(thinking_chunks).strip(),
            "stop_reason": last_stop_reason,
            "usage": last_usage,
        }


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
