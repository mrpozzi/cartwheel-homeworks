"""Trace loading and view-model construction for the Homework 4 review app.

Sources, in order of preference:

1. Live Langfuse. Every trace whose ``cartwheel.scenario_id`` starts with the
   support prefix is fetched in full and cached under ``cache/traces.json``
   (gitignored) in the same record shape the Module 1 export uses.
2. The Module 1 export ``traces/support_traces.json`` as the offline fallback.
   The chosen source and the reason are recorded so the interface comparison
   can report them.

The view model groups traces by ``cartwheel.session_id`` into sessions, orders
the turns by timestamp, and groups each turn's observations into steps: the
model's narration sentence, the tool call, and the tool result. The final
generation without a tool call is the reply. Generation inputs are dropped,
because the session timeline already shows every message they contain.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
CACHE_DIR = HERE / "cache"
CACHE_PATH = CACHE_DIR / "traces.json"
EXPORT_PATH = REPO_ROOT / "traces" / "support_traces.json"
SCENARIOS_PATH = REPO_ROOT / "scenarios" / "support_scenarios.jsonl"
SPEC_PATH = REPO_ROOT / "SPEC.md"

SCENARIO_PREFIX = "support-"

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Turn a Langfuse SDK model into plain JSON data (camelCase keys)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "json") and hasattr(value, "dict"):
        return json.loads(value.json(by_alias=True))
    if hasattr(value, "dict"):
        return value.dict(by_alias=True)
    return value


def _attrs(metadata: Any) -> dict[str, Any]:
    """Merge ``metadata.attributes`` (dict or JSON string) with top-level keys."""
    if not isinstance(metadata, dict):
        return {}
    attributes = metadata.get("attributes")
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except ValueError:
            attributes = None
    merged = dict(attributes) if isinstance(attributes, dict) else {}
    for key, value in metadata.items():
        if key != "attributes":
            merged.setdefault(key, value)
    return merged


def _get(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Read the first present key among camelCase and snake_case spellings."""
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return default


def _parts_text(message: Any) -> str:
    """Concatenate the text parts of one message."""
    if not isinstance(message, dict):
        return "" if message is None else str(message)
    out = []
    for part in message.get("parts") or []:
        if isinstance(part, dict) and part.get("type") == "text" and part.get("content"):
            out.append(str(part["content"]))
    return "\n".join(out)


def _messages_text(value: Any) -> str:
    """Text of a trace input or output: a list of messages or a raw string."""
    if isinstance(value, list):
        return "\n".join(t for t in (_parts_text(m) for m in value) if t)
    if isinstance(value, dict):
        return _parts_text(value)
    return "" if value is None else str(value)


def _money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# trace sources
# ---------------------------------------------------------------------------


def fetch_from_langfuse(prefix: str = SCENARIO_PREFIX, page_size: int = 100) -> list[dict[str, Any]]:
    """Fetch every full trace whose scenario id starts with ``prefix``.

    Requires the ``LANGFUSE_*`` environment (loaded from ``.env`` by the
    caller). Trace summaries carry metadata, so only matching traces are
    fetched in full.
    """
    from langfuse import Langfuse

    client = Langfuse()
    records: list[dict[str, Any]] = []
    page = 1
    while True:
        response = client.api.trace.list(page=page, limit=page_size)
        batch = list(response.data or [])
        for summary in batch:
            scenario_id = _attrs(_jsonable(getattr(summary, "metadata", None))).get(
                "cartwheel.scenario_id"
            )
            if not scenario_id or not str(scenario_id).startswith(prefix):
                continue
            full = _jsonable(client.api.trace.get(summary.id))
            if isinstance(full, dict):
                full.setdefault("cartwheel_scenario_id", str(scenario_id))
                records.append(full)
        if len(batch) < page_size:
            break
        page += 1
    return records


def load_records(source: str = "auto", refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return ``(records, source_info)`` for the requested source.

    ``source`` is ``"auto"`` (cache, then live Langfuse, then the export),
    ``"langfuse"`` (live or cache only), or ``"export"``. ``refresh`` ignores
    the cache. ``source_info`` records what was used and why.
    """
    info: dict[str, Any] = {"requested": source, "used": None, "reason": None}
    try:
        from observability.instrument import load_env

        load_env()  # LANGFUSE_HOST is needed for trace links even in export mode
    except Exception:
        pass

    if source in ("auto", "langfuse"):
        if not refresh and CACHE_PATH.exists():
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            info.update(
                used="langfuse-cache",
                fetched_at=payload.get("fetched_at"),
                host=payload.get("host"),
                path=str(CACHE_PATH),
                reason="cached copy of the live Langfuse fetch",
            )
            return payload["traces"], info
        try:
            host = os.environ.get("LANGFUSE_HOST")
            if not (
                os.environ.get("LANGFUSE_PUBLIC_KEY")
                and os.environ.get("LANGFUSE_SECRET_KEY")
                and host
            ):
                raise RuntimeError("LANGFUSE_* environment is not configured")
            records = fetch_from_langfuse()
            if not records:
                raise RuntimeError("Langfuse returned no support scenario traces")
            fetched_at = datetime.now(timezone.utc).isoformat()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(
                json.dumps(
                    {
                        "fetched_at": fetched_at,
                        "source": "langfuse",
                        "host": host,
                        "trace_count": len(records),
                        "traces": records,
                    },
                    default=str,
                ),
                encoding="utf-8",
            )
            info.update(used="langfuse", fetched_at=fetched_at, host=host, path=str(CACHE_PATH), reason="live fetch")
            return records, info
        except Exception as exc:  # network, auth, or missing SDK
            if source == "langfuse":
                raise
            info["fallback_reason"] = f"Langfuse unavailable: {type(exc).__name__}: {exc}"

    payload = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    records = payload["traces"] if isinstance(payload, dict) else payload
    info.update(
        used="export",
        path=str(EXPORT_PATH),
        exported_at=payload.get("exported_at") if isinstance(payload, dict) else None,
        reason=info.get("fallback_reason") or "export requested",
    )
    return records, info


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, dict[str, Any]]:
    """Map scenario id to its row from the Homework 3 dataset."""
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


# ---------------------------------------------------------------------------
# SPEC.md requirement index
# ---------------------------------------------------------------------------

_REQ_RE = re.compile(r"^(?:- )?\*\*([A-Z]+-\d+)\.\*\*\s*(.*)$")
_TOOL_ROW_RE = re.compile(r"^\|\s*(TOOL-\d+)\s*\|\s*`(\w+)`\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\w+)\s*\|$")


def load_spec(path: Path = SPEC_PATH) -> list[dict[str, str]]:
    """Return ``{id, section, text}`` for every requirement identifier in SPEC.md."""
    if not path.exists():
        return []
    out: list[dict[str, str]] = []
    section = ""
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip()
            current = None
            continue
        match = _REQ_RE.match(line.strip())
        if match:
            current = {"id": match.group(1), "section": section, "text": match.group(2).strip()}
            out.append(current)
            continue
        row = _TOOL_ROW_RE.match(line.strip())
        if row:
            out.append(
                {
                    "id": row.group(1),
                    "section": section,
                    "text": f"`{row.group(2)}`: inputs {row.group(3)}; side effects: {row.group(4)}; risk: {row.group(5)}.",
                }
            )
            current = None
            continue
        if current is not None:
            if not line.strip() or line.startswith("|") or line.startswith("#"):
                current = None
            else:
                current["text"] = (current["text"] + " " + line.strip()).strip()
    return out


# ---------------------------------------------------------------------------
# view model
# ---------------------------------------------------------------------------


def _summary(name: str, output: Any) -> str:
    """One-line human summary of a tool result, shown above the collapsed JSON."""
    if not isinstance(output, dict):
        text = json.dumps(output, ensure_ascii=False) if output is not None else ""
        return text[:160]
    if output.get("ok") is False:
        return f"{output.get('error', 'error')}: {output.get('reason', '')}".strip()
    if name == "get_order" and isinstance(output.get("order"), dict):
        o = output["order"]
        return (
            f"order {o.get('order_id')} {o.get('status')}, {_money(o.get('total_usd'))}, "
            f"refund_eligible={o.get('refund_eligible')}, delivered {o.get('delivered_at') or 'n/a'}, "
            f"store {o.get('store_name') or o.get('store_id')}"
        )
    if name == "issue_refund":
        note = f" ({output['note']})" if output.get("note") else ""
        return f"{output.get('status')}: refund {output.get('refund_id')} for {_money(output.get('amount_usd'))}{note}"
    if name == "cancel_order":
        return f"{output.get('status')}: order {output.get('order_id')}"
    if name == "escalate_to_human":
        return f"ticket {output.get('ticket_id')} created, SLA {output.get('sla_hours')}h"
    if name == "search_help_center":
        ids = [r.get("policy_id") for r in output.get("results", []) if isinstance(r, dict)]
        return f"{len(ids)} results: {', '.join(str(i) for i in ids)}"
    if name == "get_policy":
        return f"{output.get('policy_id')}: {output.get('title')}"
    if name == "get_store_info" and isinstance(output.get("store"), dict):
        s = output["store"]
        return (
            f"{s.get('name')}: window {s.get('return_window_days')}d "
            f"(override={s.get('has_return_window_override')}), cite {s.get('return_window_policy_id')}, "
            f"restocking opt-in={s.get('restocking_fee_opt_in')}"
        )
    if name == "search_products":
        products = output.get("products", [])
        head = "; ".join(
            f"{p.get('title')} {_money(p.get('price_usd'))} (store {p.get('store_id')})"
            for p in products[:3]
            if isinstance(p, dict)
        )
        return f"{output.get('count', len(products))} products" + (f": {head}" if head else "")
    if name in ("find_order", "list_my_orders"):
        orders = output.get("orders", [])
        ids = [str(o.get("order_id")) for o in orders if isinstance(o, dict)]
        return f"{len(orders)} orders" + (f": {', '.join(ids[:8])}" + ("…" if len(ids) > 8 else "") if ids else "")
    return "ok"


def _badges(steps: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Outcome badges for a turn header, computed from the tool results."""
    badges: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, text: str) -> None:
        if text not in seen:
            seen.add(text)
            badges.append({"kind": kind, "text": text})

    for step in steps:
        result = step.get("tool_result") or {}
        output = result.get("output")
        name = (step.get("tool_call") or {}).get("name") or result.get("name")
        if result.get("permission_denied"):
            add("danger", "permission denied")
        if isinstance(output, dict):
            if output.get("ok") is False:
                error = str(output.get("error"))
                if error == "permission_denied":
                    add("danger", "permission denied")
                else:
                    add("warn", error)
            status = output.get("status")
            if name == "issue_refund" and status:
                label = {"queued_for_approval": "refund queued", "auto_approved": "refund auto-approved"}.get(status, f"refund {status}")
                add("warn" if status == "queued_for_approval" else "ok", f"{label} {_money(output.get('amount_usd'))}")
            elif name == "cancel_order" and status:
                add("ok", f"order {status}")
            if name == "escalate_to_human" and output.get("ticket_id") is not None:
                add("info", f"escalated #{output['ticket_id']}")
    if not steps:
        add("muted", "no tools")
    return badges


def _same_args(a: Any, b: Any) -> bool:
    """True when two argument payloads are equal after JSON normalization."""
    try:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return a == b


def _build_turn(record: dict[str, Any], index: int, host: str | None) -> dict[str, Any]:
    """Group one trace's observations into steps and a reply."""
    trace_id = str(_get(record, "id", "trace_id"))
    observations = [o for o in (record.get("observations") or []) if isinstance(o, dict)]
    observations.sort(key=lambda o: str(_get(o, "startTime", "start_time", default="")))

    steps: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    reply = ""
    system_prompt = ""
    tokens = {"input": 0, "output": 0, "total": 0}
    models: list[str] = []
    generation_count = 0

    for obs in observations:
        kind = str(obs.get("type") or "").upper()
        if kind == "GENERATION":
            generation_count += 1
            model = obs.get("model") or _get(obs, "modelId", "model_id")
            if model and model not in models:
                models.append(str(model))
            tokens["input"] += int(_get(obs, "promptTokens", "prompt_tokens", default=0) or 0)
            tokens["output"] += int(_get(obs, "completionTokens", "completion_tokens", default=0) or 0)
            tokens["total"] += int(_get(obs, "totalTokens", "total_tokens", default=0) or 0)
            if not system_prompt:
                inp = obs.get("input")
                messages = inp.get("messages") if isinstance(inp, dict) else inp
                if isinstance(messages, list) and messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                    system_prompt = _parts_text(messages[0])
            out = obs.get("output")
            messages = out if isinstance(out, list) else [out] if isinstance(out, dict) else []
            texts: list[str] = []
            calls: list[dict[str, Any]] = []
            for message in messages:
                for part in (message.get("parts") or []) if isinstance(message, dict) else []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and part.get("content"):
                        texts.append(str(part["content"]))
                    elif part.get("type") == "tool_call":
                        body = part.get("content") if isinstance(part.get("content"), dict) else part
                        calls.append(
                            {
                                "name": body.get("name"),
                                "arguments": body.get("arguments"),
                                "call_id": body.get("id"),
                                "generation_id": obs.get("id"),
                            }
                        )
            narration = "\n".join(texts)
            if calls:
                for i, call in enumerate(calls):
                    step = {"narration": narration if i == 0 else "", "tool_call": call, "tool_result": None}
                    steps.append(step)
                    pending.append(step)
            elif narration:
                reply = narration
        elif kind == "TOOL":
            name = obs.get("name")
            attrs = _attrs(obs.get("metadata"))
            result = {
                "name": name,
                "observation_id": obs.get("id"),
                "input": obs.get("input"),
                "output": obs.get("output"),
                "summary": _summary(str(name), obs.get("output")),
                "permission_denied": str(attrs.get("cartwheel.permission_denied", "")).lower() == "true",
                "permission_reason": attrs.get("cartwheel.permission_denied.reason"),
                "start_time": _get(obs, "startTime", "start_time"),
            }
            # Parallel calls to the same tool arrive as several TOOL observations whose
            # order need not match the tool_call parts, so match on the arguments first.
            same_args = [s for s in pending if (s["tool_call"] or {}).get("name") == name and _same_args(s["tool_call"].get("arguments"), obs.get("input"))]
            same_name = [s for s in pending if (s["tool_call"] or {}).get("name") == name]
            target = (same_args or same_name or pending or [None])[0]
            if target is None:
                target = {
                    "narration": "",
                    "tool_call": {"name": name, "arguments": obs.get("input"), "call_id": None, "generation_id": None},
                    "tool_result": None,
                }
                steps.append(target)
            else:
                pending.remove(target)
                if target["tool_call"].get("arguments") is None:
                    target["tool_call"]["arguments"] = obs.get("input")
            target["tool_call"]["observation_id"] = obs.get("id")
            target["tool_result"] = result

    if not reply:
        reply = _messages_text(record.get("output"))

    html_path = _get(record, "htmlPath", "html_path")
    if not html_path:
        project = _get(record, "projectId", "project_id", default="")
        html_path = f"/project/{project}/traces/{trace_id}"
    url = f"{host.rstrip('/')}{html_path}" if host else None

    return {
        "trace_id": trace_id,
        "index": index,
        "timestamp": _get(record, "timestamp"),
        "latency": record.get("latency"),
        "tokens": tokens,
        "models": models,
        "generation_count": generation_count,
        "langfuse_url": url,
        "user": _messages_text(record.get("input")),
        "steps": steps,
        "reply": reply,
        "badges": _badges(steps),
        "tools": [s["tool_call"]["name"] for s in steps if s.get("tool_call")],
        "_system_prompt": system_prompt,
    }


def _context_lines(system_prompt: str) -> list[str]:
    """The session context lines injected into the system prompt."""
    lines: list[str] = []
    grab = False
    for line in system_prompt.splitlines():
        if line.startswith("## Session context"):
            grab = True
            continue
        if grab:
            if not line.strip():
                break
            lines.append(line.strip("- ").strip())
    return lines


def build_sessions(records: list[dict[str, Any]], scenarios: dict[str, dict[str, Any]], host: str | None) -> list[dict[str, Any]]:
    """Group trace records into ordered sessions with scenario metadata."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        attrs = _attrs(record.get("metadata"))
        session_id = str(attrs.get("cartwheel.session_id") or record.get("cartwheel_scenario_id") or _get(record, "id"))
        groups.setdefault(session_id, []).append(record)

    sessions: list[dict[str, Any]] = []
    for session_id, members in groups.items():
        members.sort(key=lambda r: str(_get(r, "timestamp", default="")))
        first_attrs = _attrs(members[0].get("metadata"))
        scenario_id = members[0].get("cartwheel_scenario_id") or first_attrs.get("cartwheel.scenario_id")
        scenario = scenarios.get(str(scenario_id), {})
        tup = scenario.get("tuple", {}) if isinstance(scenario, dict) else {}
        turns = [_build_turn(record, i + 1, host) for i, record in enumerate(members)]
        system_prompt = next((t["_system_prompt"] for t in turns if t["_system_prompt"]), "")
        for turn in turns:
            turn.pop("_system_prompt", None)
        badges: list[dict[str, str]] = []
        seen: set[str] = set()
        for turn in turns:
            for badge in turn["badges"]:
                if badge["text"] not in seen and badge["text"] != "no tools":
                    seen.add(badge["text"])
                    badges.append(badge)
        tools = list(dict.fromkeys(name for turn in turns for name in turn["tools"]))
        sessions.append(
            {
                "session_id": session_id,
                "scenario_id": scenario_id,
                "role": first_attrs.get("cartwheel.user_role") or tup.get("role"),
                "user_id": first_attrs.get("cartwheel.user_id") or tup.get("user_id"),
                "store_id": first_attrs.get("cartwheel.store_id"),
                "prompt_version": first_attrs.get("cartwheel.prompt_version"),
                "started_at": turns[0]["timestamp"],
                "turn_count": len(turns),
                "trace_ids": [t["trace_id"] for t in turns],
                "tools": tools,
                "badges": badges,
                "scenario": {
                    "scenario_group": scenario.get("scenario_group"),
                    "data_quality_case_id": scenario.get("data_quality_case_id"),
                    **{k: v for k, v in tup.items() if k not in ("role", "user_id")},
                },
                "expected": scenario.get("expected"),
                "system_prompt": {"context": _context_lines(system_prompt), "full": system_prompt},
                "turns": turns,
            }
        )
    sessions.sort(key=lambda s: str(s["started_at"] or ""))
    return sessions


def summarize(session: dict[str, Any]) -> dict[str, Any]:
    """The sidebar and labels view need everything except the turn bodies."""
    return {
        **{k: v for k, v in session.items() if k not in ("turns", "system_prompt", "expected")},
        "turns": [
            {
                "trace_id": t["trace_id"],
                "index": t["index"],
                "badges": t["badges"],
                "tools": t["tools"],
                "user": t["user"][:140],
                "langfuse_url": t["langfuse_url"],
            }
            for t in session["turns"]
        ],
    }


def load_all(source: str = "auto", refresh: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load records from the chosen source and build the session view model."""
    records, info = load_records(source=source, refresh=refresh)
    host = info.get("host") or os.environ.get("LANGFUSE_HOST")
    sessions = build_sessions(records, load_scenarios(), host)
    info["trace_count"] = sum(len(s["turns"]) for s in sessions)
    info["session_count"] = len(sessions)
    return sessions, info
