from __future__ import annotations

from sqlalchemy import Engine, inspect, text


MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            "ALTER TABLE research_tasks ADD COLUMN available_after DATETIME",
            "ALTER TABLE agent_executions ADD COLUMN last_heartbeat_at DATETIME",
            "ALTER TABLE agent_executions ADD COLUMN cancel_requested_at DATETIME",
            "ALTER TABLE agent_executions ADD COLUMN output_bytes INTEGER NOT NULL DEFAULT 0",
        ],
    ),
    (
        2,
        [
            "ALTER TABLE research_runs ADD COLUMN provider_mode VARCHAR(32) NOT NULL DEFAULT 'inhouse_azure'",
            "ALTER TABLE research_runs ADD COLUMN langgraph_thread_id VARCHAR(160)",
            "ALTER TABLE research_runs ADD COLUMN token_budget INTEGER NOT NULL DEFAULT 1000000",
            "ALTER TABLE research_runs ADD COLUMN cost_budget_usd FLOAT NOT NULL DEFAULT 50.0",
            "ALTER TABLE research_runs ADD COLUMN tokens_used INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE research_runs ADD COLUMN cost_used_usd FLOAT NOT NULL DEFAULT 0.0",
            "ALTER TABLE agent_executions ADD COLUMN model VARCHAR(160)",
            "ALTER TABLE agent_executions ADD COLUMN langgraph_thread_id VARCHAR(160)",
            "ALTER TABLE agent_executions ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE agent_executions ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE agent_executions ADD COLUMN cost_usd FLOAT NOT NULL DEFAULT 0.0",
            "ALTER TABLE agent_executions ADD COLUMN tool_calls_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE research_settings ADD COLUMN provider_mode VARCHAR(32) NOT NULL DEFAULT 'inhouse_azure'",
            "ALTER TABLE research_settings ADD COLUMN inhouse_agent_concurrency INTEGER NOT NULL DEFAULT 5",
            "ALTER TABLE research_settings ADD COLUMN inhouse_tool_rounds INTEGER NOT NULL DEFAULT 6",
            "ALTER TABLE research_settings ADD COLUMN default_task_budget INTEGER NOT NULL DEFAULT 20",
            "ALTER TABLE research_settings ADD COLUMN documentation_experiment_budget INTEGER NOT NULL DEFAULT 12",
            "ALTER TABLE research_settings ADD COLUMN azure_token_budget INTEGER NOT NULL DEFAULT 1000000",
            "ALTER TABLE research_settings ADD COLUMN azure_cost_budget_usd FLOAT NOT NULL DEFAULT 50.0",
            "ALTER TABLE research_settings ADD COLUMN codex_fallback BOOLEAN NOT NULL DEFAULT 1",
        ],
    ),
    (
        3,
        [
            "ALTER TABLE research_settings ADD COLUMN auto_accept_verified_single_source BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE research_settings ADD COLUMN auto_resolve_evidence_exceptions BOOLEAN NOT NULL DEFAULT 1",
            "ALTER TABLE research_settings ADD COLUMN auto_publish_documentation BOOLEAN NOT NULL DEFAULT 0",
            "UPDATE review_items SET status = 'auto_resolved', decision = 'handled_by_evidence_policy' WHERE status = 'open' AND category <> 'conflict'",
        ],
    ),
    (
        4,
        [
            "UPDATE review_items SET status = 'auto_resolved', decision = 'retained_as_unresolved' WHERE status = 'open'",
        ],
    ),
    (
        5,
        [
            # Automatic publishing was briefly introduced after the approval-
            # gated design. Disable it for every existing installation.
            "UPDATE research_settings SET auto_publish_documentation = 0",
            # Only demote the currently published release. Older superseded
            # releases remain immutable audit history.
            "UPDATE course_releases SET status = 'awaiting_approval', published_at = NULL "
            "WHERE status = 'published' AND id IN ("
            "SELECT dr.candidate_release_id FROM documentation_runs dr "
            "JOIN approval_decisions ad ON ad.documentation_run_id = dr.id "
            "WHERE ad.actor = 'atlas_evidence_policy' AND ad.decision = 'approve'"
            ")",
            "UPDATE documentation_runs SET status = 'awaiting_approval', completed_at = NULL "
            "WHERE candidate_release_id IN (SELECT id FROM course_releases WHERE status = 'awaiting_approval') "
            "AND id IN (SELECT documentation_run_id FROM approval_decisions "
            "WHERE actor = 'atlas_evidence_policy' AND decision = 'approve')",
        ],
    ),
]


def apply_migrations(engine: Engine) -> None:
    """Apply small, additive SQLite migrations without replacing user data."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        applied = {row[0] for row in connection.execute(text("SELECT version FROM schema_migrations"))}
        inspector = inspect(connection)
        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            for statement in statements:
                tokens = statement.split()
                if len(tokens) > 5 and tokens[0:2] == ["ALTER", "TABLE"] and tokens[3:5] == ["ADD", "COLUMN"]:
                    table = tokens[2]
                    column = tokens[5]
                    existing = {item["name"] for item in inspector.get_columns(table)}
                    if column in existing:
                        continue
                connection.execute(text(statement))
                inspector.clear_cache()
            connection.execute(text("INSERT INTO schema_migrations(version) VALUES (:version)"), {"version": version})
