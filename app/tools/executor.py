"""Validated, time-limited tool execution."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.observability import metrics
from app.observability.tracing import tracer
from app.tools.base import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

# Shared worker pool. A timed-out handler can't be killed (Python threads are not
# cancellable), so tools must be bounded themselves; the timeout guarantees the
# *request* returns. See SECURITY.md.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool")


class ToolArgumentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: str  # succeeded | failed | timeout
    output: dict[str, Any] | None
    error: str | None
    duration_ms: float


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise arguments against the tool's schema (extra keys are rejected)."""
    try:
        return spec.input_model.model_validate(arguments).model_dump(mode="json")
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        raise ToolArgumentError(f"Invalid arguments for {spec.name}: {problems}") from exc


def execute(spec: ToolSpec, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    timeout = min(spec.timeout_seconds, ctx.settings.tool_timeout_seconds)
    with tracer.start_as_current_span(f"tool.{spec.name}") as span:
        span.set_attribute("tool.name", spec.name)
        span.set_attribute("tool.read_only", spec.read_only)
        try:
            parsed = spec.input_model.model_validate(arguments)
            future = _POOL.submit(spec.handler, ctx, parsed)
            output = future.result(timeout=timeout)
            status, error = "succeeded", None
        except ValidationError:
            output, status, error = None, "failed", "Tool arguments failed validation"
        except FutureTimeout:
            future.cancel()
            output, status, error = None, "timeout", f"Tool timed out after {timeout:g}s"
        except Exception:
            logger.exception("tool %s failed", spec.name)
            output, status, error = None, "failed", "Tool execution failed"
        span.set_attribute("tool.outcome", status)

    elapsed = time.perf_counter() - started
    metrics.TOOL_INVOCATIONS.labels(spec.name, status).inc()
    metrics.TOOL_DURATION.labels(spec.name).observe(elapsed)
    return ToolResult(status=status, output=output, error=error, duration_ms=round(elapsed * 1000, 2))
