"""Planning Advisor (FY27): allowlisted planning tools + pluggable reasoning engine.

Engines:
- RuleAdvisor (default, free, no model): transparent keyword/entity rules with drill-down context.
- ClaudeAdvisor (optional, paid): same tools via the Claude API; enabled only with NORBAE_ADVISOR_ENGINE=claude
  and ANTHROPIC_API_KEY. Falls back to RuleAdvisor on any error or ungrounded figure.
Neither engine can change the plan: they return *proposals*; a human applies them.
"""
