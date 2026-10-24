#!/usr/bin/env python3
"""[v1 — runtime saat ini] Validate ``agents/registry.yaml`` (schema_version 1) against PRD §6, ADR-012, docs/TOOL_CATALOG.md and agent contracts.

Usage::

    python3 scripts/validate_registry_v1.py [path/to/registry.yaml]

Exit codes: 0 = valid, 1 = validation errors, 2 = file/usage error.

Rules (see docs/AGENT_CATALOG.md §2 for the narrative version):
  * schema: ``schema_version == 1``; every entry has exactly the documented fields with valid types/enums;
  * identity: 42 entries, unique ``agent_id`` and ``slug``; all 41 PRD specialist ids + ``ORCH`` present;
    ``name``/``group``/``capabilities`` match PRD §6 verbatim (embedded ``PRD_CATALOG``);
  * mode: ``enabled`` is true iff ``mvp_mode != planned``; ``runtime_agent`` only (and always) for ``profile``
    and must point to an ``llm`` agent; the set of ``llm`` agents equals the keys of ``OUTPUT_CONTRACTS``
    in backend/app/agents/contracts.py; contracts match that module (profile agents inherit their runtime's);
  * tools: subset of ``TOOL_CATALOG``; ``submit_result`` required for llm/profile, forbidden otherwise;
    tools disabled in MVP (``git.push``, ``deploy.staging``, ``deps.install``) never on enabled agents;
  * permissions: ``approval_grant == deny`` everywhere; every tool's permission precondition holds
    (e.g. no write tools for ``read_only``, no ``tests.run`` without ``sandbox: allow``);
    PRD-pinned profiles (reviewer/user proxy read-only, C17 test_only, G35 docs_only, ...) are enforced;
  * limits: ``1 <= max_iterations <= 40``; llm/profile ``timeout_seconds`` within 300..900.

The script depends only on PyYAML and the standard library (contracts.py is parsed with ``ast``, not imported).
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "agents" / "registry.yaml"
CONTRACTS_PY = REPO_ROOT / "backend" / "app" / "agents" / "contracts.py"

# --------------------------------------------------------------------------------------------- enums
GROUPS = {
    "planning_intent",
    "software_development",
    "testing_debugging",
    "quality_security",
    "git_collaboration",
    "devops_operations",
    "documentation_knowledge",
    "specialized_engineering",
    "orchestration",
}
PRIORITIES = {"P0", "P1", "P2"}
MVP_MODES = {"llm", "profile", "tool", "planned"}
MODEL_TIERS = {"fast", "default", "complex"}
PERMISSION_VALUES: dict[str, set[str]] = {
    "filesystem": {"none", "read_only", "test_only", "docs_only", "scoped_write"},
    "sandbox": {"deny", "allow"},
    "git": {"none", "read", "branch_commit"},
    "network": {"deny", "allowlist"},
    "deployment": {"deny", "staging_with_approval"},
    "approval_grant": {"deny"},
}
REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    "agent_id": str,
    "slug": str,
    "name": str,
    "group": str,
    "version": int,
    "priority": str,
    "mvp_mode": str,
    "runtime_agent": (str, type(None)),
    "enabled": bool,
    "model_tier": str,
    "description": str,
    "purpose": str,
    "triggers": list,
    "capabilities": list,
    "tools": list,
    "permissions": dict,
    "input_contract": (str, type(None)),
    "output_contract": (str, type(None)),
    "limits": dict,
    "inputs_tools_note": str,
    "output_note": str,
    "access_note": str,
}

# ------------------------------------------------------------------------- docs/TOOL_CATALOG.md (MVP)
# name -> (risk, precondition key). Keep in sync with docs/TOOL_CATALOG.md "Builtin tools (MVP)".
TOOL_CATALOG: dict[str, tuple[str, str]] = {
    "repo.get_tree": ("R0", "fs_read"),
    "repo.read_file": ("R0", "fs_read"),
    "repo.search_text": ("R0", "fs_read"),
    "repo.search_symbol": ("R0", "fs_read"),
    "repo.detect_stack": ("R0", "none"),
    "repo.find_related_tests": ("R0", "fs_read"),
    "git.status": ("R0", "git_read"),
    "git.diff": ("R0", "git_read"),
    "git.log": ("R0", "git_read"),
    "tests.list_profiles": ("R0", "none"),
    "tests.get_run": ("R0", "none"),
    "review.get_task_diff": ("R0", "none"),
    "acceptance.list_criteria": ("R0", "none"),
    "acceptance.list_evidence": ("R0", "none"),
    "memory.retrieve_context": ("R0", "none"),
    "security.scan_secrets": ("R0", "none"),
    "code.write_file": ("R1", "fs_write"),
    "code.apply_patch": ("R1", "fs_write"),
    "code.delete_file": ("R1", "fs_scoped_write"),
    "git.commit_task": ("R1", "git_branch_commit"),
    "memory.store_decision": ("R1", "none"),
    "tests.run": ("R2", "sandbox"),
    "env.inspect_runtime": ("R2", "sandbox"),
    "deps.install": ("R2", "sandbox_network"),
    "git.push": ("R3", "git_branch_commit"),
    "deploy.staging": ("R3", "deployment"),
    "submit_result": ("-", "llm_only"),
}
DISABLED_IN_MVP = {"git.push", "deploy.staging", "deps.install"}

# Fallback if backend/app/agents/contracts.py is not present (it is the binding source when it exists).
FALLBACK_CONTRACTS: dict[str, tuple[str, str]] = {
    "A02": ("IntakeInput", "IntakeOutput"),
    "A03": ("PlanInput", "PlanOutput"),
    "B14": ("ImplementInput", "ImplementOutput"),
    "C20": ("DebugInput", "DebugOutput"),
    "D22": ("ReviewInput", "ReviewOutput"),
    "A07": ("UserProxyInput", "UserProxyOutput"),
}

# Permission fields pinned by PRD access notes / AC-10 / TOOL_CATALOG semantics.
PINNED_PERMISSIONS: dict[str, dict[str, str]] = {
    "A01": {"filesystem": "read_only"},
    "A02": {"filesystem": "read_only"},
    "A03": {"filesystem": "read_only"},  # "orchestration only"
    "A04": {"filesystem": "read_only"},
    "A05": {"filesystem": "read_only"},
    "A06": {"filesystem": "read_only"},
    "A07": {"filesystem": "read_only", "sandbox": "deny", "deployment": "deny"},  # cannot give human approval
    "C16": {"filesystem": "read_only"},
    "C17": {"filesystem": "test_only"},
    "D22": {"filesystem": "read_only", "sandbox": "deny"},  # AC-10: default reviewer read-only
    "D23": {"filesystem": "read_only"},
    "D26": {"filesystem": "read_only"},
    "F33": {"filesystem": "read_only"},
    "G35": {"filesystem": "docs_only"},
    "G36": {"filesystem": "docs_only"},
    "B14": {"filesystem": "scoped_write", "deployment": "deny"},  # "no direct deploy"
    "E27": {"git": "branch_commit"},
}
ONLY_DEPLOYER = {"F32"}

ID_RE = re.compile(r"^(?:[A-H]\d{2}|ORCH)$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CAP_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
TRIGGER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
CONTRACT_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")

# ------------------------------------------------------------------- PRD §6 verbatim (mechanical copy)
# id -> (name, group, space-separated capability functions in PRD order)
PRD_CATALOG: dict[str, tuple[str, str, str]] = {
    "A01": (
        "Request & Intent Classifier",
        "planning_intent",
        (
            "intent.classify intent.detect_stack intent.estimate_complexity intent.identify_constraints "
            "intent.select_workflow"
        ),
    ),
    "A02": (
        "Requirement Analyst",
        "planning_intent",
        (
            "requirements.extract requirements.generate_acceptance_criteria "
            "requirements.identify_edge_cases requirements.check_completeness "
            "requirements.generate_test_scenarios"
        ),
    ),
    "A03": (
        "Planner & Task Decomposer",
        "planning_intent",
        (
            "plan.decompose plan.define_dependencies plan.identify_parallel_tasks plan.assign_specialists "
            "plan.generate_execution_graph"
        ),
    ),
    "A04": (
        "Codebase Explorer & Context Engineer",
        "planning_intent",
        (
            "repo.get_tree repo.search_text repo.search_symbol repo.read_file repo.detect_entrypoints "
            "repo.build_dependency_map repo.find_related_tests"
        ),
    ),
    "A05": (
        "Software Architect",
        "planning_intent",
        (
            "architecture.propose architecture.define_modules architecture.define_interfaces "
            "architecture.define_api_contracts architecture.evaluate_tradeoffs architecture.create_adr"
        ),
    ),
    "A06": (
        "Change Impact & Risk Analyst",
        "planning_intent",
        (
            "impact.map_affected_files impact.trace_dependencies impact.detect_breaking_changes "
            "impact.estimate_regression_risk impact.identify_required_tests impact.create_rollback_plan"
        ),
    ),
    "A07": (
        "User Proxy / User Intent & Acceptance",
        "planning_intent",
        (
            "user_proxy.capture_intent user_proxy.manage_requirements "
            "user_proxy.generate_acceptance_criteria user_proxy.validate_implementation "
            "user_proxy.validate_user_journey user_proxy.review_visual_output "
            "user_proxy.detect_requirement_gap user_proxy.issue_acceptance_report"
        ),
    ),
    "B08": (
        "UI/UX Designer",
        "software_development",
        (
            "design.analyze_brief design.create_user_flow design.define_wireframe "
            "design.define_components design.define_design_tokens design.check_responsive_layout"
        ),
    ),
    "B09": (
        "Frontend Developer",
        "software_development",
        (
            "frontend.create_component frontend.create_page frontend.configure_routing "
            "frontend.manage_state frontend.integrate_api frontend.handle_loading_error "
            "frontend.optimize_rendering"
        ),
    ),
    "B10": (
        "Backend Developer",
        "software_development",
        (
            "backend.create_endpoint backend.implement_service backend.validate_input "
            "backend.handle_errors backend.implement_background_job backend.configure_middleware "
            "backend.optimize_endpoint"
        ),
    ),
    "B11": (
        "API & Integration Specialist",
        "software_development",
        (
            "api.define_contract api.generate_openapi api.implement_client api.integrate_external_service "
            "api.validate_compatibility api.configure_retry_timeout api.verify_webhooks"
        ),
    ),
    "B12": (
        "Database & Data Modeling Specialist",
        "software_development",
        (
            "db.design_schema db.define_relationships db.create_query db.optimize_query "
            "db.analyze_query_plan db.recommend_indexes db.validate_integrity"
        ),
    ),
    "B13": (
        "Authentication & Identity Specialist",
        "software_development",
        (
            "auth.implement_login auth.configure_session auth.implement_rbac auth.configure_oauth "
            "auth.review_authorization auth.implement_account_recovery auth.audit_identity_flows"
        ),
    ),
    "B14": (
        "General Code Implementation Agent",
        "software_development",
        (
            "code.create_file code.apply_patch code.modify_function code.implement_feature "
            "code.refactor_scope code.resolve_type_errors code.validate_patch"
        ),
    ),
    "B15": (
        "AI/LLM Application Specialist",
        "software_development",
        (
            "ai.configure_provider ai.create_prompt_template ai.implement_tool_calling "
            "ai.define_structured_output ai.implement_rag ai.evaluate_model_output ai.track_token_cost "
            "ai.configure_fallback"
        ),
    ),
    "C16": (
        "Test Strategy Agent",
        "testing_debugging",
        (
            "test.plan test.define_test_matrix test.map_requirements_to_tests test.identify_coverage_gaps "
            "test.prioritize_regression_tests"
        ),
    ),
    "C17": (
        "Unit Testing Agent",
        "testing_debugging",
        (
            "tests.generate_unit_tests tests.run_unit_tests tests.analyze_unit_failures "
            "tests.identify_boundary_cases tests.check_assertion_quality tests.report_coverage"
        ),
    ),
    "C18": (
        "Integration & API Testing Agent",
        "testing_debugging",
        (
            "tests.generate_integration_tests tests.run_api_tests tests.verify_contracts "
            "tests.test_database_integration tests.simulate_dependency_failure "
            "tests.validate_authentication_flows"
        ),
    ),
    "C19": (
        "E2E & Browser QA Agent",
        "testing_debugging",
        (
            "browser.open_page browser.click_element browser.fill_form browser.capture_screenshot "
            "browser.collect_console_errors browser.inspect_network_failures browser.run_user_journey "
            "browser.compare_visual_state"
        ),
    ),
    "C20": (
        "Debugger & Root Cause Analyst",
        "testing_debugging",
        (
            "debug.parse_stack_trace debug.analyze_logs debug.reproduce_issue debug.identify_root_cause "
            "debug.propose_patch debug.compare_before_after debug.validate_fix"
        ),
    ),
    "C21": (
        "Environment & Build Troubleshooter",
        "testing_debugging",
        (
            "env.inspect_runtime env.detect_missing_dependency build.run build.diagnose_failure "
            "build.resolve_configuration build.check_version_compatibility"
        ),
    ),
    "D22": (
        "Code Review Agent",
        "quality_security",
        (
            "review.inspect_diff review.detect_logic_bug review.check_conventions "
            "review.detect_duplication review.analyze_regression_risk review.verify_acceptance_criteria "
            "review.generate_findings"
        ),
    ),
    "D23": (
        "Application Security Agent",
        "quality_security",
        (
            "security.scan_secrets security.review_authorization security.review_input_validation "
            "security.detect_injection_risks security.review_file_access security.analyze_threat_model "
            "security.review_security_headers"
        ),
    ),
    "D24": (
        "Dependency & Supply Chain Agent",
        "quality_security",
        (
            "deps.inventory deps.audit_vulnerabilities deps.check_licenses deps.detect_unused_packages "
            "deps.propose_upgrade deps.validate_lockfile deps.check_transitive_dependencies"
        ),
    ),
    "D25": (
        "Performance & Scalability Agent",
        "quality_security",
        (
            "performance.profile_application performance.analyze_query performance.detect_bottleneck "
            "performance.review_memory_usage performance.design_load_test performance.compare_benchmark"
        ),
    ),
    "D26": (
        "Accessibility & Compatibility Agent",
        "quality_security",
        (
            "a11y.scan a11y.check_keyboard_navigation a11y.review_semantics a11y.check_contrast "
            "compatibility.test_browser_matrix compatibility.validate_responsive"
        ),
    ),
    "E27": (
        "Git Workflow Agent",
        "git_collaboration",
        (
            "git.inspect_status git.inspect_diff git.create_task_branch git.inspect_history "
            "git.prepare_commit git.generate_commit_message git.inspect_untracked_files"
        ),
    ),
    "E28": (
        "Merge Conflict Resolution Agent",
        "git_collaboration",
        (
            "git.detect_conflicts git.explain_conflict git.propose_resolution git.apply_resolution "
            "git.validate_merge_result git.run_post_merge_tests"
        ),
    ),
    "E29": (
        "Pull Request & Collaboration Agent",
        "git_collaboration",
        (
            "collaboration.link_issue_to_task collaboration.prepare_pr_description "
            "collaboration.summarize_changes collaboration.generate_review_checklist "
            "collaboration.collect_review_feedback collaboration.map_feedback_to_tasks"
        ),
    ),
    "F30": (
        "Build & CI Agent",
        "devops_operations",
        (
            "ci.generate_pipeline ci.validate_config ci.run_local_checks ci.analyze_pipeline_failure "
            "ci.configure_test_stages ci.verify_artifacts"
        ),
    ),
    "F31": (
        "Infrastructure as Code Agent",
        "devops_operations",
        (
            "infra.generate_dockerfile infra.generate_compose infra.generate_iac "
            "infra.validate_configuration infra.review_resource_limits infra.check_network_policy "
            "infra.plan_environment_changes"
        ),
    ),
    "F32": (
        "Deployment & Release Agent",
        "devops_operations",
        (
            "release.prepare release.validate_artifact release.run_preflight release.deploy_staging "
            "release.run_smoke_tests release.prepare_rollback release.deploy_production"
        ),
    ),
    "F33": (
        "Observability & SRE Agent",
        "devops_operations",
        (
            "observability.query_logs observability.inspect_metrics observability.inspect_traces "
            "observability.detect_anomaly observability.evaluate_slo observability.propose_alert_rule "
            "observability.correlate_release_with_errors"
        ),
    ),
    "F34": (
        "Incident Response & Recovery Agent",
        "devops_operations",
        (
            "incident.classify_severity incident.correlate_events incident.identify_recent_changes "
            "incident.propose_mitigation incident.prepare_rollback incident.verify_recovery "
            "incident.generate_postmortem"
        ),
    ),
    "G35": (
        "Technical Documentation Agent",
        "documentation_knowledge",
        (
            "docs.generate_readme docs.generate_setup_guide docs.document_architecture "
            "docs.document_environment docs.update_changelog docs.verify_instructions"
        ),
    ),
    "G36": (
        "API & Developer Documentation Agent",
        "documentation_knowledge",
        (
            "docs.generate_api_reference docs.generate_request_examples docs.document_error_codes "
            "docs.generate_sdk_guide docs.check_contract_consistency docs.update_api_examples"
        ),
    ),
    "G37": (
        "Project Knowledge & Memory Agent",
        "documentation_knowledge",
        (
            "memory.index_repository memory.retrieve_context memory.summarize_decisions "
            "memory.store_architecture_decision memory.retrieve_task_history memory.refresh_index "
            "memory.detect_stale_context"
        ),
    ),
    "H38": (
        "Data Engineering Agent",
        "specialized_engineering",
        (
            "data.design_pipeline data.implement_transform data.validate_quality "
            "data.configure_scheduling data.check_idempotency data.profile_dataset "
            "data.generate_data_tests"
        ),
    ),
    "H39": (
        "Migration Specialist",
        "specialized_engineering",
        (
            "migration.analyze_source_target migration.map_schema migration.generate_migration_script "
            "migration.plan_backfill migration.run_dry_run migration.validate_data_integrity "
            "migration.prepare_rollback"
        ),
    ),
    "H40": (
        "Legacy Modernization & Refactoring Agent",
        "specialized_engineering",
        (
            "refactor.assess_legacy_code refactor.identify_code_smells refactor.propose_safe_steps "
            "refactor.extract_module refactor.remove_dead_code refactor.update_patterns "
            "refactor.verify_behavior"
        ),
    ),
    "H41": (
        "Language & Framework Specialist",
        "specialized_engineering",
        (
            "framework.detect_stack framework.load_language_profile framework.resolve_build_commands "
            "framework.check_idioms framework.check_version_compatibility "
            "framework.resolve_framework_errors framework.recommend_project_structure"
        ),
    ),
    "ORCH": (
        "Orchestrator Agent",
        "orchestration",
        (
            "orchestrator.route_intent orchestrator.create_workflow orchestrator.schedule_task "
            "orchestrator.resolve_dependencies orchestrator.invoke_agent orchestrator.enforce_budget "
            "orchestrator.handle_retry orchestrator.pause_resume orchestrator.collect_evidence "
            "orchestrator.request_approval orchestrator.finalize_task"
        ),
    ),
}


def load_contracts(path: Path = CONTRACTS_PY) -> dict[str, tuple[str, str]]:
    """Return {agent_id: (input_class, output_class)} parsed from contracts.py via ``ast`` (no import)."""
    if not path.is_file():
        return dict(FALLBACK_CONTRACTS)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    maps: dict[str, dict[str, str]] = {}
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        ):
            target, value = node.targets[0].id, node.value
        if target in {"OUTPUT_CONTRACTS", "INPUT_CONTRACTS"} and isinstance(value, ast.Dict):
            m: dict[str, str] = {}
            for k, v in zip(value.keys, value.values, strict=True):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                    m[str(k.value)] = v.id
            maps[target] = m
    out_map = maps.get("OUTPUT_CONTRACTS", {})
    in_map = maps.get("INPUT_CONTRACTS", {})
    return {aid: (in_map.get(aid, ""), out_cls) for aid, out_cls in out_map.items()}


def _check_tool_precondition(tool: str, perms: dict[str, str]) -> str | None:
    pre = TOOL_CATALOG[tool][1]
    fs, sandbox, git = perms.get("filesystem"), perms.get("sandbox"), perms.get("git")
    if pre == "fs_read" and fs == "none":
        return "requires filesystem != none"
    if pre == "fs_write" and fs not in {"test_only", "docs_only", "scoped_write"}:
        return "requires filesystem in {test_only, docs_only, scoped_write}"
    if pre == "fs_scoped_write" and fs != "scoped_write":
        return "requires filesystem = scoped_write"
    if pre == "git_read" and git not in {"read", "branch_commit"}:
        return "requires git >= read"
    if pre == "git_branch_commit" and git != "branch_commit":
        return "requires git = branch_commit"
    if pre == "sandbox" and sandbox != "allow":
        return "requires sandbox = allow"
    if pre == "sandbox_network" and (sandbox != "allow" or perms.get("network") != "allowlist"):
        return "requires sandbox = allow and network = allowlist"
    if pre == "deployment" and perms.get("deployment") != "staging_with_approval":
        return "requires deployment = staging_with_approval"
    return None


def validate(data: Any, contracts: dict[str, tuple[str, str]] | None = None) -> list[str]:
    """Return a list of human-readable error messages (empty list = valid)."""
    contracts = load_contracts() if contracts is None else contracts
    errors: list[str] = []
    err = errors.append

    if not isinstance(data, dict):
        return ["registry root must be a mapping with keys 'schema_version' and 'agents'"]
    extra_root = set(data) - {"schema_version", "agents"}
    if extra_root:
        err(f"unknown top-level keys: {sorted(extra_root)}")
    if data.get("schema_version") != 1:
        err(f"schema_version must be 1 (got {data.get('schema_version')!r})")
    agents = data.get("agents")
    if not isinstance(agents, list):
        return [*errors, "'agents' must be a list"]

    by_id: dict[str, dict[str, Any]] = {}
    ids = Counter()
    slugs = Counter()
    for idx, a in enumerate(agents):
        where = f"agents[{idx}]"
        if not isinstance(a, dict):
            err(f"{where}: entry must be a mapping")
            continue
        aid = a.get("agent_id")
        where = f"{aid or where}"
        missing = [k for k in REQUIRED_FIELDS if k not in a]
        unknown = [k for k in a if k not in REQUIRED_FIELDS]
        if missing:
            err(f"{where}: missing fields {missing}")
        if unknown:
            err(f"{where}: unknown fields {unknown}")
        for k, typ in REQUIRED_FIELDS.items():
            if k in a and not isinstance(a[k], typ):
                err(f"{where}: field '{k}' has wrong type {type(a[k]).__name__}")
        if not isinstance(aid, str) or not ID_RE.match(aid):
            err(f"{where}: agent_id must match {ID_RE.pattern}")
            continue
        ids[aid] += 1
        slug = a.get("slug")
        if isinstance(slug, str):
            slugs[slug] += 1
            if not SLUG_RE.match(slug):
                err(f"{aid}: slug '{slug}' must be snake_case")
        by_id.setdefault(aid, a)

    for aid, n in ids.items():
        if n > 1:
            err(f"duplicate agent_id '{aid}' ({n} entries)")
    for slug, n in slugs.items():
        if n > 1:
            err(f"duplicate slug '{slug}' ({n} entries)")
    expected_ids = set(PRD_CATALOG)
    missing_ids = sorted(expected_ids - set(by_id))
    unknown_ids = sorted(set(by_id) - expected_ids)
    if missing_ids:
        err(f"missing PRD agents: {missing_ids}")
    if unknown_ids:
        err(f"agent ids not in PRD §6: {unknown_ids}")
    if len(agents) != len(expected_ids):
        err(f"expected {len(expected_ids)} entries (41 specialists + ORCH), found {len(agents)}")

    llm_ids = {aid for aid, a in by_id.items() if a.get("mvp_mode") == "llm"}
    if llm_ids != set(contracts):
        err(
            "llm agents must equal OUTPUT_CONTRACTS keys in contracts.py: "
            f"registry={sorted(llm_ids)} contracts={sorted(contracts)}"
        )

    for aid, a in by_id.items():
        _validate_entry(aid, a, by_id, contracts, err)
    return errors


def _validate_entry(
    aid: str,
    a: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    contracts: dict[str, tuple[str, str]],
    err: Any,
) -> None:
    # ---- PRD verbatim fields
    if aid in PRD_CATALOG:
        name, group, caps = PRD_CATALOG[aid]
        if a.get("name") != name:
            err(f"{aid}: name {a.get('name')!r} != PRD {name!r}")
        if a.get("group") != group:
            err(f"{aid}: group {a.get('group')!r} != PRD {group!r}")
        if a.get("capabilities") != caps.split():
            err(f"{aid}: capabilities differ from PRD §6 (must be verbatim, same order)")
    # ---- enums & scalars
    if a.get("group") not in GROUPS:
        err(f"{aid}: invalid group {a.get('group')!r}")
    if a.get("priority") not in PRIORITIES:
        err(f"{aid}: invalid priority {a.get('priority')!r}")
    mode = a.get("mvp_mode")
    if mode not in MVP_MODES:
        err(f"{aid}: invalid mvp_mode {mode!r}")
    if a.get("model_tier") not in MODEL_TIERS:
        err(f"{aid}: invalid model_tier {a.get('model_tier')!r}")
    if not isinstance(a.get("version"), int) or isinstance(a.get("version"), bool) or a.get("version", 0) < 1:
        err(f"{aid}: version must be an integer >= 1")
    for k in ("name", "description", "purpose", "inputs_tools_note", "output_note", "access_note"):
        if isinstance(a.get(k), str) and not a[k].strip():
            err(f"{aid}: '{k}' must not be empty")
    enabled = a.get("enabled")
    if isinstance(enabled, bool) and enabled != (mode != "planned"):
        err(f"{aid}: enabled must be {mode != 'planned'} for mvp_mode={mode}")
    # ---- lists
    caps = a.get("capabilities") or []
    if not caps:
        err(f"{aid}: capabilities must not be empty")
    for c in caps:
        if not isinstance(c, str) or not CAP_RE.match(c):
            err(f"{aid}: capability {c!r} must look like 'namespace.function'")
    if len(set(map(str, caps))) != len(caps):
        err(f"{aid}: duplicate capabilities")
    triggers = a.get("triggers") or []
    if not triggers:
        err(f"{aid}: triggers must not be empty")
    for t in triggers:
        if not isinstance(t, str) or not TRIGGER_RE.match(t):
            err(f"{aid}: trigger {t!r} must be snake_case")
    # ---- runtime_agent / contracts
    rt = a.get("runtime_agent")
    if mode == "profile":
        target = by_id.get(rt) if isinstance(rt, str) else None
        if target is None:
            err(f"{aid}: profile agent needs runtime_agent pointing to an existing agent (got {rt!r})")
        elif target.get("mvp_mode") != "llm":
            err(f"{aid}: runtime_agent {rt} must have mvp_mode=llm (has {target.get('mvp_mode')})")
        exp = contracts.get(rt) if isinstance(rt, str) else None
        if exp and (a.get("input_contract"), a.get("output_contract")) != exp:
            err(f"{aid}: profile contracts must equal runtime {rt}'s {exp}")
    elif rt is not None:
        err(f"{aid}: runtime_agent must be null for mvp_mode={mode}")
    if mode == "llm":
        exp = contracts.get(aid)
        if exp is None:
            err(f"{aid}: llm agent has no entry in OUTPUT_CONTRACTS")
        elif (a.get("input_contract"), a.get("output_contract")) != exp:
            err(
                f"{aid}: contracts {(a.get('input_contract'), a.get('output_contract'))} != contracts.py {exp}"
            )
    if mode in {"tool", "planned"} and (
        a.get("input_contract") is not None or a.get("output_contract") is not None
    ):
        err(f"{aid}: input_contract/output_contract must be null for mvp_mode={mode}")
    for k in ("input_contract", "output_contract"):
        v = a.get(k)
        if isinstance(v, str) and not CONTRACT_RE.match(v):
            err(f"{aid}: {k} {v!r} is not a class name")
    # ---- permissions
    perms = a.get("permissions")
    if not isinstance(perms, dict):
        return
    if set(perms) != set(PERMISSION_VALUES):
        err(f"{aid}: permissions must have exactly {sorted(PERMISSION_VALUES)} (got {sorted(perms)})")
    for k, allowed in PERMISSION_VALUES.items():
        if k in perms and perms[k] not in allowed:
            if k == "approval_grant":
                err(f"{aid}: approval_grant must be 'deny' (PRD §10.4) — got {perms[k]!r}")
            else:
                err(f"{aid}: permissions.{k}={perms[k]!r} not in {sorted(allowed)}")
    for k, v in PINNED_PERMISSIONS.get(aid, {}).items():
        if perms.get(k) != v:
            err(f"{aid}: permissions.{k} must be {v!r} per PRD access note (got {perms.get(k)!r})")
    if perms.get("deployment") != "deny" and aid not in ONLY_DEPLOYER:
        err(f"{aid}: only {sorted(ONLY_DEPLOYER)} may have deployment != deny")
    # ---- tools
    tools = a.get("tools") or []
    if len(set(map(str, tools))) != len(tools):
        err(f"{aid}: duplicate tools")
    for t in tools:
        if t not in TOOL_CATALOG:
            err(f"{aid}: tool {t!r} is not in docs/TOOL_CATALOG.md")
            continue
        if t == "submit_result":
            continue
        problem = _check_tool_precondition(t, perms)
        if problem:
            err(f"{aid}: tool {t} {problem} (permissions inconsistent with allowlist)")
        if t in DISABLED_IN_MVP and a.get("enabled") is True:
            err(f"{aid}: tool {t} is disabled in MVP and must not be allowlisted for an enabled agent")
    has_submit = "submit_result" in tools
    if mode in {"llm", "profile"} and not has_submit:
        err(f"{aid}: llm/profile agents must allowlist submit_result (ADR-013)")
    if mode in {"tool", "planned"} and has_submit:
        err(f"{aid}: submit_result is only for llm/profile agents")
    if mode in {"llm", "profile"} and len([t for t in tools if t != "submit_result"]) == 0:
        err(f"{aid}: llm/profile agent needs at least one tool besides submit_result")
    # ---- limits
    limits = a.get("limits")
    if not isinstance(limits, dict) or set(limits) != {"max_iterations", "timeout_seconds"}:
        err(f"{aid}: limits must have exactly max_iterations and timeout_seconds")
        return
    mi, to = limits.get("max_iterations"), limits.get("timeout_seconds")
    if not isinstance(mi, int) or isinstance(mi, bool) or not 1 <= mi <= 40:
        err(f"{aid}: limits.max_iterations must be an int in 1..40 (CLAUDE_MAX_TOOL_ITERATIONS)")
    if not isinstance(to, int) or isinstance(to, bool) or not 30 <= to <= 3600:
        err(f"{aid}: limits.timeout_seconds must be an int in 30..3600")
    elif mode in {"llm", "profile"} and not 300 <= to <= 900:
        err(f"{aid}: llm/profile timeout_seconds must be within 300..900")


def summarize(data: dict[str, Any]) -> str:
    agents = data["agents"]
    modes = Counter(a["mvp_mode"] for a in agents)
    groups = Counter(a["group"] for a in agents)
    lines = [
        f"OK: {len(agents)} agents ({len(agents) - 1} specialists + ORCH)",
        "  mvp_mode: " + ", ".join(f"{m}={modes.get(m, 0)}" for m in ("llm", "profile", "tool", "planned")),
        "  enabled: " + str(sum(1 for a in agents if a["enabled"])),
        "  groups: " + ", ".join(f"{g}={n}" for g, n in sorted(groups.items())),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1 or (argv and argv[0] in {"-h", "--help"}):
        print(__doc__)
        return 2
    path = Path(argv[0]) if argv else DEFAULT_REGISTRY
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ERROR: registry file not found: {path}", file=sys.stderr)
        return 2
    except yaml.YAMLError as exc:
        print(f"ERROR: invalid YAML in {path}: {exc}", file=sys.stderr)
        return 2
    errors = validate(data)
    if errors:
        print(f"INVALID: {path} — {len(errors)} error(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"{path}")
    print(summarize(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
