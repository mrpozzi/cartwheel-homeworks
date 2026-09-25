"""Tracing setup and the hand-written instrumentation for Homework 2.

`setup_tracing()` is the whole course stack: the Langfuse client reads
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and LANGFUSE_HOST from the
environment and registers an OpenTelemetry tracer provider.
OpenLLMetry's OpenAI Agents integration records agent, model, and tool spans
using OTel GenAI attributes. Students add request spans,
auth context and permission-denied results as span
attributes (the `cartwheel.*` namespace from the Module 1 outline,
Artifact G).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.tracing import set_trace_processors
from agents.tracing.processors import default_processor
from opentelemetry import trace

if TYPE_CHECKING:
    from agent.auth import AuthContext

REPO_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("cartwheel.instrument")

_genai_instrumented = False
_openai_tracing_enabled = False


def configure_model_tracing(*, openai_model: bool) -> None:
    """Remove implicit hosted export for non-OpenAI models.

    SDK processors are process-wide. Preserve either explicitly selected course
    destination; do not globally disable spans, which would also break Langfuse.
    """
    if not openai_model and not _genai_instrumented and not _openai_tracing_enabled:
        set_trace_processors([])


def setup_openai_tracing() -> bool:
    """Explicitly select hosted tracing, including for non-OpenAI inference."""
    global _openai_tracing_enabled
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ValueError("--trace-openai requires OPENAI_API_KEY; omit the flag for local chat")
    set_trace_processors([default_processor()])
    _openai_tracing_enabled = True
    return True



def instrument_genai(tracer_provider: Any) -> None:
    """Install GenAI recording once, using the supplied OTel provider."""
    global _genai_instrumented
    if _genai_instrumented:
        return
    from opentelemetry.instrumentation.openai_agents import OpenAIAgentsInstrumentor

    os.environ.setdefault("TRACELOOP_TRACE_CONTENT", "false")
    # Export only through Langfuse, not the SDK's separate hosted tracing path.
    instrumentor = OpenAIAgentsInstrumentor(replace_existing_processors=True)
    instrumentor.instrument(tracer_provider=tracer_provider)
    if not instrumentor.is_instrumented_by_opentelemetry:
        raise RuntimeError("OpenAI Agents tracing instrumentation failed to install")
    _genai_instrumented = True


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (existing vars win).

    A tiny loader so the repo does not need python-dotenv. Lines starting
    with '#' and blank lines are ignored. Values are never logged.
    """
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            os.environ.setdefault(key, value)


def setup_tracing() -> bool:
    """Install Langfuse tracing; return False when credentials are missing."""
    load_env()
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        log.warning(
            "LANGFUSE_PUBLIC_KEY is not set; tracing is off. Start the stack "
            "(docker compose -f observability/docker-compose.yml up -d) and "
            "copy .env.example to .env."
        )
        return False
    from langfuse import get_client

    get_client()  # registers the OTel tracer provider from LANGFUSE_* env vars
    instrument_genai(trace.get_tracer_provider())
    # Preserve processor replacement for callers such as the server, which
    # ignore our return value. A missing secret makes Langfuse a no-op client;
    # returning before replacement would leave hosted OpenAI export active.
    if not os.environ.get("LANGFUSE_SECRET_KEY"):
        return False
    log.info("tracing enabled; spans go to %s", os.environ.get("LANGFUSE_HOST"))
    return True


_workshop_processor: Any = None


def setup_workshop() -> bool:
    """Mirror every span to a local Raindrop Workshop (Homework 4, Part C).

    Active only when ``RAINDROP_LOCAL_DEBUGGER`` names the local daemon, and
    only after :func:`setup_tracing` has installed the Langfuse tracer
    provider. Workshop accepts OTLP/HTTP, so one more batch processor with a
    standard OTLP exporter is attached to that same provider; no second
    provider is created and the Langfuse export is untouched. Agent, model,
    and tool spans then appear in Workshop with the same GenAI attributes that
    Langfuse receives. Local mirroring needs no cloud write key.
    """
    global _workshop_processor
    if _workshop_processor is not None:
        return True
    url = os.environ.get("RAINDROP_LOCAL_DEBUGGER", "").strip().rstrip("/")
    if not url:
        return False
    provider = trace.get_tracer_provider()
    add_processor = getattr(provider, "add_span_processor", None)
    if add_processor is None:
        log.warning("RAINDROP_LOCAL_DEBUGGER is set but no tracer provider exists; run setup_tracing() first")
        return False
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    endpoint = url if url.endswith("/traces") else f"{url}/traces"
    _workshop_processor = BatchSpanProcessor(_WorkshopExporter(endpoint=endpoint, timeout=5))
    add_processor(_workshop_processor)
    log.info("workshop mirroring enabled; spans also go to %s", endpoint)
    return True


def _workshop_exporter_class() -> type:
    """Build the exporter class lazily so importing this module stays cheap."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import ReadableSpan

    class WorkshopExporter(OTLPSpanExporter):
        """OTLP exporter that adds the tool payload keys Workshop reads.

        OpenLLMetry records a tool's arguments and result under the GenAI
        attributes; Workshop fills its tool input and output columns from
        ``tool.input`` / ``tool.output``. The copy happens on the exported
        span only, so the spans Langfuse receives are unchanged.
        """

        _COPIES = (
            ("gen_ai.tool.call.arguments", "tool.input"),
            ("gen_ai.tool.call.result", "tool.output"),
        )

        def export(self, spans):  # type: ignore[override]
            adapted = []
            for span in spans:
                attrs = dict(span.attributes or {})
                extra = {dst: attrs[src] for src, dst in self._COPIES if src in attrs and dst not in attrs}
                if extra:
                    span = ReadableSpan(
                        name=span.name,
                        context=span.get_span_context(),
                        parent=span.parent,
                        resource=span.resource,
                        attributes={**attrs, **extra},
                        events=span.events,
                        links=span.links,
                        kind=span.kind,
                        status=span.status,
                        start_time=span.start_time,
                        end_time=span.end_time,
                        instrumentation_scope=span.instrumentation_scope,
                    )
                adapted.append(span)
            return super().export(adapted)

    return WorkshopExporter


class _WorkshopExporterProxy:
    """Instantiate the lazily built exporter class (keeps the import local)."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _workshop_exporter_class()(*args, **kwargs)


_WorkshopExporter = _WorkshopExporterProxy


def flush_workshop() -> None:
    """Drain spans queued for Workshop; safe to call when mirroring is off."""
    if _workshop_processor is None:
        return
    try:
        _workshop_processor.force_flush(timeout_millis=5000)
    except Exception:  # never let telemetry take the host down
        log.debug("workshop flush failed", exc_info=True)


def record_tool_result(ctx: "AuthContext", result: dict[str, Any]) -> None:
    """Add authenticated identity and permission attributes to the active tool span.

    OpenLLMetry creates the tool span and records its name, arguments, and
    result. The tool wrappers call this helper before that span ends.
    Add the caller's user_role and string user_id, plus the string store_id
    for merchants, then record the permission decision with the helper below.
    When tracing is off, the active span is non-recording and this is a no-op.
    """
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_attribute("cartwheel.user_role", ctx.role)
    span.set_attribute("cartwheel.user_id", str(ctx.user_id))
    if ctx.role == "merchant":
        span.set_attribute("cartwheel.store_id", str(ctx.store_id))
    _set_permission_denied_attributes(span, result)


def _set_permission_denied_attributes(
    span: trace.Span, result: dict[str, Any]
) -> None:
    """Set the permission-denied attributes on a tool span.

    Contract (Module 1 outline, Artifact G):
      - `result` is the structured dict a tool returned (see agent/auth.py
        for the convention).
      - Always set the span attribute "cartwheel.permission_denied" to a
        bool: True when result["error"] == "permission_denied", else False.
        Use result.get, since success dicts have no "error" key.
      - When it is True, also set "cartwheel.permission_denied.reason" to
        result["reason"] (default to "" if the reason is missing).
      - Set attributes with span.set_attribute(name, value). Do not raise on
        odd input; any dict without the permission_denied error code is
        simply False.

    Why this exists: permission-denied events are gold for Module 4, and
    the smoke report counts them and Module 3 asserts on them. This is the one place in the
    course where you touch instrumentation by hand.
    """
    denied = result.get("error") == "permission_denied"
    span.set_attribute("cartwheel.permission_denied", denied)
    if denied:
        span.set_attribute(
            "cartwheel.permission_denied.reason", result.get("reason") or ""
        )
