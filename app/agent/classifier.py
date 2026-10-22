"""Request classification and argument extraction.

Two strategies:
* ``rules`` - deterministic keyword/regex classification (mock mode, and the
  fallback whenever the LLM's proposal is unusable).
* ``llm``   - the model proposes an intent, tool and arguments as JSON.

Either way the output is only a *proposal*. The tool is fixed by the intent,
the arguments are schema-validated, and the policy engine decides whether it
may run. The classifier has no say in authorisation.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agent.prompts import PLANNER_SYSTEM, planner_prompt
from app.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse
from app.tools.builtin import ToolRegistry

logger = logging.getLogger(__name__)

Intent = Literal["knowledge_question", "system_status", "create_incident", "usage_report", "general"]
INTENT_TOOL: dict[str, str | None] = {
    "knowledge_question": "document_search",
    "system_status": "system_status",
    "create_incident": "create_incident",
    "usage_report": "usage_report",
    "general": None,
}

_QUESTION_START = re.compile(r"^\s*(how|what|when|who|why|where|which|should|do|does|is|are|explain)\b", re.I)
_INCIDENT = re.compile(r"\b(create|open|raise|file|log|declare|start)\b[^.?!]{0,40}\bincident\b", re.I)
_PAGE = re.compile(r"\bpage (the )?on-?call\b", re.I)
_USAGE = re.compile(r"\b(usage (report|summary|stats)|token usage|how many (requests|runs|queries))\b", re.I)
_STATUS_WORD = re.compile(r"\b(status|health|healthy|up|down|working|online|offline|uptime|degraded)\b", re.I)
_SYSTEM_WORD = re.compile(
    r"\b(system|systems|api|database|db|vector|store|chroma|llm|model|bedrock|service|"
    r"services|platform|everything|components?)\b",
    re.I,
)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|help|good (morning|afternoon|evening))\b[\s!.?]*$", re.I
)
_TITLE = re.compile(r"\bincident\b\s*(?:for|about|regarding|on|:|-)\s*(.+)$", re.I | re.S)

_SEVERITY = [
    (re.compile(r"\b(sev ?1|p1|critical)\b", re.I), "sev1"),
    (re.compile(r"\b(sev ?2|p2|high|major)\b", re.I), "sev2"),
    (re.compile(r"\b(sev ?4|p4|low|minor)\b", re.I), "sev4"),
    (re.compile(r"\b(sev ?3|p3|medium|moderate)\b", re.I), "sev3"),
]
_COMPONENTS = [
    (re.compile(r"\bvector( store)?|chroma|embedding", re.I), "vector_store"),
    (re.compile(r"\b(database|db|sqlite)\b", re.I), "database"),
    (re.compile(r"\b(llm|model|bedrock|inference)\b", re.I), "llm"),
    (re.compile(r"\b(auth|login|sso|token)\b", re.I), "auth"),
    (re.compile(r"\b(ingest|ingestion|upload|indexing)\b", re.I), "ingestion"),
    (re.compile(r"\b(api|gateway|endpoint)\b", re.I), "api"),
]


@dataclass(frozen=True, slots=True)
class Classification:
    intent: Intent
    tool_name: str | None
    arguments: dict[str, Any] = field(default_factory=dict)
    source: Literal["rules", "llm"] = "rules"
    llm_response: LLMResponse | None = None


def _component(message: str, default: str) -> str:
    return next((name for pattern, name in _COMPONENTS if pattern.search(message)), default)


def _incident_args(message: str) -> dict[str, Any]:
    severity = next((sev for pattern, sev in _SEVERITY if pattern.search(message)), "sev3")
    match = _TITLE.search(message)
    title = (match.group(1) if match else message).strip().rstrip(".!?")
    title = re.sub(r"\b(sev ?[1-4]|p[1-4])\b", "", title, flags=re.I).strip(" ,:-")
    if len(title) < 5:
        title = message.strip()[:120]
    return {
        "title": (title[:1].upper() + title[1:])[:120],
        "severity": severity,
        "description": message.strip()[:2000],
        "affected_component": _component(message, "unspecified"),
    }


def classify_with_rules(message: str) -> Classification:
    text = message.strip()
    if (_INCIDENT.search(text) or _PAGE.search(text)) and not _QUESTION_START.match(text):
        return Classification("create_incident", "create_incident", _incident_args(text))
    if _USAGE.search(text):
        return Classification("usage_report", "usage_report", {"days": 7})
    if _STATUS_WORD.search(text) and _SYSTEM_WORD.search(text):
        component = _component(text, "all")
        if component not in {"api", "database", "vector_store", "llm"}:
            component = "all"
        return Classification("system_status", "system_status", {"component": component})
    if _GREETING.match(text):
        return Classification("general", None, {})
    return Classification("knowledge_question", "document_search", {"query": text[:500]})


def _extract_json(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


class Classifier:
    def __init__(self, llm: LLMClient, registry: ToolRegistry, max_tokens: int = 300) -> None:
        self.llm = llm
        self.registry = registry
        self.max_tokens = max_tokens

    def classify(self, message: str) -> Classification:
        if not self.llm.supports_planning:
            return classify_with_rules(message)
        prompt = planner_prompt(message, [t.schema() for t in self.registry.all()])
        try:
            response = self.llm.complete(
                LLMRequest(system=PLANNER_SYSTEM, prompt=prompt, max_tokens=self.max_tokens)
            )
        except LLMError:
            return classify_with_rules(message)

        proposal = _extract_json(response.text) or {}
        intent = proposal.get("intent")
        if intent not in INTENT_TOOL:
            logger.info("planner returned unusable intent; falling back to rules")
            fallback = classify_with_rules(message)
            return Classification(fallback.intent, fallback.tool_name, fallback.arguments, "rules", response)
        # The intent decides the tool. A model naming a *different* tool is ignored.
        tool = INTENT_TOOL[intent]
        arguments = proposal.get("arguments") if isinstance(proposal.get("arguments"), dict) else {}
        if intent == "knowledge_question":
            arguments = {"query": str(arguments.get("query") or message)[:500]}
        return Classification(intent, tool, arguments, "llm", response)
