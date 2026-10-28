"""OpenTelemetry distributed tracing (P2 #12) — optional by construction.

If the opentelemetry packages are installed (they're in requirements.txt for the
next image build) AND OTEL_EXPORTER_OTLP_ENDPOINT is set, real spans are exported
over OTLP. Otherwise every helper is a zero-cost no-op — the app never depends on
tracing being available (same doctrine as the sandbox/DL guards).

Span layout: one root span per pipeline run, one child span per agent node —
correlated with logs via run_id/agent attributes (extends the request_id story
into true Layer-5 tracing).
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_tracer = None
_initialized = False


def _init() -> None:
    global _tracer, _initialized
    _initialized = True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "automl-orchestrator"),
        }))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("automl.pipeline")
    except Exception:
        _tracer = None  # packages absent / exporter misconfigured → no-op


@contextmanager
def span(name: str, **attributes):
    """A traced span if OTel is live, else a no-op context."""
    if not _initialized:
        _init()
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            if v is not None:
                s.set_attribute(k, str(v))
        yield s
