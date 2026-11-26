"""OpenTelemetry -> Phoenix (OTLP/HTTP). LangChain/LangGraph spans via OpenInference."""

from __future__ import annotations

import logging

from common.config import get_settings

log = logging.getLogger(__name__)
_configured = False


def setup_tracing(service_name: str) -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.phoenix_otlp_endpoint))
        )
        trace.set_tracer_provider(provider)
        LangChainInstrumentor().instrument(tracer_provider=provider)
        _configured = True
        log.info("tracing to %s", settings.phoenix_otlp_endpoint)
    except Exception as exc:  # tracing must never take the service down
        log.warning("tracing disabled: %s", exc)
