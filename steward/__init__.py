"""Splunk Steward — an AI operations copilot that fills the missing-Splunk-expert gap.

Modules:
    config        Environment/config loading (.env).
    llm_groq      Groq LLM wrapper (OpenAI-compatible open models).
    splunk_mcp    Read path: query Splunk via the MCP Server.
    splunk_rest   Write path: apply .conf changes and ingest via REST.
    onboarding    Core flow: log sample -> generated parsing config -> preview -> apply.
    health        Diagnostic checks explained in plain language.
"""

__version__ = "0.1.0"
