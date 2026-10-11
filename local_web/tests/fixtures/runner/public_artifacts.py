"""Web 표시 전용 공개 계약 fixture. 실제 모듈·LLM·대상 요청을 실행하지 않는다."""

import hashlib
import json


def public_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def make_artifacts(run_id: str, target_url: str, case: str = "completed") -> dict[str, dict]:
    result = {}
    producers = {"crawl_result": "collector", "semantic_analysis": "semantic_analyzer",
                 "graph_query_result": "knowledge_graph", "vulnerability_candidates": "access_analyzer",
                 "test_scenarios": "scenario_generator", "safety_decisions": "safety_policy",
                 "verification_results": "verifier", "diagnosis_report": "reporter"}

    def publish(name: str, data: dict, references: tuple[str, ...] = ()) -> None:
        status = "failed" if name == "crawl_result" and case == "failed" else "partial" if name == "semantic_analysis" and case == "partial" else "completed"
        result[name] = {"schema_version": "0.2.0", "artifact_type": name, "artifact_id": name + "_" + run_id,
                        "run_id": run_id, "iteration": 0, "producer": producers[name], "mode": "development",
                        "created_at": "2026-10-12T00:00:00Z", "status": status,
                        "input_refs": [{"artifact_id": result[ref]["artifact_id"], "artifact_type": ref, "iteration": 0,
                                        "path": "artifacts/iteration-000/" + producers[ref] + "/" + ref + ".json",
                                        "sha256": hashlib.sha256(public_bytes(result[ref])).hexdigest()} for ref in references],
                        "errors": [] if status == "completed" else [{"code": "TEST_ERROR", "message": "PASSWORD_SENTINEL", "item_ref": None, "retryable": False}],
                        "runtime_metrics": None, "data": None if status == "failed" else data}

    evidence = {"evidence_id": "evidence-k", "kind": "response", "path": "evidence/verifier/response.json",
                "sha256": "b" * 64, "redacted": True}
    publish("crawl_result", {"target_url": target_url, "roles": [{"role_id": "role-q", "name": "reviewer"}],
                             "accounts": [{"account_id": "actor-z", "role_id": "role-q", "alias": "probe_z", "session_ref": "PRIVATE_SESSION_SENTINEL"}],
                             "pages": [{"page_id": "page-j", "account_id": "actor-z", "url": target_url,
                                        "title": "Fixture page", "evidence_refs": []}], "actions": [], "requests": []})
    if case == "failed":
        return result
    nodes = [{"node_id": node_id, "node_type": node_type, "properties": {"secret": "PASSWORD_SENTINEL"},
              "basis": "observed", "evidence_refs": []}
             for node_type, node_id in (("User", "actor-z"), ("Role", "role-q"), ("Page", "page-j"),
                                       ("Action", "action-p"), ("Endpoint", "endpoint-m"), ("Parameter", "parameter-t"))]
    for scope, node_id in (("type", "resource-kind-n"), ("instance", "opaque-instance-v")):
        nodes.append({"node_id": node_id, "node_type": "Resource", "basis": "inferred", "evidence_refs": [],
                      "properties": {"resource_key": "document", "resource_scope": scope,
                                     "match_key": None if scope == "type" else {"resource_key": "document", "identifiers": [{"key": "document_key", "value": "47"}]}}})
    edges = [{"relationship_id": "relation-w", "source_id": "actor-z", "target_id": "opaque-instance-v",
              "relation_type": "ACCESS", "properties": {"secret": "PASSWORD_SENTINEL"}, "basis": "observed", "evidence_refs": []}]
    workflow = {"workflow_id": "workflow-h", "name": "Document flow", "role_ids": ["role-q"],
                "steps": [{"step_id": "later", "order": 1, "action": "read", "request_ids": []},
                          {"step_id": "earlier", "order": 0, "action": "open", "request_ids": []}],
                "dependencies": [{"before_step_id": "earlier", "after_step_id": "later", "condition": "Open before read",
                                  "basis": "inferred", "evidence_refs": []}], "basis": "inferred", "evidence_refs": []}
    publish("semantic_analysis", {"normalized_requests": [], "nodes": nodes, "relationships": edges,
                                  "workflows": [workflow], "model_info": None}, ("crawl_result",))
    publish("graph_query_result", {"graph_id": run_id, "graph_revision": 2,
                                   "results": [{"query_id": "query-unrelated", "query_key": "resource_ownership", "status": "completed", "rows": [], "errors": []},
                                               {"query_id": "query-snapshot", "query_key": "structure_snapshot", "status": "completed", "rows": [{"nodes": nodes, "relationships": edges, "workflows": [workflow]}], "errors": []}]}, ("semantic_analysis",))
    cases = (("alpha", "allow", "success", "completed", "confirmed"),
             ("delta", "require_approval", "blocked", "not_executed", "approval_pending"),
             ("omega", "allow", "indeterminate", "error", "indeterminate"))
    candidates, scenarios, decisions, verifications, findings = [], [], [], [], []
    for suffix, decision, verification, execution, finding_status in cases:
        candidate_id, scenario_id = "candidate-" + suffix, "scenario-" + suffix
        candidates.append({"candidate_id": candidate_id, "category": "authorization", "vulnerability_type": "horizontal_access",
                           "rule_id": "rule-x", "actor_account_id": "actor-z", "actor_role_id": "role-q", "reference_account_id": "owner-r",
                           "resource_ids": ["opaque-instance-v"], "source_request_ids": ["request-u"], "workflow_id": None,
                           "hypothesis": "Cross-account document access", "expected_behavior": "Owner-only access", "expected_basis": "rule", "evidence_refs": []})
        scenarios.append({"scenario_id": scenario_id, "candidate_id": candidate_id, "expected_basis": "rule",
                          "resource_ids": ["opaque-instance-v"], "preconditions": [{"check_id": "session-check", "kind": "session_valid", "subject_ref": "actor-z", "selector": None, "operator": "eq", "expected": True}],
                          "steps": [{"step_id": "step-k", "order": 0, "source_request_id": "request-u", "account_id": "actor-z", "role_id": "role-q", "session_ref": "PRIVATE_SESSION_SENTINEL",
                                     "request": {"method": "GET", "url_template": target_url, "parameters": [{"name": "secret", "location": "query", "value": "PASSWORD_SENTINEL", "binding_ref": None}], "body_ref": None},
                                     "bindings": [], "state_change": "unknown"}],
                          "assertions": [{"check_id": "status-check", "kind": "response_status", "subject_ref": "step-k", "selector": None, "operator": "eq", "expected": 200}]})
        decisions.append({"decision_id": "decision-" + suffix, "scenario_id": scenario_id, "decision": decision,
                          "reason_codes": ["STATE_CHANGE_UNKNOWN"] if decision == "require_approval" else [], "reason": "Analyst approval required" if decision == "require_approval" else "Public policy reason",
                          "assessment": {name: {"status": "require_approval" if name == "state_change" and decision == "require_approval" else "pass", "rule_id": "policy-rule", "reason": "Policy assessment"}
                                         for name in ("target_scope", "test_accounts", "request_budget", "state_change", "data_impact", "service_impact")},
                          "effective_origins": [target_url], "effective_account_ids": ["actor-z"],
                          "limits": {"max_requests": 2, "max_duration_ms": 5000, "allow_state_change": False}, "approval_ref": None})
        verifications.append({"verification_id": "verification-" + suffix, "candidate_id": candidate_id, "scenario_id": scenario_id,
                              "decision_id": "decision-" + suffix, "policy_decision": decision, "execution_status": execution, "result": verification,
                              "reason": "Verification public reason", "steps": [], "evidence_refs": [evidence] if finding_status == "confirmed" else [], "errors": []})
        findings.append({"finding_id": "finding-" + suffix, "candidate_id": candidate_id, "scenario_id": scenario_id,
                         "verification_id": "verification-" + suffix, "category": "authorization", "vulnerability_type": "horizontal_access", "status": finding_status,
                         "title": "Document authorization test " + suffix + (" <img src=x onerror='window.artifactXss=1'>" if suffix == "alpha" else ""),
                         "description": "Reporter public description", "evidence_refs": [evidence] if finding_status == "confirmed" else []})
    publish("vulnerability_candidates", {"source_graph_revision": 2, "candidates": candidates, "model_info": None}, ("graph_query_result",))
    publish("test_scenarios", {"scenarios": list(reversed(scenarios)), "model_info": None}, ("vulnerability_candidates",))
    scenarios_hash = hashlib.sha256(public_bytes(result["test_scenarios"])).hexdigest()
    publish("safety_decisions", {"policy_id": "policy-test", "policy_version": "test-v2", "scenarios_sha256": scenarios_hash, "decisions": decisions}, ("test_scenarios",))
    publish("verification_results", {"source_graph_revision": 2, "scenarios_sha256": scenarios_hash, "results": list(reversed(verifications)),
                                     "graph_updates": {"source_verification_ids": [], "nodes": [], "relationships": []}}, ("test_scenarios", "safety_decisions"))
    publish("diagnosis_report", {"target_url": target_url, "summary": {"candidate_count": 3, "confirmed_count": 1, "not_confirmed_count": 0,
                                                                      "suspected_count": 0, "indeterminate_count": 1, "policy_blocked_count": 0, "approval_pending_count": 1},
                                 "findings": findings, "limitations": ["Approval pending and indeterminate scenarios remain."]}, ("vulnerability_candidates", "test_scenarios", "safety_decisions", "verification_results"))
    return result
