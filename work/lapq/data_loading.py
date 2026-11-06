"""Load the LAPQ paper's OSMnx GraphML while preserving its edge semantics.

The upstream city-graph Dijkstra loops over distinct neighbours and reads
``graph[u][v][0]['length']``. Consequently a parallel edge with key 1 must
not replace key 0, even when it is shorter. This loader deliberately mirrors
that choice. Coordinates are (longitude, latitude), in GraphML node order.
No rounding, scaling, symmetrisation, or minimum-edge aggregation is applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import xml.etree.ElementTree as ET


@dataclass
class GraphData:
    adj: list[list[tuple[int, float]]]
    node_ids: list[str]
    coords: list[tuple[float, float]]
    raw_edge_count: int
    retained_edge_count: int
    directed: bool
    metadata: dict[str, str]

    @property
    def n(self) -> int:
        return len(self.node_ids)

    @property
    def m(self) -> int:
        return self.retained_edge_count


def load_graphml(path: str | Path) -> GraphData:
    """Return an indexed graph matching upstream per-neighbour key-0 access.

    Raises ValueError for unsupported undirected input, duplicate node IDs,
    missing key-0 edges, malformed coordinates, or invalid edge lengths.
    """
    namespace = "{http://graphml.graphdrawing.org/xmlns}"
    root = ET.parse(path).getroot()
    keys = {
        key.attrib["id"]: key.attrib["attr.name"]
        for key in root.findall(namespace + "key")
    }

    def attributes(element: ET.Element) -> dict[str, str]:
        return {
            keys[item.attrib["key"]]: item.text or ""
            for item in element.findall(namespace + "data")
        }

    graph = root.find(namespace + "graph")
    if graph is None:
        raise ValueError("GraphML has no graph element")
    if graph.get("edgedefault") != "directed":
        raise ValueError("Only the upstream directed city-graph format is supported")

    node_ids: list[str] = []
    coords: list[tuple[float, float]] = []
    for node in graph.findall(namespace + "node"):
        node_ids.append(node.attrib["id"])
        values = attributes(node)
        point = (float(values["x"]), float(values["y"]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError("Non-finite node coordinate")
        coords.append(point)
    index = {node_id: i for i, node_id in enumerate(node_ids)}
    if len(index) != len(node_ids):
        raise ValueError("Duplicate node ID")

    # Dict insertion order preserves the first appearance of each neighbour,
    # exactly as NetworkX MultiDiGraph adjacency dictionaries do. The key-0
    # edge can appear after another parallel edge without changing that order.
    neighbours: list[dict[int, float | None]] = [{} for _ in node_ids]
    raw_edge_count = 0
    for edge in graph.findall(namespace + "edge"):
        raw_edge_count += 1
        if edge.get("directed") == "false":
            raise ValueError("Undirected edge inside directed graph")
        source = index[edge.attrib["source"]]
        target = index[edge.attrib["target"]]
        neighbours[source].setdefault(target, None)
        if edge.get("id") != "0":
            continue
        if neighbours[source][target] is not None:
            raise ValueError("Duplicate key-0 edge for one ordered vertex pair")
        length = float(attributes(edge)["length"])
        if not math.isfinite(length) or length < 0:
            raise ValueError("Length must be finite and nonnegative")
        neighbours[source][target] = length

    adj: list[list[tuple[int, float]]] = []
    for source, entries in enumerate(neighbours):
        row: list[tuple[int, float]] = []
        for target, length in entries.items():
            if length is None:
                raise ValueError(
                    f"Missing edge key 0 for {node_ids[source]} -> {node_ids[target]}"
                )
            row.append((target, length))
        adj.append(row)

    return GraphData(
        adj=adj,
        node_ids=node_ids,
        coords=coords,
        raw_edge_count=raw_edge_count,
        retained_edge_count=sum(map(len, adj)),
        directed=True,
        metadata=attributes(graph),
    )


def load_dimacs(path: str | Path) -> GraphData:
    """Read a DIMACS shortest-path graph with unchanged integer arc weights.

    Vertices 1..n become indices 0..n-1. Every arc and its file order are
    retained, including parallel arcs. Coordinates are absent (``coords=[]``).
    Consumers adapting this to a per-neighbour interface must yield all entries
    rather than constructing a dict that would collapse parallel arcs.
    """
    adj: list[list[tuple[int, float]]] | None = None
    expected_edges = None
    raw_edges = 0
    comments: list[str] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            fields = raw_line.split()
            if not fields:
                continue
            if fields[0] == "c":
                comments.append(raw_line[1:].strip())
                continue
            if fields[0] == "p":
                if adj is not None or len(fields) != 4 or fields[1] != "sp":
                    raise ValueError(f"Invalid problem header at line {line_number}")
                n, expected_edges = map(int, fields[2:])
                if n < 0 or expected_edges < 0:
                    raise ValueError("Negative graph size")
                adj = [[] for _ in range(n)]
                continue
            if fields[0] != "a" or len(fields) != 4 or adj is None:
                raise ValueError(f"Invalid arc line at {line_number}")
            source, target, weight = map(int, fields[1:])
            source -= 1
            target -= 1
            if not (0 <= source < len(adj) and 0 <= target < len(adj)):
                raise ValueError(f"Out-of-range vertex at line {line_number}")
            if weight < 0:
                raise ValueError(f"Negative weight at line {line_number}")
            raw_edges += 1
            adj[source].append((target, weight))
    if adj is None or raw_edges != expected_edges:
        raise ValueError(f"Arc count mismatch: {raw_edges} != {expected_edges}")
    return GraphData(
        adj=adj,
        node_ids=[str(i + 1) for i in range(len(adj))],
        coords=[],
        raw_edge_count=raw_edges,
        retained_edge_count=raw_edges,
        directed=True,
        metadata={"format": "DIMACS sp", "comments": "\n".join(comments),
                  "parallel_arc_policy": "preserve_all"},
    )
