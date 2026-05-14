"""Multi-agent strategist pipeline.

Each agent consumes a focused slice of the briefing and emits a compact,
structured summary. The Master Strategist (Agent C) consumes the
agents' outputs together with the original briefing — never less data
than today, but with pre-digested context that reduces hallucination
risk.

Pipeline:
    briefing
        │
        ├──> Agent A (macro_reader)      ─┐
        ├──> Agent B (stock_scorer)      ─┼──> Agent C (master_strategist_v2)
        └──> playbook_analogues          ─┘            │
                                                       ▼
                                              MasterDecisionV2
                                              + per-tab guidance
                                              + per-position stop/target

Every agent is **fallback-first**: it must work without the LLM and only
optionally calls Claude to refine the rule-based output. The bot is on
fallback ~55% of days today, so production must not require Claude.
"""
