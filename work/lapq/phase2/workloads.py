"""Frozen, deterministic workloads for the LAPQ cost study (stdlib only).

All vertex identifiers in adjacency/splits are zero-based integer indices.
GraphML deliberately follows the original artifact's per-neighbour key-0 policy;
DIMACS retains every arc, including duplicates. Synthetic edge order is frozen.
This module constructs inputs and certifies reference distances; it never times.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
import struct
import sys

HERE = Path(__file__).resolve().parent
LAPQ = HERE.parent
sys.path.insert(0, str(LAPQ))
from data_loading import GraphData, load_dimacs, load_graphml

DESIGN_SEED = 20261105
GENERATOR_VERSION = 1
MANIFEST = HERE / "workload_manifest.json"
BASE_SPECS = {
    "brussels": {"family": "released_osmnx", "path": "data/brussels.graphml"},
    "ny": {"family": "dimacs_road", "path": "../data/USA-road-d.NY.gr"},
    "grid_2025": {"family": "grid_integer", "n": 2025, "seed": 1001},
    "grid_8100": {"family": "grid_integer", "n": 8100, "seed": 1002},
    "geometric_2048": {"family": "geometric_float", "n": 2048, "seed": 2001},
    "geometric_8192": {"family": "geometric_float", "n": 8192, "seed": 2002},
    "sparse_2048": {"family": "sparse_integer", "n": 2048, "seed": 3001},
    "sparse_8192": {"family": "sparse_integer", "n": 8192, "seed": 3002},
}
SHIFT_BASES = ("brussels", "grid_2025", "geometric_2048", "sparse_2048")


def _seed(*parts) -> int:
    value = ":".join(map(str, parts)).encode()
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def _make_graph(adj, coords, family, seed) -> GraphData:
    n = len(adj)
    m = sum(map(len, adj))
    return GraphData(adj, list(map(str, range(n))), coords, m, m, True,
                     {"family": family, "generator_seed": str(seed),
                      "generator_version": str(GENERATOR_VERSION)})


def _grid(n: int, seed: int) -> GraphData:
    side = math.isqrt(n)
    assert side * side == n
    rng = random.Random(seed)
    adj = [[] for _ in range(n)]
    for u in range(n):
        x, y = u % side, u // side
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            xx, yy = x + dx, y + dy
            if 0 <= xx < side and 0 <= yy < side:
                # Independent directions, bounded integer costs and many ties.
                adj[u].append((yy * side + xx, rng.randint(1, 16)))
    return _make_graph(adj, [(u % side, u // side) for u in range(n)],
                       "grid_integer", seed)


def _geometric(n: int, seed: int) -> GraphData:
    """Symmetrized exact 8-nearest-neighbour Euclidean graph on uniform points.

    Spatial bins speed construction, without scipy. Rare disconnected components
    are joined to vertex 0 by a nearest-to-0 representative, explicitly recorded.
    """
    rng = random.Random(seed)
    coords = [(rng.random(), rng.random()) for _ in range(n)]
    side = math.ceil(math.sqrt(n))
    cells = defaultdict(list)
    for u, (x, y) in enumerate(coords):
        cells[(int(x * side), int(y * side))].append(u)
    neighbours = [set() for _ in range(n)]
    for u, (x, y) in enumerate(coords):
        cx, cy = int(x * side), int(y * side)
        candidates = []
        radius = -1
        while True:
            radius += 1
            for xx in range(max(0, cx-radius), min(side-1, cx+radius)+1):
                for yy in range(max(0, cy-radius), min(side-1, cy+radius)+1):
                    if max(abs(xx-cx), abs(yy-cy)) != radius:
                        continue
                    for v in cells.get((xx, yy), ()):
                        if v != u:
                            vx, vy = coords[v]
                            candidates.append(((x-vx)**2 + (y-vy)**2, v))
            if len(candidates) < 8:
                continue
            closest = heapq.nsmallest(8, candidates)
            bounds = []
            if cx-radius > 0: bounds.append(x-(cx-radius)/side)
            if cx+radius+1 < side: bounds.append((cx+radius+1)/side-x)
            if cy-radius > 0: bounds.append(y-(cy-radius)/side)
            if cy+radius+1 < side: bounds.append((cy+radius+1)/side-y)
            if not bounds or closest[-1][0] <= min(bounds)**2:
                for _, v in closest:
                    neighbours[u].add(v)
                    neighbours[v].add(u)
                break
    unseen = set(range(n))
    components = []
    while unseen:
        start = min(unseen)
        stack, component = [start], []
        unseen.remove(start)
        while stack:
            u = stack.pop()
            component.append(u)
            for v in neighbours[u]:
                if v in unseen:
                    unseen.remove(v)
                    stack.append(v)
        components.append(component)
    added = []
    for component in components[1:]:
        v = min(component, key=lambda v: (math.dist(coords[0], coords[v]), v))
        neighbours[0].add(v)
        neighbours[v].add(0)
        added.append([0, v])
    adj = [[(v, 10000.0 * math.dist(coords[u], coords[v]))
            for v in sorted(neighbours[u])] for u in range(n)]
    graph = _make_graph(adj, coords, "geometric_float", seed)
    graph.metadata["component_bridges"] = json.dumps(added)
    return graph


def _sparse(n: int, seed: int) -> GraphData:
    rng = random.Random(seed)
    adj = []
    for u in range(n):
        # Bidirectional ring guarantees strong connectivity; four distinct random
        # outgoing arcs add shortcuts. No duplicate pairs or self loops.
        targets = [(u+1) % n, (u-1) % n]
        while len(targets) < 6:
            v = rng.randrange(n)
            if v != u and v not in targets:
                targets.append(v)
        adj.append([(v, rng.randint(1, 1024)) for v in targets])
    return _make_graph(adj, [], "sparse_integer", seed)


def _parse_name(name: str):
    if "__" not in name:
        return name, None
    base, transform = name.split("__", 1)
    if base not in SHIFT_BASES or transform not in ("scale025", "scale4", "lopsided"):
        raise KeyError(name)
    if transform == "lopsided" and base != "sparse_2048":
        raise KeyError(name)
    return base, transform


def load(name: str) -> GraphData:
    """Return fresh graph objects, including named target-distribution shifts."""
    base, transform = _parse_name(name)
    spec = BASE_SPECS[base]
    if base == "brussels":
        graph = load_graphml(LAPQ / spec["path"])
    elif base == "ny":
        graph = load_dimacs(LAPQ / spec["path"])
    else:
        generator = {"grid_integer": _grid, "geometric_float": _geometric,
                     "sparse_integer": _sparse}[spec["family"]]
        graph = generator(spec["n"], spec["seed"])
    if transform:
        if transform in ("scale025", "scale4"):
            scale = 0.25 if transform == "scale025" else 4
            graph.adj = [[(v, w * scale) for v, w in row] for row in graph.adj]
        else:
            # Two cost regimes with a four-order-of-magnitude separation.
            # This is a declared stress input, not a claim of realistic road costs.
            boundary = graph.n // 4
            graph.adj = [[(v, w if u < boundary and v < boundary else w * 4096)
                          for v, w in row] for u, row in enumerate(graph.adj)]
        graph.metadata["transform"] = transform
        graph.metadata["training_graph"] = base
    graph.metadata["workload_name"] = name
    return graph


def _all_names():
    names = list(BASE_SPECS)
    names += [base + "__" + transform for base in SHIFT_BASES
              for transform in ("scale025", "scale4")]
    names.append("sparse_2048__lopsided")
    return names


def graph_hash(graph: GraphData) -> str:
    """SHA256 of typed adjacency and order; ignores presentation metadata."""
    h = hashlib.sha256(struct.pack(">QQ", graph.n, graph.m))
    for u, row in enumerate(graph.adj):
        h.update(struct.pack(">QQ", u, len(row)))
        for v, weight in row:
            h.update(struct.pack(">Q", v))
            h.update(("i:" + str(weight) if type(weight) is int else
                      "f:" + float(weight).hex()).encode() + b";")
    return h.hexdigest()


def reference_distances(graph: GraphData, source: int):
    """heapq reference plus independent optimality certificate checks."""
    distances = [math.inf] * graph.n
    parents = [None] * graph.n
    distances[source] = 0
    heap = [(0, source)]
    while heap:
        distance, u = heapq.heappop(heap)
        if distance != distances[u]:
            continue
        for v, weight in graph.adj[u]:
            candidate = distance + weight
            if candidate < distances[v]:
                distances[v] = candidate
                parents[v] = (u, weight)
                heapq.heappush(heap, (candidate, v))
    # Every finite distance has an attainable parent path, and no edge can
    # improve a distance. Positive input weights make parent distances decrease.
    for v, distance in enumerate(distances):
        if v != source and math.isfinite(distance):
            u, weight = parents[v]
            assert distances[u] + weight == distance
            assert distances[u] < distance
    for u, row in enumerate(graph.adj):
        for v, weight in row:
            assert distances[v] <= distances[u] + weight
    return distances


def _make_splits(name: str, seed: int, graph: GraphData):
    rng = random.Random(_seed(seed, name, "splits"))
    trains = rng.sample(range(graph.n), 3)
    used = set(trains)
    groups_per_train = 1 if name == "ny" else 2
    splits = []
    for train in trains:
        distances = reference_distances(graph, train)
        reachable = sorted((d, v) for v, d in enumerate(distances)
                           if math.isfinite(d) and v != train)
        quarter = max(1, len(reachable) // 4)
        queries = []
        for label, pool in (("near", reachable[:quarter]),
                            ("far", reachable[-quarter:])):
            eligible = [v for _, v in pool if v not in used]
            selected = rng.sample(eligible, groups_per_train)
            for v in selected:
                queries.append({"source": v, "group": label})
                used.add(v)
        splits.append({"train_source": train, "queries": queries})
    return splits


def select_training_and_queries(name: str, seed: int = DESIGN_SEED):
    """Use frozen manifest when present; shifted targets reuse base splits."""
    base, _ = _parse_name(name)
    if seed == DESIGN_SEED and MANIFEST.exists():
        return json.loads(MANIFEST.read_text())["workloads"][base]["splits"]
    return _make_splits(base, seed, load(base))


def workload_catalog():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())["workloads"]
    return {name: {"training_graph": _parse_name(name)[0]}
            for name in _all_names()}


def _distance_hash(distances):
    h = hashlib.sha256()
    for d in distances:
        h.update(("i:" + str(d) if type(d) is int else
                  "f:" + float(d).hex()).encode() + b";")
    return h.hexdigest()


def build_manifest():
    manifest = {"protocol_version": 1, "design_seed": DESIGN_SEED,
                "generator_version": GENERATOR_VERSION,
                "performance_timings_collected": False,
                "workloads": {}}
    for name in _all_names():
        graph = load(name)
        base, transform = _parse_name(name)
        weights = [w for row in graph.adj for _, w in row]
        assert len(weights) == graph.m
        assert all(math.isfinite(w) and w > 0 for w in weights)
        assert all(0 <= v < graph.n for row in graph.adj for v, _ in row)
        splits = (manifest["workloads"][base]["splits"] if transform else
                  _make_splits(base, DESIGN_SEED, graph))
        references = {}
        sources = sorted({split["train_source"] for split in splits} |
                         {q["source"] for split in splits for q in split["queries"]})
        for source in sources:
            distances = reference_distances(graph, source)
            finite = [d for d in distances if math.isfinite(d)]
            references[str(source)] = {"sha256": _distance_hash(distances),
                "reachable": len(finite), "max_distance": max(finite)}
        record = {"name": name, "family": BASE_SPECS[base]["family"],
            "training_graph": base, "transform": transform,
            "n": graph.n, "m": graph.m, "raw_edges": graph.raw_edge_count,
            "adjacency_sha256": graph_hash(graph), "min_weight": min(weights),
            "max_weight": max(weights),
            "all_weights_int_type": all(type(w) is int for w in weights),
            "all_weights_integral_value": all(float(w).is_integer() for w in weights),
            "n_times_max_weight_bound": graph.n * max(weights),
            "metadata": graph.metadata, "splits": splits,
            "reference_distances": references}
        if "path" in BASE_SPECS[base]:
            path = LAPQ / BASE_SPECS[base]["path"]
            record["source_path_relative_to_lapq"] = BASE_SPECS[base]["path"]
            record["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["workloads"][name] = record
        print(json.dumps({"validated": name, "n": graph.n, "m": graph.m,
                          "reference_sources": len(sources)}), flush=True)
    manifest["generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-manifest", action="store_true")
    args = parser.parse_args()
    if args.build_manifest:
        build_manifest()
    else:
        print(json.dumps(workload_catalog(), indent=2))
