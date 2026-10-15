"""Comparison-only snapshot views; actual node IDs and edges remain authoritative."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from modules.reporter.matching import (
    ActorMatcher,
    has_parameter_match_fields,
    normalize_text,
    parameter_comparison_properties,
)
from modules.reporter.models import GraphSnapshot


@dataclass(frozen=True)
class RelationshipScore:
    matched_count: int
    source_unmatched_count: int
    target_unmatched_count: int
    both_unmatched_count: int
    missing_relationship_count: int

    def to_note(self) -> str:
        return (
            f"관계 매칭: 일치 {self.matched_count}개, "
            f"출발 노드 미매칭 {self.source_unmatched_count}개, "
            f"도착 노드 미매칭 {self.target_unmatched_count}개, "
            f"양쪽 노드 미매칭 {self.both_unmatched_count}개, "
            f"노드 일치·관계 미매칭 {self.missing_relationship_count}개"
        )


@dataclass(frozen=True)
class _NodeView:
    node_id: str
    node_type: str
    properties: Mapping[str, Any]
    endpoint_id: str | None = None


def score_relationships(
    snapshot: GraphSnapshot,
    entities: Sequence[Mapping[str, Any]],
    relationships: Sequence[Mapping[str, Any]],
    actor_matcher: ActorMatcher,
) -> RelationshipScore:
    views = _snapshot_views(snapshot)
    matches_by_gt_id = {
        entity["gt_id"]: tuple(
            view for view in views
            if view.node_type == entity["entity_type"]
            and actor_matcher.matches_entity(
                entity["entity_type"], entity["match_key"], view.properties,
            )
        )
        for entity in entities
    }
    edges = {
        (item["source_id"], item["target_id"], normalize_text(item["relation_type"]))
        for item in snapshot.relationships
    }
    counts: Counter[str] = Counter()
    for expected in relationships:
        sources = matches_by_gt_id[expected["source_gt_id"]]
        targets = matches_by_gt_id[expected["target_gt_id"]]
        if not sources and not targets:
            counts["both"] += 1
        elif not sources:
            counts["source"] += 1
        elif not targets:
            counts["target"] += 1
        elif _has_matching_edge(sources, targets, expected["relation_type"], edges):
            counts["matched"] += 1
        else:
            counts["edge"] += 1
    return RelationshipScore(
        counts["matched"], counts["source"], counts["target"], counts["both"], counts["edge"],
    )


def _snapshot_views(snapshot: GraphSnapshot) -> tuple[_NodeView, ...]:
    views: list[_NodeView] = []
    nodes_by_id = {node["node_id"]: node for node in snapshot.nodes}
    for node in snapshot.nodes:
        properties = dict(node["properties"])
        if node["node_type"] == "Page" and "path" not in properties:
            url = properties.get("url")
            if isinstance(url, str) and url.strip():
                # matches_key applies the existing path normalization to this view.
                properties["path"] = url
        views.append(_NodeView(node["node_id"], node["node_type"], properties))
    for edge in snapshot.relationships:
        if edge["relation_type"] != "USE":
            continue
        endpoint = nodes_by_id.get(edge["source_id"])
        parameter = nodes_by_id.get(edge["target_id"])
        if (
            endpoint is None or endpoint["node_type"] != "Endpoint"
            or parameter is None or parameter["node_type"] != "Parameter"
            or not has_parameter_match_fields(endpoint["properties"], parameter["properties"])
        ):
            continue
        properties = {
            **parameter["properties"],
            **parameter_comparison_properties(endpoint["properties"], parameter["properties"]),
        }
        views.append(_NodeView(parameter["node_id"], "Parameter", properties, endpoint["node_id"]))
    return tuple(views)


def _has_matching_edge(
    sources: Sequence[_NodeView],
    targets: Sequence[_NodeView],
    relation_type: str,
    edges: set[tuple[str, str, str]],
) -> bool:
    normalized_type = normalize_text(relation_type)
    return any(
        (source.node_id, target.node_id, normalized_type) in edges
        # A shared Parameter must not borrow another Endpoint's USE context.
        and (normalized_type != "use" or target.endpoint_id in {None, source.node_id})
        for source in sources for target in targets
    )
