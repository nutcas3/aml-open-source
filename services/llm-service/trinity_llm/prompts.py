"""
Prompt templates for the Trinity LLM Service.

Both the investigation and SAR prompts request **JSON output** so the
response can be parsed deterministically. This works with:

* OpenAI JSON mode (``response_format={"type": "json_object"}``)
* Ollama structured output (``format="json"`` in the API call)

Templates are rendered with Jinja2 (already a project dependency).
"""

from __future__ import annotations

import json
from typing import Any

from jinja2 import Environment, StrictUndefined

_jinja_env = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)


# ---------------------------------------------------------------------------
# Investigation prompt
# ---------------------------------------------------------------------------

INVESTIGATION_PROMPT = """\
You are a senior AML compliance analyst evaluating financial transactions for money laundering risk.

## Transaction Details
- ID: {{ transaction_id }}
- Amount: {{ "%s %.2f"|format(currency, amount) }}
- Description: {{ description }}
- Sender: {{ sender }}
- Receiver: {{ receiver }}
- Timestamp: {{ timestamp }}

## Identified Entities
{{ entities_json }}

## Suspicious Indicators
{{ suspicious_indicators_json }}

{% if context %}
## Additional Context
{{ context }}
{% endif %}

## Instructions
Analyze the transaction above for money laundering risk. Consider:
- Sanctions list matches
- Transaction patterns (structuring, layering, integration)
- Geographic risk factors (high-risk jurisdictions)
- Unusual activity indicators (amount thresholds, rapid movement)

Respond with **only** a JSON object (no markdown, no prose) with this exact schema:

{
  "risk_level": "Low" | "Medium" | "High",
  "requires_sar": true | false,
  "reasoning": "<detailed explanation of the risk assessment>",
  "recommended_actions": ["<action 1>", "<action 2>", ...]
}
"""


# ---------------------------------------------------------------------------
# SAR (Suspicious Activity Report) prompt
# ---------------------------------------------------------------------------

SAR_PROMPT = """\
You are a compliance officer drafting a formal Suspicious Activity Report (SAR) narrative.

## Investigation Results
{{ investigation_results }}

## Transaction Summary
{{ transaction_summary }}

## Instructions
Generate a formal SAR narrative following FinCEN guidelines. Include:
- Transaction details (amount, parties, timing)
- Description of the suspicious activity
- Entities involved and their roles
- Timeline of events
- Regulatory concerns
- Recommended follow-up actions

Respond with **only** a JSON object (no markdown, no prose) with this exact schema:

{
  "sar_narrative": "<the formal SAR narrative text>"
}
"""


def render_investigation_prompt(
    *,
    transaction: dict[str, Any],
    entities: list[dict[str, Any]],
    context: str | None = None,
) -> str:
    """Render the investigation prompt from a transaction + entities.

    ``transaction`` and ``entities`` follow the OpenAPI schemas defined in
    ``contracts/openapi.yaml`` (``Transaction`` and ``Entity``).
    """

    suspicious = [e for e in entities if e.get("suspicious")]
    template = _jinja_env.from_string(INVESTIGATION_PROMPT)
    return template.render(
        transaction_id=transaction.get("id", "Unknown"),
        amount=float(transaction.get("amount", 0)),
        currency=transaction.get("currency", "USD"),
        description=transaction.get("description", ""),
        sender=transaction.get("sender", ""),
        receiver=transaction.get("receiver", ""),
        timestamp=transaction.get("timestamp", ""),
        entities_json=json.dumps(entities, indent=2),
        suspicious_indicators_json=json.dumps(suspicious, indent=2),
        context=context or "",
    )


def render_sar_prompt(
    *,
    investigation_results: str,
    transaction: dict[str, Any],
    entities: list[dict[str, Any]],
) -> str:
    """Render the SAR generation prompt."""

    summary = (
        f"ID: {transaction.get('id', 'Unknown')}\n"
        f"Amount: {transaction.get('currency', 'USD')} "
        f"{float(transaction.get('amount', 0)):.2f}\n"
        f"Sender: {transaction.get('sender', '')}\n"
        f"Receiver: {transaction.get('receiver', '')}\n"
        f"Description: {transaction.get('description', '')}\n"
        f"Entities: {json.dumps(entities, indent=2)}"
    )
    template = _jinja_env.from_string(SAR_PROMPT)
    return template.render(
        investigation_results=investigation_results,
        transaction_summary=summary,
    )
