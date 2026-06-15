"""The onboarding copilot — Steward's centerpiece flow.

    raw log sample  ->  Groq generates parsing config  ->  preview  ->  human approves  ->  apply

The LLM never touches Splunk directly. It only proposes a config; writes happen
through splunk_rest after explicit approval in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import container, splunk_rest
from .llm_groq import complete_json

PREVIEW_INDEX = "steward_preview"

_SYSTEM = """You are a senior Splunk data-onboarding engineer. Given a raw log sample,
infer how Splunk should parse it and return the props.conf settings that produce
clean, correctly-timestamped events.

Return a JSON object with exactly these keys:
- "sourcetype": a concise suggested sourcetype name (lowercase, ':' separated)
- "props": an object of props.conf key/value strings. Prefer these keys when relevant:
    SHOULD_LINEMERGE, LINE_BREAKER, TIME_PREFIX, TIME_FORMAT,
    MAX_TIMESTAMP_LOOKAHEAD, TRUNCATE, KV_MODE, EXTRACT-<name>
- "rationale": 1-2 sentences, plain language, explaining the key choices.
- "confidence": "high" | "medium" | "low"

CRITICAL line-breaking rule (Splunk silently ignores misconfigured combinations):
- LINE_BREAKER is ONLY honored when SHOULD_LINEMERGE = false. If you provide a
  custom LINE_BREAKER you MUST also set SHOULD_LINEMERGE = false, otherwise Splunk
  ignores the breaker and merges separate events into one.
- For logs with a reliable per-event start (timestamp, bracket, marker), prefer the
  explicit approach: SHOULD_LINEMERGE = false plus a LINE_BREAKER that captures the
  separator before each new event.
- Use SHOULD_LINEMERGE = true only for genuinely multi-line events with no reliable
  per-event start delimiter, and in that case do NOT set LINE_BREAKER — use
  BREAK_ONLY_BEFORE instead.
- Never set SHOULD_LINEMERGE = true and LINE_BREAKER together.

Base every setting on evidence in the sample. Do not invent fields that are not present."""


@dataclass
class OnboardingProposal:
    sourcetype: str
    props: dict[str, str]
    rationale: str
    confidence: str
    raw: dict[str, Any] = field(default_factory=dict)

    def as_conf_text(self) -> str:
        """Render the proposal as a props.conf stanza for the approval diff."""
        lines = [f"[{self.sourcetype}]"]
        lines += [f"{k} = {v}" for k, v in self.props.items()]
        return "\n".join(lines)


def propose_config(log_sample: str, max_lines: int = 40) -> OnboardingProposal:
    """Ask Groq to infer parsing config from a sample. Pure read — no Splunk writes."""
    sample = "\n".join(log_sample.splitlines()[:max_lines])
    result = complete_json(_SYSTEM, f"Log sample:\n```\n{sample}\n```")
    return OnboardingProposal(
        sourcetype=result.get("sourcetype", "steward:unknown"),
        props=result.get("props", {}),
        rationale=result.get("rationale", ""),
        confidence=result.get("confidence", "low"),
        raw=result,
    )


def apply_proposal(proposal: OnboardingProposal) -> dict:
    """Write the approved config. Call ONLY after human confirmation in the UI."""
    splunk_rest.apply_conf_stanza("props", proposal.sourcetype, proposal.props)
    return {"applied": proposal.sourcetype, "props": proposal.props}


def preview_ingest(filepath: str, proposal: OnboardingProposal) -> dict:
    """Ingest the sample into a throwaway index so the user can verify parsing.

    Stages the host file into the Splunk container first (Splunk reads from the
    container filesystem, not the host). Returns enough detail for the UI to run
    a follow-up search and show the parsed events.
    """
    splunk_rest.ensure_index(PREVIEW_INDEX)
    splunk_rest.apply_conf_stanza("props", proposal.sourcetype, proposal.props)
    container_path = container.stage_file(filepath)
    splunk_rest.oneshot_ingest(container_path, PREVIEW_INDEX, proposal.sourcetype)
    return {
        "index": PREVIEW_INDEX,
        "sourcetype": proposal.sourcetype,
        "verify_spl": f'index={PREVIEW_INDEX} sourcetype={proposal.sourcetype}',
    }
