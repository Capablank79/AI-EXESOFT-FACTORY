# Contrato de agentes v0.1

Cada asignación deberá incluir: project_id, task_id, role, objective, acceptance_criteria, context_refs, allowed_tools, workspace, timeout_seconds, max_attempts.

Cada resultado deberá incluir: status (success|blocked|failed), artifacts, tests_executed, evidence_paths, commit_sha (cuando corresponda), issues y next_actions.

Roles previstos: pm, architect, ux, developer (Claude Code), qa y devops. Ninguno puede autocertificarse la aprobación de QA.

**Estado actual:** contrato de diseño; todavía no existe el runtime de despacho ni la automatización de Claude Code.
