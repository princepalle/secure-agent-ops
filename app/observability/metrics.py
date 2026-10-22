"""Prometheus metrics (exposed at ``/metrics``).

Labels are low-cardinality by design: no tenant ids, user ids or raw paths.
Token counters carry a ``source`` label so measured provider counts are
never mixed with heuristic estimates.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

HTTP_REQUESTS = Counter("sao_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "sao_http_request_duration_seconds", "HTTP request latency", ["method", "route"], buckets=_LATENCY_BUCKETS
)
AGENT_RUNS = Counter("sao_agent_runs_total", "Agent workflow runs", ["intent", "status"])
AGENT_DURATION = Histogram(
    "sao_agent_run_duration_seconds",
    "End-to-end agent workflow duration",
    ["intent"],
    buckets=_LATENCY_BUCKETS,
)
AGENT_NODE_DURATION = Histogram(
    "sao_agent_node_duration_seconds", "Duration of each agent graph node", ["node"], buckets=_LATENCY_BUCKETS
)
TOOL_INVOCATIONS = Counter("sao_tool_invocations_total", "Tool invocations by outcome", ["tool", "outcome"])
TOOL_DURATION = Histogram(
    "sao_tool_duration_seconds", "Tool execution duration", ["tool"], buckets=_LATENCY_BUCKETS
)
RETRIEVAL_LATENCY = Histogram(
    "sao_retrieval_duration_seconds", "Vector retrieval latency", buckets=_LATENCY_BUCKETS
)
LLM_CALLS = Counter("sao_llm_calls_total", "LLM calls", ["provider", "outcome"])
LLM_TOKENS = Counter(
    "sao_llm_tokens_total", "LLM tokens; source=measured|estimated", ["provider", "direction", "source"]
)
LLM_COST = Counter(
    "sao_llm_estimated_cost_usd_total",
    "Estimated LLM cost in USD from configured pricing (only when pricing is set)",
    ["provider"],
)
RATE_LIMITED = Counter("sao_rate_limited_total", "Requests rejected by the rate limiter", ["scope"])
APPROVALS = Counter("sao_approvals_total", "Approval decisions", ["decision"])
DOCUMENTS_INDEXED = Counter("sao_documents_indexed_total", "Documents indexed")
BUILD_INFO = Gauge("sao_build_info", "Build information", ["version", "llm_provider", "embedding_provider"])
