"""Build the Homework 4 review batches and write ``analysis/state/sample_manifest.json``.

The unit is one Langfuse trace (one user turn). A trace never belongs to more
than one batch: every command excludes the ids already in the manifest.

Commands (run from the repository root)::

    uv run python -m analysis.review_app.sample uniform   --n 15 --name B1-uniform
    uv run python -m analysis.review_app.sample cluster   --n 15 --name B1-cluster
    uv run python -m analysis.review_app.sample dimension --n 30 --name B2-intent \
        --dimension intent --secondary difficulty
    uv run python -m analysis.review_app.sample add --name B3-depth --ids ID... --reason "..."
    uv run python -m analysis.review_app.sample show

Every batch records its method, seed, parameters, the chosen ids, and a
one-line reason per id. Clustering reuses the standardization and k-means
helpers from ``analysis.helpers.selection`` over trace-level features.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import math
import random
from typing import Any

from analysis.helpers import _state, selection
from analysis.review_app import loader

DEFAULT_SEED = 41
MANIFEST = "sample_manifest.json"

WRITE_TOOLS = {"issue_refund", "cancel_order", "escalate_to_human"}
RETRIEVAL_TOOLS = {"search_help_center", "get_policy", "get_store_info"}
ROLES = ("shopper", "merchant", "support")


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def flat_traces() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One record per trace with its session context and numeric features."""
    sessions, info = loader.load_all()
    rows: list[dict[str, Any]] = []
    for s in sessions:
        for t in s["turns"]:
            tools = [st["tool_call"]["name"] for st in t["steps"] if st.get("tool_call")]
            errors = sum(
                1 for st in t["steps"]
                if isinstance((st.get("tool_result") or {}).get("output"), dict)
                and st["tool_result"]["output"].get("ok") is False
            )
            rows.append(
                {
                    "trace_id": t["trace_id"],
                    "session_id": s["session_id"],
                    "scenario_id": s["scenario_id"],
                    "role": s["role"],
                    "turn": t["index"],
                    "turn_count": s["turn_count"],
                    "scenario": s["scenario"],
                    "tools": tools,
                    "features": {
                        "tool_calls": len(t["steps"]),
                        "distinct_tools": len(set(tools)),
                        "retrieval": int(any(n in RETRIEVAL_TOOLS for n in tools)),
                        "write": int(any(n in WRITE_TOOLS for n in tools)),
                        "tool_errors": errors,
                        "tokens": t["tokens"]["total"],
                        "turn": t["index"],
                        "turn_count": s["turn_count"],
                        "user_words": len(t["user"].split()),
                        "reply_words": len(t["reply"].split()),
                        **{f"role_{r}": int(s["role"] == r) for r in ROLES},
                    },
                }
            )
    return rows, info


def load_manifest() -> dict[str, Any]:
    return _state.read_json(_state.state_path(MANIFEST), default={"batches": []})


def save_manifest(manifest: dict[str, Any]) -> None:
    _state.write_json(_state.state_path(MANIFEST), manifest)


def used_ids(manifest: dict[str, Any]) -> set[str]:
    return {tid for b in manifest.get("batches", []) for tid in b.get("trace_ids", [])}


def _batch(name: str, method: str, seed: int | None, picks: list[tuple[str, str]], **params: Any) -> dict[str, Any]:
    return {
        "name": name,
        "method": method,
        "n": len(picks),
        "seed": seed,
        "selected_at": _utcnow(),
        "params": params,
        "trace_ids": [tid for tid, _ in picks],
        "reasons": {tid: reason for tid, reason in picks},
    }


# ---------------------------------------------------------------------------
# selection methods
# ---------------------------------------------------------------------------


def pick_uniform(pool: list[dict[str, Any]], n: int, seed: int) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    chosen = rng.sample(pool, min(n, len(pool)))
    return [(r["trace_id"], "uniform random pick over all traces not yet in a batch") for r in chosen]


def pick_cluster(pool: list[dict[str, Any]], n: int, seed: int) -> list[tuple[str, str]]:
    """k-means with k = n over standardized trace features; one representative per
    cluster (nearest the centroid). Empty clusters are backfilled with the trace
    farthest from every pick so far (max-min diversity)."""
    names = list(pool[0]["features"].keys())
    vectors = selection._standardize([[float(r["features"][k]) for k in names] for r in pool])
    assign = selection._kmeans(vectors, k=n, seed=seed)
    members: dict[int, list[int]] = collections.defaultdict(list)
    for i, c in enumerate(assign):
        members[c].append(i)
    picks: list[tuple[str, str]] = []
    chosen: set[int] = set()
    for c in sorted(members):
        idx = members[c]
        centroid = [sum(vectors[i][d] for i in idx) / len(idx) for d in range(len(names))]
        best = min(idx, key=lambda i: sum((a - b) ** 2 for a, b in zip(vectors[i], centroid)))
        f = pool[best]["features"]
        picks.append(
            (
                pool[best]["trace_id"],
                f"cluster {c} representative, nearest centroid of {len(idx)} traces "
                f"(tool_calls={f['tool_calls']}, distinct_tools={f['distinct_tools']}, retrieval={f['retrieval']}, "
                f"write={f['write']}, errors={f['tool_errors']}, tokens={f['tokens']}, turn {f['turn']}/{f['turn_count']}, role={pool[best]['role']})",
            )
        )
        chosen.add(best)
    while len(picks) < n and len(chosen) < len(pool):
        far = max(
            (i for i in range(len(pool)) if i not in chosen),
            key=lambda i: min(sum((a - b) ** 2 for a, b in zip(vectors[i], vectors[j])) for j in chosen),
        )
        picks.append((pool[far]["trace_id"], "diversity backfill: farthest from every representative chosen so far"))
        chosen.add(far)
    return picks


def _dim_value(row: dict[str, Any], dimension: str) -> str:
    if dimension == "role":
        return str(row["role"])
    if dimension in ("turn", "turn_count"):
        return str(row[dimension])
    return str(row["scenario"].get(dimension))


def pick_dimension(
    pool: list[dict[str, Any]], n: int, seed: int, dimension: str, secondary: str | None
) -> list[tuple[str, str]]:
    """Spread ``n`` picks equally across the dimension's values. Inside each value,
    when a secondary dimension is given, round-robin across its values so the
    slots are not all drawn from the most common secondary value."""
    rng = random.Random(seed)
    by_value: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for r in pool:
        by_value[_dim_value(r, dimension)].append(r)
    values = sorted(by_value, key=lambda v: (-len(by_value[v]), v))
    base, extra = divmod(n, len(values))
    quota = {v: base + (1 if i < extra else 0) for i, v in enumerate(values)}
    # If a value has fewer traces than its quota, hand the surplus to the largest values.
    surplus = 0
    for v in values:
        if quota[v] > len(by_value[v]):
            surplus += quota[v] - len(by_value[v])
            quota[v] = len(by_value[v])
    for v in values:
        if surplus == 0:
            break
        room = len(by_value[v]) - quota[v]
        take = min(room, surplus)
        quota[v] += take
        surplus -= take

    picks: list[tuple[str, str]] = []
    for v in values:
        rows = by_value[v]
        if secondary:
            groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
            for r in rows:
                groups[_dim_value(r, secondary)].append(r)
            for g in groups.values():
                rng.shuffle(g)
            order = sorted(groups, key=lambda g: (len(groups[g]), g))  # rarest secondary values first
            ordered: list[dict[str, Any]] = []
            while any(groups[g] for g in order):
                for g in order:
                    if groups[g]:
                        ordered.append(groups[g].pop())
        else:
            ordered = rows[:]
            rng.shuffle(ordered)
        for r in ordered[: quota[v]]:
            reason = f"{dimension}={v}"
            if secondary:
                reason += f" ({secondary}={_dim_value(r, secondary)})"
            picks.append((r["trace_id"], reason + "; equal allocation across values, secondary spread"))
    return picks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _composition(rows_by_id: dict[str, dict[str, Any]], ids: list[str]) -> str:
    def count(key) -> str:
        c = collections.Counter(key(rows_by_id[t]) for t in ids)
        return ", ".join(f"{k}={v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], str(kv[0]))))
    return (
        f"    roles: {count(lambda r: r['role'])}\n"
        f"    intents: {count(lambda r: r['scenario'].get('intent'))}\n"
        f"    difficulty: {count(lambda r: r['scenario'].get('difficulty'))}\n"
        f"    tool calls: {count(lambda r: r['features']['tool_calls'])}\n"
        f"    turn position: {count(lambda r: f'{r['turn']}/{r['turn_count']}')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("uniform", "cluster", "dimension"):
        p = sub.add_parser(cmd)
        p.add_argument("--n", type=int, required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--seed", type=int, default=DEFAULT_SEED)
        p.add_argument("--dry-run", action="store_true")
        if cmd == "dimension":
            p.add_argument("--dimension", required=True)
            p.add_argument("--secondary", default=None)
    p = sub.add_parser("add", help="record hand-picked ids (depth searches) under a batch")
    p.add_argument("--name", required=True)
    p.add_argument("--method", default="depth_search")
    p.add_argument("--ids", nargs="+", required=True)
    p.add_argument("--reason", required=True)
    sub.add_parser("show")
    args = parser.parse_args()

    rows, info = flat_traces()
    rows_by_id = {r["trace_id"]: r for r in rows}
    manifest = load_manifest()
    manifest.setdefault("unit", "one Langfuse trace (one user turn); the interface shows the whole session")
    manifest["source"] = {k: info.get(k) for k in ("used", "path", "fetched_at", "trace_count", "session_count")}
    used = used_ids(manifest)

    if args.cmd == "show":
        print(f"{len(rows)} traces available, {len(used)} in {len(manifest['batches'])} batches")
        for b in manifest["batches"]:
            print(f"\n{b['name']}: {b['n']} traces, method={b['method']}, seed={b.get('seed')}, params={b.get('params')}")
            print(_composition(rows_by_id, b["trace_ids"]))
        return

    if any(b["name"] == args.name for b in manifest["batches"]):
        raise SystemExit(f"batch '{args.name}' already exists; pick another name")

    if args.cmd == "add":
        unknown = [i for i in args.ids if i not in rows_by_id]
        dup = [i for i in args.ids if i in used]
        if unknown or dup:
            raise SystemExit(f"unknown ids: {unknown}; already in a batch: {dup}")
        picks = [(i, args.reason) for i in dict.fromkeys(args.ids)]
        batch = _batch(args.name, args.method, None, picks)
    else:
        pool = [r for r in rows if r["trace_id"] not in used]
        if args.cmd == "uniform":
            picks = pick_uniform(pool, args.n, args.seed)
            batch = _batch(args.name, "uniform", args.seed, picks, pool_size=len(pool))
        elif args.cmd == "cluster":
            picks = pick_cluster(pool, args.n, args.seed)
            batch = _batch(args.name, "cluster_representatives", args.seed, picks, pool_size=len(pool), k=args.n,
                           features=list(pool[0]["features"].keys()))
        else:
            picks = pick_dimension(pool, args.n, args.seed, args.dimension, args.secondary)
            batch = _batch(args.name, "dimension", args.seed, picks, pool_size=len(pool), dimension=args.dimension,
                           secondary=args.secondary)
            batch["dimension"] = args.dimension

    print(f"{batch['name']}: {batch['n']} traces ({batch['method']})")
    print(_composition(rows_by_id, batch["trace_ids"]))
    overlap = set(batch["trace_ids"]) & used
    assert not overlap, f"overlap with earlier batches: {overlap}"
    if getattr(args, "dry_run", False):
        print("dry run, manifest not written")
        return
    manifest["batches"].append(batch)
    manifest["updated_at"] = _utcnow()
    save_manifest(manifest)
    print(f"written to {_state.state_path(MANIFEST)}")


if __name__ == "__main__":
    main()
