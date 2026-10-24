#!/usr/bin/env python3
"""Validate ``agents/registry.yaml`` (schema v2) against PRD §6, ADR-012/013/046/047/048, docs/TOOL_CATALOG.md,
docs/PLATFORM_EXPANSION.md §9b (KONTRAK v2: K2 agents, K3 tools) and backend/app/agents/contracts.py.

Usage::

    python3 scripts/validate_registry.py [path/to/registry.v2.yaml]   # schema_version 1 → didelegasikan ke validate_registry_v1.py

Exit codes: 0 = valid, 1 = validation errors, 2 = file/usage error.

Rules (see docs/AGENT_CATALOG.md §2 and §12 for the narrative version):
  * schema: ``schema_version == 2``; every entry has exactly the documented required fields (+ optional v2 fields
    ``target_mode``, ``phase``, ``contract_status``, ``notes``) with valid types/enums;
  * identity: 53 entries, unique ``agent_id`` and ``slug``; all 41 PRD specialist ids + ``ORCH`` + the 11 K2 ids
    present; PRD entries match PRD §6 verbatim (``PRD_CATALOG``); K2 entries match K2 verbatim (slug, name, group,
    target_mode, phase) and allowlist their K2 core tools; E29 is promoted (target_mode llm, phase P9);
  * mode: ``enabled`` is true iff ``mvp_mode != planned``; ``target_mode``/``phase`` only on planned agents and a
    planned agent with ``target_mode`` must have ``phase``; ``runtime_agent`` only (and always) for ``profile``;
    the set of active ``llm`` agents equals the keys of ``OUTPUT_CONTRACTS`` in contracts.py; contracts match that
    module; planned agents with ``target_mode`` carry conceptual contracts with ``contract_status: planned``;
  * tools: subset of the v2 ``TOOL_CATALOG``; ``submit_result`` required for llm/profile (active or target) and
    forbidden otherwise, except ADR-047: ``output_contract: CitedAnswer`` must NOT list ``submit_result`` (and needs
    a citation-source tool); disabled/planned tools never on enabled agents;
  * permissions (v2 profile, 8 fields; legacy entries may keep the 6 v1 fields with defaults ``portfolio: none``,
    ``data_sensitivity_max: confidential``): ``approval_grant == deny`` everywhere; every tool precondition holds;
    server tools (``web.search``, ``web.fetch``, ``code.execute_remote``, ``docs.render_skills``) require
    ``network: provider_web``; R2 tools require ``sandbox: allow``; R3 tools ``github.push_branch``/``github.open_pr``
    only J48, ``portfolio.publish`` only K51, never on llm/profile agents (active or target), existing R3 rules kept;
    ``portfolio.draft_item`` requires ``portfolio: draft``; provider-executed doc/code tools (and, per T15, image
    tools) are forbidden for ``data_sensitivity_max: confidential``; PRD-pinned profiles are enforced;
  * limits: ``1 <= max_iterations <= 40``; llm/profile (active or target) ``timeout_seconds`` within 300..900;
    target_mode tool → ``max_iterations == 1``; optional ``tool_quota`` (v2 entries only) keys ⊆ the agent's tools.

The script depends only on PyYAML and the standard library (contracts.py is parsed with ``ast``, not imported).
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "agents" / "registry.v2.yaml"  # v1 runtime: agents/registry.yaml (validate_registry_v1.py)
CONTRACTS_PY = REPO_ROOT / "backend" / "app" / "agents" / "contracts.py"

SCHEMA_VERSION = 2

# --------------------------------------------------------------------------------------------- enums
GROUPS_V1 = {
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
GROUPS_V2 = {"ai_assistant", "integrations", "portfolio"}
GROUPS = GROUPS_V1 | GROUPS_V2
PRIORITIES = {"P0", "P1", "P2"}
MVP_MODES = {"llm", "profile", "tool", "planned"}
TARGET_MODES = {"llm", "profile", "tool"}
PHASES = {"P8", "P9", "P10", "P11", "P12"}
CONTRACT_STATUSES = {"planned"}
MODEL_TIERS = {"fast", "default", "complex"}
SENSITIVITY_LEVELS = ("public", "internal", "confidential")
PERMISSION_VALUES: dict[str, set[str]] = {
    "filesystem": {"none", "read_only", "test_only", "docs_only", "scoped_write"},
    "sandbox": {"deny", "allow"},
    "git": {"none", "read", "branch_commit"},
    "network": {"deny", "allowlist", "provider_web"},
    "deployment": {"deny", "staging_with_approval"},
    "portfolio": {"none", "draft"},
    "data_sensitivity_max": set(SENSITIVITY_LEVELS),
    "approval_grant": {"deny"},
}
PERMISSION_FIELDS_V1 = frozenset({"filesystem", "sandbox", "git", "network", "deployment", "approval_grant"})
PERMISSION_FIELDS_V2 = frozenset(PERMISSION_VALUES)
# Applied to legacy (v1) entries that omit the v2 fields. ``confidential`` restates v1 semantics (no label cap; the
# IDE agents are governed by ADR-011) and fails closed: adding a provider doc/code tool to such an entry is rejected.
LEGACY_PERMISSION_DEFAULTS = {"portfolio": "none", "data_sensitivity_max": "confidential"}

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
OPTIONAL_FIELDS: dict[str, type | tuple[type, ...]] = {
    "target_mode": str,
    "phase": str,
    "contract_status": str,
    "notes": list,
}


# --------------------------------------------------------------------- docs/TOOL_CATALOG.md (MVP + v2)
class Tool(NamedTuple):
    """One TOOL_CATALOG row.

    ``pre`` are permission/allowlist preconditions checked statically (the ToolGateway re-checks at runtime):
    ``fs_read``, ``fs_write``, ``fs_scoped_write``, ``git_read``, ``git_branch_commit``, ``network_allowlist``,
    ``provider_web``, ``deployment``, ``portfolio_draft``, ``not_confidential``, ``moderation``. Every R2 tool also
    requires ``sandbox: allow`` (risk rule). ``status``: ``mvp`` (implemented), ``disabled`` (P1/P2, never on an
    enabled agent), ``planned`` (K3 v2 tool, design only — never on an enabled agent).
    """

    risk: str  # R0 | R1 | R2 | R3 | "-" (terminal)
    executor: (
        str  # backend | sandbox | worker | provider_anthropic | provider_image | backend+vendor | terminal
    )
    pre: tuple[str, ...]
    status: str


TOOL_CATALOG: dict[str, Tool] = {
    # ---- MVP builtin tools (docs/TOOL_CATALOG.md "Builtin tools (MVP)")
    "repo.get_tree": Tool("R0", "backend", ("fs_read",), "mvp"),
    "repo.read_file": Tool("R0", "backend", ("fs_read",), "mvp"),
    "repo.search_text": Tool("R0", "backend", ("fs_read",), "mvp"),
    "repo.search_symbol": Tool("R0", "backend", ("fs_read",), "mvp"),
    "repo.detect_stack": Tool("R0", "backend", (), "mvp"),
    "repo.find_related_tests": Tool("R0", "backend", ("fs_read",), "mvp"),
    "git.status": Tool("R0", "backend", ("git_read",), "mvp"),
    "git.diff": Tool("R0", "backend", ("git_read",), "mvp"),
    "git.log": Tool("R0", "backend", ("git_read",), "mvp"),
    "tests.list_profiles": Tool("R0", "backend", (), "mvp"),
    "tests.get_run": Tool("R0", "backend", (), "mvp"),
    "review.get_task_diff": Tool("R0", "backend", (), "mvp"),
    "acceptance.list_criteria": Tool("R0", "backend", (), "mvp"),
    "acceptance.list_evidence": Tool("R0", "backend", (), "mvp"),
    "memory.retrieve_context": Tool("R0", "backend", (), "mvp"),
    "security.scan_secrets": Tool("R0", "backend", (), "mvp"),
    "code.write_file": Tool("R1", "backend", ("fs_write",), "mvp"),
    "code.apply_patch": Tool("R1", "backend", ("fs_write",), "mvp"),
    "code.delete_file": Tool("R1", "backend", ("fs_scoped_write",), "mvp"),
    "git.commit_task": Tool("R1", "backend", ("git_branch_commit",), "mvp"),
    "memory.store_decision": Tool("R1", "backend", (), "mvp"),
    "tests.run": Tool("R2", "sandbox", (), "mvp"),
    "env.inspect_runtime": Tool("R2", "sandbox", (), "mvp"),
    "deps.install": Tool("R2", "sandbox", ("network_allowlist",), "disabled"),
    "git.push": Tool("R3", "worker", ("git_branch_commit",), "disabled"),
    "deploy.staging": Tool("R3", "worker", ("deployment",), "disabled"),
    "submit_result": Tool("-", "terminal", (), "mvp"),
    # ---- v2 tools (PLATFORM_EXPANSION §9b K3) — design only
    "capabilities.list": Tool("R0", "backend", (), "planned"),
    "web.search": Tool("R0", "provider_anthropic", ("provider_web",), "planned"),
    "web.fetch": Tool("R0", "provider_anthropic", ("provider_web",), "planned"),
    "code.execute_remote": Tool("R2", "provider_anthropic", ("provider_web", "not_confidential"), "planned"),
    "docs.render_skills": Tool("R1", "provider_anthropic", ("provider_web", "not_confidential"), "planned"),
    "docs.render_local": Tool("R2", "sandbox", (), "planned"),
    "assets.store": Tool("R1", "backend", (), "planned"),
    "image.generate": Tool("R1", "provider_image", ("moderation", "not_confidential"), "planned"),
    "image.edit": Tool("R1", "provider_image", ("moderation", "not_confidential"), "planned"),
    "moderation.check_text": Tool("R0", "backend+vendor", (), "planned"),
    "moderation.check_image_prompt": Tool("R0", "backend+vendor", (), "planned"),
    "kb.search": Tool("R0", "backend", (), "planned"),
    "kb.get_chunk": Tool("R0", "backend", (), "planned"),
    "analysis.run": Tool("R2", "sandbox", (), "planned"),
    "github.list_repos": Tool("R0", "backend", ("git_read",), "planned"),
    "github.get_pr_status": Tool("R0", "backend", ("git_read",), "planned"),
    "github.get_repo_summary": Tool("R0", "backend", ("git_read",), "planned"),
    "github.import_repo": Tool("R1", "worker", ("git_read",), "planned"),
    "github.pull": Tool("R1", "worker", ("git_read",), "planned"),
    "github.push_branch": Tool("R3", "worker", ("git_branch_commit",), "planned"),
    "github.open_pr": Tool("R3", "worker", ("git_branch_commit",), "planned"),
    "portfolio.get_profile": Tool("R0", "backend", (), "planned"),
    "portfolio.draft_item": Tool("R1", "backend", ("portfolio_draft",), "planned"),
    "portfolio.privacy_check": Tool("R0", "backend", (), "planned"),
    "portfolio.build_site": Tool("R2", "sandbox", (), "planned"),
    "portfolio.publish": Tool("R3", "backend", (), "planned"),
}
DISABLED_IN_MVP = {name for name, t in TOOL_CATALOG.items() if t.status == "disabled"}
PLANNED_TOOLS = {name for name, t in TOOL_CATALOG.items() if t.status == "planned"}
# Claude server tools: executed on Anthropic infrastructure, still allowlisted/metered/audited by the ToolGateway.
SERVER_TOOLS = {name for name, t in TOOL_CATALOG.items() if t.executor == "provider_anthropic"}
# R3 tools introduced in v2 and the only agents that may hold them (existing R3 keep their v1 rules).
R3_OWNERS: dict[str, set[str]] = {
    "github.push_branch": {"J48"},
    "github.open_pr": {"J48"},
    "portfolio.publish": {"K51"},
}
CITED_ANSWER = "CitedAnswer"
CITATION_SOURCE_TOOLS = {"web.search", "web.fetch", "kb.search", "kb.get_chunk"}
NOT_CONFIDENTIAL_REASON = {
    "docs.render_skills": "Agent Skills/Files API are not ZDR (ADR-039)",
    "code.execute_remote": "provider code execution, data != confidential (K3)",
    "image.generate": "third-party image vendor (T15)",
    "image.edit": "third-party image vendor (T15)",
}

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
    "E29": {"filesystem": "read_only"},  # v2: PR author never writes files; push/PR via J48 (K2 promotion)
}
ONLY_DEPLOYER = {"F32"}

ID_RE = re.compile(r"^(?:[A-H]\d{2}|[IJKS]\d{2}|ORCH)$")
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

# ------------------------------------------------------------ PLATFORM_EXPANSION §9b K2 (mechanical copy)
# id -> (slug, name, group, target_mode, phase, K2 core tools, K2 "atau" alternatives: at least one required)
K2_CATALOG: dict[str, tuple[str, str, str, str, str, tuple[str, ...], tuple[str, ...]]] = {
    "I42": (
        "assistant_concierge",
        "Assistant Router & Concierge",
        "ai_assistant",
        "llm",
        "P10",
        ("capabilities.list", "kb.search", "submit_result"),
        (),
    ),
    "I43": (
        "web_research",
        "Web Research & Citation Agent",
        "ai_assistant",
        "llm",
        "P10",
        ("web.search", "web.fetch"),
        (),
    ),
    "I44": (
        "document_builder",
        "Document Builder Agent",
        "ai_assistant",
        "llm",
        "P10",
        ("docs.render_skills", "docs.render_local", "assets.store", "submit_result"),
        (),
    ),
    "I45": (
        "visual_prompt",
        "Visual Prompt Agent",
        "ai_assistant",
        "llm",
        "P11",
        ("moderation.check_image_prompt", "image.generate", "image.edit", "submit_result"),
        (),
    ),
    "I46": (
        "data_analyst",
        "Data Analysis Agent",
        "ai_assistant",
        "llm",
        "P11",
        ("assets.store", "submit_result"),
        ("analysis.run", "code.execute_remote"),
    ),
    "I47": (
        "knowledge_base",
        "Knowledge Base Agent",
        "ai_assistant",
        "llm",
        "P11",
        ("kb.search", "kb.get_chunk"),
        (),
    ),
    "J48": (
        "github_integration",
        "GitHub Integration Agent",
        "integrations",
        "tool",
        "P9",
        (
            "github.list_repos",
            "github.import_repo",
            "github.pull",
            "github.push_branch",
            "github.open_pr",
            "github.get_pr_status",
        ),
        (),
    ),
    "K49": (
        "portfolio_curator",
        "Portfolio Curator Agent",
        "portfolio",
        "llm",
        "P12",
        (
            "portfolio.get_profile",
            "github.get_repo_summary",
            "repo.read_file",
            "portfolio.draft_item",
            "submit_result",
        ),
        (),
    ),
    "K50": (
        "cv_job_fit",
        "CV & Job-fit Agent",
        "portfolio",
        "llm",
        "P12",
        ("web.fetch", "portfolio.get_profile", "docs.render_local", "portfolio.draft_item", "submit_result"),
        (),
    ),
    "K51": (
        "portfolio_publisher",
        "Portfolio Publisher",
        "portfolio",
        "tool",
        "P12",
        ("portfolio.privacy_check", "portfolio.build_site", "portfolio.publish"),
        (),
    ),
    "S52": (
        "content_safety",
        "Content Safety & Moderation",
        "quality_security",
        "tool",
        "P10",
        ("moderation.check_text", "moderation.check_image_prompt"),
        (),
    ),
}
# K2 "Promosi": existing PRD agents whose target_mode/phase are fixed by the contract.
PROMOTIONS: dict[str, tuple[str, str]] = {"E29": ("llm", "P9")}
EXPECTED_IDS = set(PRD_CATALOG) | set(K2_CATALOG)
EXPECTED_TOTAL = len(EXPECTED_IDS)  # 41 + ORCH + 11 = 53


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


def effective_permissions(perms: dict[str, Any]) -> dict[str, Any]:
    """Permissions with v2 defaults applied to legacy (v1, 6-field) entries."""
    return {**LEGACY_PERMISSION_DEFAULTS, **perms}


def runtime_kind(a: dict[str, Any]) -> str | None:
    """``mvp_mode`` for active agents, ``target_mode`` for planned ones (None when a planned agent has none)."""
    mode = a.get("mvp_mode")
    if mode == "planned":
        tm = a.get("target_mode")
        return tm if isinstance(tm, str) else None
    return mode if isinstance(mode, str) else None


def _tool_problems(tool: str, perms: dict[str, Any], tools: list[Any]) -> list[str]:
    spec = TOOL_CATALOG[tool]
    fs, sandbox, git = perms.get("filesystem"), perms.get("sandbox"), perms.get("git")
    problems: list[str] = []
    for pre in spec.pre:
        if pre == "fs_read" and fs == "none":
            problems.append("requires filesystem != none")
        elif pre == "fs_write" and fs not in {"test_only", "docs_only", "scoped_write"}:
            problems.append("requires filesystem in {test_only, docs_only, scoped_write}")
        elif pre == "fs_scoped_write" and fs != "scoped_write":
            problems.append("requires filesystem = scoped_write")
        elif pre == "git_read" and git not in {"read", "branch_commit"}:
            problems.append("requires git >= read")
        elif pre == "git_branch_commit" and git != "branch_commit":
            problems.append("requires git = branch_commit")
        elif pre == "network_allowlist" and perms.get("network") != "allowlist":
            problems.append("requires network = allowlist")
        elif pre == "provider_web" and perms.get("network") != "provider_web":
            problems.append("requires network = provider_web (server tool executed by the provider)")
        elif pre == "deployment" and perms.get("deployment") != "staging_with_approval":
            problems.append("requires deployment = staging_with_approval")
        elif pre == "portfolio_draft" and perms.get("portfolio") != "draft":
            problems.append("requires portfolio = draft")
        elif pre == "not_confidential" and perms.get("data_sensitivity_max") == "confidential":
            problems.append(
                f"is forbidden for data_sensitivity_max = confidential ({NOT_CONFIDENTIAL_REASON[tool]})"
            )
        elif pre == "moderation" and "moderation.check_image_prompt" not in tools:
            problems.append("requires moderation.check_image_prompt in the same allowlist (AX-07, T16)")
    if spec.risk == "R2" and sandbox != "allow":
        problems.append("requires sandbox = allow (R2)")
    return problems


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
    if data.get("schema_version") != SCHEMA_VERSION:
        err(
            f"schema_version must be {SCHEMA_VERSION} (got {data.get('schema_version')!r}; v1 retired by ADR-048)"
        )
    agents = data.get("agents")
    if not isinstance(agents, list):
        return [*errors, "'agents' must be a list"]

    by_id: dict[str, dict[str, Any]] = {}
    ids = Counter()
    slugs = Counter()
    allowed_fields = {**REQUIRED_FIELDS, **OPTIONAL_FIELDS}
    for idx, a in enumerate(agents):
        where = f"agents[{idx}]"
        if not isinstance(a, dict):
            err(f"{where}: entry must be a mapping")
            continue
        aid = a.get("agent_id")
        where = f"{aid or where}"
        missing = [k for k in REQUIRED_FIELDS if k not in a]
        unknown = [k for k in a if k not in allowed_fields]
        if missing:
            err(f"{where}: missing fields {missing}")
        if unknown:
            err(f"{where}: unknown fields {unknown}")
        for k, typ in allowed_fields.items():
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
    missing_prd = sorted(set(PRD_CATALOG) - set(by_id))
    missing_k2 = sorted(set(K2_CATALOG) - set(by_id))
    unknown_ids = sorted(set(by_id) - EXPECTED_IDS)
    if missing_prd:
        err(f"missing PRD agents: {missing_prd}")
    if missing_k2:
        err(f"missing K2 agents: {missing_k2}")
    if unknown_ids:
        err(f"agent ids not in PRD §6 or K2: {unknown_ids}")
    if len(agents) != EXPECTED_TOTAL:
        err(f"expected {EXPECTED_TOTAL} entries (41 specialists + ORCH + 11 K2), found {len(agents)}")

    llm_ids = {aid for aid, a in by_id.items() if a.get("mvp_mode") == "llm"}
    if llm_ids != set(contracts):
        err(
            "llm agents must equal OUTPUT_CONTRACTS keys in contracts.py: "
            f"registry={sorted(llm_ids)} contracts={sorted(contracts)}"
        )

    for aid, a in by_id.items():
        _validate_entry(aid, a, by_id, contracts, err)
    return errors


def _validate_identity(aid: str, a: dict[str, Any], err: Any) -> None:
    if aid in PRD_CATALOG:
        name, group, caps = PRD_CATALOG[aid]
        if a.get("name") != name:
            err(f"{aid}: name {a.get('name')!r} != PRD {name!r}")
        if a.get("group") != group:
            err(f"{aid}: group {a.get('group')!r} != PRD {group!r}")
        if a.get("capabilities") != caps.split():
            err(f"{aid}: capabilities differ from PRD §6 (must be verbatim, same order)")
    if aid in PROMOTIONS:
        tm, phase = PROMOTIONS[aid]
        if a.get("target_mode") != tm or a.get("phase") != phase:
            err(f"{aid}: K2 promotion requires target_mode {tm!r} and phase {phase!r}")
    if aid in K2_CATALOG:
        slug, name, group, tm, phase, core, any_of = K2_CATALOG[aid]
        for field, exp in (
            ("slug", slug),
            ("name", name),
            ("group", group),
            ("target_mode", tm),
            ("phase", phase),
        ):
            if a.get(field) != exp:
                err(f"{aid}: {field} {a.get(field)!r} != K2 {exp!r}")
        tools = a.get("tools") or []
        missing = [t for t in core if t not in tools]
        if missing:
            err(f"{aid}: missing K2 tools {missing}")
        if any_of and not any(t in tools for t in any_of):
            err(f"{aid}: needs at least one of K2 alternatives {list(any_of)}")


def _validate_mode(
    aid: str, a: dict[str, Any], by_id: dict[str, dict[str, Any]], contracts: Any, err: Any
) -> None:
    mode = a.get("mvp_mode")
    tm = a.get("target_mode")
    phase = a.get("phase")
    # ---- target_mode / phase (v2)
    if tm is not None:
        if mode != "planned":
            err(f"{aid}: target_mode is only for mvp_mode=planned (got mvp_mode={mode})")
        if tm not in TARGET_MODES:
            err(f"{aid}: invalid target_mode {tm!r}")
        if phase is None:
            err(f"{aid}: planned agent with target_mode must have phase (ADR-048)")
    if phase is not None:
        if tm is None:
            err(f"{aid}: phase requires target_mode")
        if phase not in PHASES:
            err(f"{aid}: invalid phase {phase!r} (allowed {sorted(PHASES)})")
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
    pair = (a.get("input_contract"), a.get("output_contract"))
    status = a.get("contract_status")
    if mode == "llm":
        exp = contracts.get(aid)
        if exp is None:
            err(f"{aid}: llm agent has no entry in OUTPUT_CONTRACTS")
        elif pair != exp:
            err(f"{aid}: contracts {pair} != contracts.py {exp}")
    if mode == "tool" and pair != (None, None):
        err(f"{aid}: input_contract/output_contract must be null for mvp_mode={mode}")
    if mode == "planned":
        if tm is None and pair != (None, None):
            err(
                f"{aid}: input_contract/output_contract must be null for mvp_mode=planned without target_mode"
            )
        if tm is not None and (None in pair or status != "planned"):
            err(
                f"{aid}: planned agent with target_mode must declare conceptual input/output contracts "
                "with contract_status: planned"
            )
    if status is not None:
        if status not in CONTRACT_STATUSES:
            err(f"{aid}: invalid contract_status {status!r}")
        if mode != "planned" or tm is None:
            err(
                f"{aid}: contract_status is only for planned agents with target_mode (bound contracts live in contracts.py)"
            )
    for k in ("input_contract", "output_contract"):
        v = a.get(k)
        if isinstance(v, str) and not CONTRACT_RE.match(v):
            err(f"{aid}: {k} {v!r} is not a class name")


def _validate_entry(
    aid: str,
    a: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    contracts: dict[str, tuple[str, str]],
    err: Any,
) -> None:
    _validate_identity(aid, a, err)
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
    notes = a.get("notes")
    if isinstance(notes, list) and (not notes or not all(isinstance(n, str) and n.strip() for n in notes)):
        err(f"{aid}: notes must be a non-empty list of non-empty strings")
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
    _validate_mode(aid, a, by_id, contracts, err)
    kind = runtime_kind(a)
    is_v2_entry = a.get("target_mode") is not None
    # ---- permissions
    raw_perms = a.get("permissions")
    if not isinstance(raw_perms, dict):
        return
    fields = set(raw_perms)
    if is_v2_entry and fields != PERMISSION_FIELDS_V2:
        err(
            f"{aid}: permissions must have exactly {sorted(PERMISSION_FIELDS_V2)} "
            f"(v2 entries declare portfolio and data_sensitivity_max; got {sorted(fields)})"
        )
    elif not is_v2_entry and fields not in (PERMISSION_FIELDS_V1, PERMISSION_FIELDS_V2):
        err(
            f"{aid}: permissions must have exactly {sorted(PERMISSION_FIELDS_V1)} (v1) or "
            f"{sorted(PERMISSION_FIELDS_V2)} (v2) (got {sorted(fields)})"
        )
    perms = effective_permissions(raw_perms)
    for k, allowed in PERMISSION_VALUES.items():
        if k in raw_perms and raw_perms[k] not in allowed:
            if k == "approval_grant":
                err(f"{aid}: approval_grant must be 'deny' (PRD §10.4) — got {raw_perms[k]!r}")
            else:
                err(f"{aid}: permissions.{k}={raw_perms[k]!r} not in {sorted(allowed)}")
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
        for problem in _tool_problems(t, perms, tools):
            err(f"{aid}: tool {t} {problem} (permissions inconsistent with allowlist)")
        if t in DISABLED_IN_MVP and a.get("enabled") is True:
            err(f"{aid}: tool {t} is disabled in MVP and must not be allowlisted for an enabled agent")
        if t in PLANNED_TOOLS and a.get("enabled") is True:
            err(f"{aid}: tool {t} is a planned v2 tool (K3) and must not be allowlisted for an enabled agent")
        if TOOL_CATALOG[t].risk == "R3":
            owners = R3_OWNERS.get(t)
            if owners is not None and aid not in owners:
                err(f"{aid}: tool {t} (R3) is reserved for {sorted(owners)}")
            if kind in {"llm", "profile"}:
                err(
                    f"{aid}: tool {t} (R3) is never allowed for llm/profile agents (incl. planned target_mode)"
                )
    known = [t for t in tools if t in TOOL_CATALOG]
    if perms.get("network") == "provider_web" and not any(t in SERVER_TOOLS for t in known):
        err(f"{aid}: network = provider_web granted without any provider server tool (least privilege)")
    if perms.get("portfolio") == "draft" and "portfolio.draft_item" not in tools:
        err(f"{aid}: portfolio = draft granted without portfolio.draft_item (least privilege)")
    # ---- submit_result (ADR-013 / ADR-047)
    has_submit = "submit_result" in tools
    cited = a.get("output_contract") == CITED_ANSWER
    if cited:
        if has_submit:
            err(
                f"{aid}: output_contract CitedAnswer must not allowlist submit_result (ADR-047: ends with cited text)"
            )
        if not any(t in CITATION_SOURCE_TOOLS for t in tools):
            err(
                f"{aid}: output_contract CitedAnswer needs a citation-source tool {sorted(CITATION_SOURCE_TOOLS)}"
            )
    elif kind in {"llm", "profile"} and not has_submit:
        err(
            f"{aid}: llm/profile agents must allowlist submit_result (ADR-013; incl. planned target_mode llm/profile)"
        )
    if kind not in {"llm", "profile"} and has_submit:
        err(f"{aid}: submit_result is only for llm/profile agents (or planned target_mode llm/profile)")
    if kind in {"llm", "profile"} and len([t for t in tools if t != "submit_result"]) == 0:
        err(f"{aid}: llm/profile agent needs at least one tool besides submit_result")
    # ---- limits
    limits = a.get("limits")
    allowed_limit_keys = {"max_iterations", "timeout_seconds"}
    if not isinstance(limits, dict) or not allowed_limit_keys <= set(limits):
        err(f"{aid}: limits must have exactly max_iterations and timeout_seconds")
        return
    extra = set(limits) - allowed_limit_keys
    if extra and (not is_v2_entry or extra != {"tool_quota"}):
        err(
            f"{aid}: limits must have exactly max_iterations and timeout_seconds (+ tool_quota for v2 entries)"
        )
    quota = limits.get("tool_quota")
    if "tool_quota" in limits:
        if not isinstance(quota, dict) or not quota:
            err(f"{aid}: limits.tool_quota must be a non-empty mapping tool -> int")
        else:
            for t, q in quota.items():
                if t not in tools:
                    err(f"{aid}: limits.tool_quota key {t!r} is not in the agent's tools")
                if not isinstance(q, int) or isinstance(q, bool) or not 1 <= q <= 100:
                    err(f"{aid}: limits.tool_quota[{t!r}] must be an int in 1..100")
    mi, to = limits.get("max_iterations"), limits.get("timeout_seconds")
    if not isinstance(mi, int) or isinstance(mi, bool) or not 1 <= mi <= 40:
        err(f"{aid}: limits.max_iterations must be an int in 1..40 (CLAUDE_MAX_TOOL_ITERATIONS)")
    elif a.get("target_mode") == "tool" and mi != 1:
        err(f"{aid}: target_mode tool means one deterministic call per invocation (max_iterations must be 1)")
    if not isinstance(to, int) or isinstance(to, bool) or not 30 <= to <= 3600:
        err(f"{aid}: limits.timeout_seconds must be an int in 30..3600")
    elif kind in {"llm", "profile"} and not 300 <= to <= 900:
        err(f"{aid}: llm/profile timeout_seconds must be within 300..900")


def summarize(data: dict[str, Any]) -> str:
    agents = data["agents"]
    modes = Counter(a["mvp_mode"] for a in agents)
    groups = Counter(a["group"] for a in agents)
    planned = [a for a in agents if a["mvp_mode"] == "planned"]
    targets = Counter(a.get("target_mode") or "unset" for a in planned)
    phases = Counter(a["phase"] for a in agents if a.get("phase"))
    lines = [
        f"OK: {len(agents)} agents (41 specialists + ORCH + {len(K2_CATALOG)} K2) — schema_version {SCHEMA_VERSION}",
        "  mvp_mode: " + ", ".join(f"{m}={modes.get(m, 0)}" for m in ("llm", "profile", "tool", "planned")),
        "  enabled: " + str(sum(1 for a in agents if a["enabled"])),
        "  target_mode (planned): "
        + ", ".join(f"{t}={targets.get(t, 0)}" for t in ("llm", "profile", "tool", "unset")),
        "  phase: " + ", ".join(f"{p}={phases[p]}" for p in sorted(phases, key=lambda p: int(p[1:]))),
        "  contract_status planned: " + str(sum(1 for a in agents if a.get("contract_status") == "planned")),
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
    if isinstance(data, dict) and data.get("schema_version") == 1:
        # Registry runtime v1 (agents/registry.yaml) tetap divalidasi aturan v1 sampai migrasi WP-9.0.
        import importlib.util

        spec = importlib.util.spec_from_file_location("validate_registry_v1", Path(__file__).with_name("validate_registry_v1.py"))
        assert spec and spec.loader
        v1 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(v1)
        return int(v1.main([str(path)]))
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
