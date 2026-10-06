from modules.knowledge_graph.models import (
    EvidenceReference,
    GraphEdge,
    GraphNode,
    Workflow,
    WorkflowDependency,
    WorkflowStep,
)
from modules.knowledge_graph.storage import (
    deserialize_edge,
    deserialize_node,
    deserialize_workflow,
    encode_json,
    serialize_edge,
    serialize_node,
    serialize_workflow,
    serialize_workflow_dependency,
    serialize_workflow_step,
)


EVIDENCE = EvidenceReference(
    evidence_id="evidence_001",
    kind="response",
    path="evidence/semantic_analyzer/response.json",
    sha256="a" * 64,
    redacted=True,
)


def test_node_and_edge_round_trip_nested_json() -> None:
    node = GraphNode(
        node_id="resource_001",
        node_type="Resource",
        properties={"nested": {"items": [1, True, None]}, "한글": "값"},
        basis="inferred",
        evidence_refs=(EVIDENCE,),
    )
    edge = GraphEdge(
        relationship_id="relationship_001",
        source_id="account_001",
        target_id="resource_001",
        relation_type="OWNS",
        properties={"weights": [0.1, 0.2]},
        basis="observed",
        evidence_refs=(EVIDENCE,),
    )

    assert deserialize_node(serialize_node(node)) == node
    assert deserialize_edge(serialize_edge(edge)) == edge


def test_workflow_round_trip_and_dependency_identity_are_stable() -> None:
    workflow = Workflow(
        workflow_id="workflow_001",
        name="order flow",
        role_ids=("role_user",),
        steps=(
            WorkflowStep("step_pay", 1, "pay", ("request_pay",)),
            WorkflowStep("step_create", 0, "create", ("request_create",)),
        ),
        dependencies=(
            WorkflowDependency(
                "step_create",
                "step_pay",
                "order exists",
                "inferred",
                (EVIDENCE,),
            ),
        ),
        basis="inferred",
        evidence_refs=(EVIDENCE,),
    )
    workflow_value = serialize_workflow(workflow)
    step_values = [
        serialize_workflow_step(workflow.workflow_id, step) for step in workflow.steps
    ]
    dependency_values = [
        serialize_workflow_dependency(workflow.workflow_id, dependency, index)
        for index, dependency in enumerate(workflow.dependencies)
    ]

    restored = deserialize_workflow(workflow_value, step_values, dependency_values)

    assert restored.steps[0].step_id == "step_create"
    assert restored.dependencies == workflow.dependencies
    assert restored.role_ids == workflow.role_ids
    assert len(dependency_values[0]["dependency_id"]) == 64
    assert dependency_values[0] == serialize_workflow_dependency(
        workflow.workflow_id,
        workflow.dependencies[0],
        0,
    )


def test_canonical_json_is_order_independent() -> None:
    assert encode_json({"b": 2, "a": 1}) == encode_json({"a": 1, "b": 2})
