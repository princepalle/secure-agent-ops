"""OpenTelemetry tracing.

Spans are always created (FastAPI requests, agent nodes, tools, retrieval,
LLM calls). They are exported only when configured: to an OTLP/HTTP
collector via ``SAO_OTEL_EXPORTER_OTLP_ENDPOINT`` and/or to stdout via
``SAO_OTEL_CONSOLE_EXPORT=true``.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app.config import Settings

logger = logging.getLogger(__name__)

tracer = trace.get_tracer("secureagentops")
_configured = False


def configure_tracing(settings: Settings) -> None:
    global _configured
    if _configured:  # the global provider can only be set once per process
        return
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.otel_service_name,
                "service.version": settings.app_version,
                "deployment.environment": settings.environment,
            }
        )
    )
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        logger.info("exporting traces to %s", endpoint)
    if settings.otel_console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
