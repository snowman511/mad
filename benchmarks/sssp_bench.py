#!/usr/bin/env python3
"""SSSP benchmark harness — the Route-A declare-and-measure template.

Contract (ScriptVerifier-compatible, self-contained: copy this file and swap
the workload for your own problem):

  stdin  <- JSON context from the orchestrator:
            {"task": ..., "candidate": {"id", "config": {...}|str, "body", ...}, ...}
  stdout -> JSON result:
            {"passed": bool, "score": float|None, "summary": str,
             "measured": {...}, "mismatch": {...}|null,
             "reason": "no_score" (when nothing measurable was declared)}

Agents DECLINE to measure. They post an [EXPERIMENT_RESULT] whose body carries
a fenced ```json``` config; the harness runs it and owns the truth:

    {"impl": "bidirectional", "expected": {"speedup": 3.0}}

Rules baked in here:
  1. The workload (graph family, size, seed, repeats) is FIXED by the harness —
     a config may only choose the algorithm and its parameters. Otherwise the
     optimizer games the metric by shrinking the workload.
  2. The candidate must return the same distance as the baseline, or the run
     is marked incorrect and fails regardless of speed.
  3. Overclaiming: any `expected` value more than TOL above its measured value
     flags a mismatch -> the orchestrator records an immutable COUNTEREXAMPLE.
  4. score = baseline_time / candidate_time (>1 means faster than baseline).

`--selftest` cross-checks all implementations against each other.

Env overrides for tests / machines: MAD_SSSP_N, MAD_SSSP_DEGREE,
MAD_SSSP_MAX_W, MAD_SSSP_SEED, MAD_SSSP_REPEATS.
"""

from __future__ import annotations

import heapq
import json
import os
import random
import re
import sys
import time

# ----------------------------------------------------------------- workload
# Fixed by the harness on purpose: the workload is not a free variable.

N = max(100, min(50000, int(os.environ.get("MAD_SSSP_N", "1200"))))
DEGREE = max(2, min(32, int(os.environ.get("MAD_SSSP_DEGREE", "4"))))
MAX_W = max(1, int(os.environ.get("MAD_SSSP_MAX_W", "100")))
SEED = int(os.environ.get("MAD_SSSP_SEED", "7"))
REPEATS = max(1, min(10, int(os.environ.get("MAD_SSSP_REPEATS", "3"))))

BASELINE_IMPL = "dijkstra_heap"
TOL = 0.30  # overclaim tolerance: claimed may exceed measured by at most 30%

# Correctness battery: (n, degree, seed) graphs run BEFORE timing. Deliberately
# different from the timed workload (different seeds, sizes, sparsity), plus a
# degenerate s==t pair — "correct on the benchmark graph" is not "correct".
BATTERY_GRAPHS = [(N, DEGREE, SEED + 1), (max(120, N // 2), DEGREE, SEED + 2), (150, 2, SEED + 3)]
BATTERY_PAIRS_PER_GRAPH = 3


def gen_graph(n: int, degree: int, max_w: int, seed: int):
    """Deterministic random sparse digraph. Returns (forward, reverse) adjacency."""
    rng = random.Random(seed)
    adj = [[] for _ in range(n)]
    radj = [[] for _ in range(n)]
    for u in range(n):
        for v in rng.sample(range(n), min(degree, n)):
            if v == u:
                continue
            w = rng.randint(1, max_w)
            adj[u].append((v, w))
            radj[v].append((u, w))
    return adj, radj


# -------------------------------------------------------------- algorithms
# Every implementation answers: shortest-path distance from s to t (-1 = none).

def dijkstra_naive(adj, s, t, early_exit=False):
    n = len(adj)
    INF = float("inf")
    dist = [INF] * n
    done = [False] * n
    dist[s] = 0
    for _ in range(n):
        u, best = -1, INF
        for v in range(n):
            if not done[v] and dist[v] < best:
                u, best = v, dist[v]
        if u < 0 or best == INF:
            break
        done[u] = True
        if early_exit and u == t:
            return dist[t]
        for v, w in adj[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
    return dist[t] if dist[t] < INF else -1


def dijkstra_heap(adj, s, t, early_exit=False):
    n = len(adj)
    INF = float("inf")
    dist = [INF] * n
    dist[s] = 0
    heap = [(0, s)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        if early_exit and u == t:
            return d
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist[t] if dist[t] < INF else -1


def bidirectional(adj, radj, s, t):
    """Bidirectional Dijkstra. Stop when top_f + top_b >= mu, where mu is the
    best s-t path over vertices settled (popped) by one side and reached by
    the other — the standard safe criterion."""
    n = len(adj)
    INF = float("inf")
    df = [INF] * n
    db = [INF] * n
    df[s] = 0
    db[t] = 0
    hf = [(0, s)]
    hb = [(0, t)]
    mu = INF
    while hf and hb:
        if hf[0][0] + hb[0][0] >= mu:
            break
        if hf[0][0] <= hb[0][0]:
            d, u = heapq.heappop(hf)
            if d > df[u]:
                continue
            if db[u] < INF:
                mu = min(mu, d + db[u])
            for v, w in adj[u]:
                nd = d + w
                if nd < df[v]:
                    df[v] = nd
                    heapq.heappush(hf, (nd, v))
        else:
            d, u = heapq.heappop(hb)
            if d > db[u]:
                continue
            if df[u] < INF:
                mu = min(mu, d + df[u])
            for v, w in radj[u]:
                nd = d + w
                if nd < db[v]:
                    db[v] = nd
                    heapq.heappush(hb, (nd, v))
    return -1 if mu == INF else mu


IMPLS = {
    "dijkstra_naive": lambda adj, radj, s, t, cfg: dijkstra_naive(adj, s, t, early_exit=False),
    "dijkstra_naive_early": lambda adj, radj, s, t, cfg: dijkstra_naive(adj, s, t, early_exit=True),
    "dijkstra_heap": lambda adj, radj, s, t, cfg: dijkstra_heap(adj, s, t, early_exit=False),
    "dijkstra_heap_early": lambda adj, radj, s, t, cfg: dijkstra_heap(adj, s, t, early_exit=True),
    "bidirectional": lambda adj, radj, s, t, cfg: bidirectional(adj, radj, s, t),
}

# Keys a config may set. Anything else (n, seed, ...) is harness-owned and ignored.
ALLOWED_CONFIG_KEYS = {"impl", "expected", "note"}


# ------------------------------------------------------------------ harness

def correctness_battery(cfg: dict) -> dict | None:
    """Compare the candidate against the baseline on randomized graphs the
    timed workload never shows it, including the s==t degenerate pair.
    Returns a failure record, or None when every pair agrees."""
    fn = IMPLS[cfg["impl"]]
    for n, degree, seed in BATTERY_GRAPHS:
        adj, radj = gen_graph(n, degree, MAX_W, seed)
        rng = random.Random(seed * 31 + 7)
        pairs = [(0, n - 1), (0, 0)]
        while len(pairs) < BATTERY_PAIRS_PER_GRAPH:
            pairs.append((rng.randrange(n), rng.randrange(n)))
        for s, t in pairs:
            expected = dijkstra_heap(adj, s, t, early_exit=False)
            got = fn(adj, radj, s, t, cfg)
            if got != expected:
                return {
                    "graph": {"n": n, "degree": degree, "seed": seed},
                    "s": s, "t": t, "got": got, "expected": expected,
                }
    return None


def measure(cfg: dict) -> dict:
    # correctness first: never spend timing effort on a wrong algorithm
    battery_err = correctness_battery(cfg)
    if battery_err is not None:
        return {
            "impl": cfg["impl"],
            "correct": False,
            "correctness_battery": battery_err,
            "speedup": 0.0,
        }

    adj, radj = gen_graph(N, DEGREE, MAX_W, SEED)
    s, t = 0, N - 1

    t0 = time.perf_counter()
    for _ in range(REPEATS):
        base = dijkstra_heap(adj, s, t, early_exit=False)
    base_ms = (time.perf_counter() - t0) / REPEATS * 1000

    fn = IMPLS[cfg["impl"]]
    t0 = time.perf_counter()
    for _ in range(REPEATS):
        result = fn(adj, radj, s, t, cfg)
    cand_ms = (time.perf_counter() - t0) / REPEATS * 1000

    out = {
        "impl": cfg["impl"],
        "workload": {"n": N, "degree": DEGREE, "max_w": MAX_W, "seed": SEED, "repeats": REPEATS},
        "correctness_battery": {"graphs": len(BATTERY_GRAPHS), "pairs": len(BATTERY_GRAPHS) * BATTERY_PAIRS_PER_GRAPH, "ok": True},
        "dist": result,
        "baseline_dist": base,
        "correct": result == base and base >= 0,
        "speedup": round(base_ms / max(cand_ms, 1e-9), 4),
        "time_ms": round(cand_ms, 3),
        "baseline_ms": round(base_ms, 3),
    }
    return out


def compare_expected(expected, measured: dict) -> dict | None:
    """Overclaim detection: expected value > measured * (1 + TOL) is a mismatch.
    Underclaiming (honest conservatism) is never penalized."""
    if not isinstance(expected, dict):
        return None
    for key, claimed in expected.items():
        if key not in measured:
            continue
        got = measured[key]
        if isinstance(claimed, (int, float)) and isinstance(got, (int, float)):
            if claimed > got * (1 + TOL) + 1e-12:
                return {"key": key, "claimed": claimed, "measured": got, "tolerance": TOL}
    return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_config(candidate: dict) -> dict | None:
    """Pull the declared config from candidate metadata or its body text."""
    cfg = candidate.get("config")
    if isinstance(cfg, dict):
        return cfg
    for text in (cfg if isinstance(cfg, str) else None, candidate.get("body", "")):
        if not text:
            continue
        blocks = _JSON_FENCE_RE.findall(text)
        for raw in reversed(blocks):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def selftest() -> int:
    for seed in (1, 2, 3):
        n, degree = 120, 3
        adj, radj = gen_graph(n, degree, 20, seed)
        for s, t in ((0, n - 1), (0, n // 2), (5, 90)):
            ref = dijkstra_heap(adj, s, t, early_exit=False)
            checks = {
                "naive": dijkstra_naive(adj, s, t),
                "naive_early": dijkstra_naive(adj, s, t, early_exit=True),
                "heap_early": dijkstra_heap(adj, s, t, early_exit=True),
                "bidir": bidirectional(adj, radj, s, t),
            }
            for name, got in checks.items():
                if got != ref:
                    print(f"selftest FAIL: seed={seed} s={s} t={t} {name}: {got} != {ref}")
                    return 1
    print("selftest OK: all implementations agree on every checked pair")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if "--selftest" in sys.argv:
        return selftest()

    def emit(payload: dict) -> None:
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")

    try:
        context = json.load(sys.stdin)
    except json.JSONDecodeError:
        emit({"passed": False, "summary": "harness received non-JSON stdin", "reason": "no_score"})
        return 0

    candidate = context.get("candidate") or {}
    cfg = extract_config(candidate)
    if not cfg or "impl" not in cfg:
        emit({
            "passed": False,
            "summary": "no runnable config declared (need a ```json block with an `impl` key)",
            "reason": "no_score",
        })
        return 0

    if cfg["impl"] not in IMPLS:
        emit({
            "passed": False,
            "summary": f"unknown impl {cfg['impl']!r}; known: {sorted(IMPLS)}",
            "reason": "no_score",
        })
        return 0

    unknown = set(cfg) - ALLOWED_CONFIG_KEYS
    if unknown:
        emit({
            "passed": False,
            "summary": f"config may only set {sorted(ALLOWED_CONFIG_KEYS)}; got {sorted(unknown)} "
                       f"(workload is harness-owned)",
            "reason": "no_score",
        })
        return 0

    try:
        measured = measure(cfg)
    except Exception as exc:  # a crashing benchmark must not kill the session
        emit({"passed": False, "summary": f"benchmark crashed: {exc}", "reason": "no_score"})
        return 0

    if not measured.get("correct"):
        detail = measured.get("correctness_battery") or {}
        if detail and "got" in detail:
            wrong = (
                f"WRONG on graph {detail['graph']} pair (s={detail['s']},t={detail['t']}): "
                f"got {detail['got']}, baseline {detail['expected']}"
            )
        else:
            wrong = f"WRONG DISTANCE {measured.get('dist')} (baseline {measured.get('baseline_dist')})"
        emit({
            "passed": False,
            "score": None,
            "summary": f"{cfg['impl']}: {wrong}",
            "measured": measured,
            "mismatch": {"key": "correctness", "claimed": cfg["impl"], "measured": "wrong distance"},
        })
        return 0

    mismatch = compare_expected(cfg.get("expected"), measured)
    score = float(measured["speedup"])
    summary = f"{cfg['impl']}: measured speedup {score} (baseline {measured['baseline_ms']}ms vs {measured['time_ms']}ms)"
    if mismatch:
        summary += f" — OVERCLAIMED: expected {mismatch['claimed']}, measured {mismatch['measured']}"
    emit({"passed": mismatch is None, "score": score, "summary": summary,
          "measured": measured, "mismatch": mismatch})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
