"""add control plane foundations

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), server_default="active", nullable=False),
        sa.Column(
            "tenant_admin_settings_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')", name="ck_tenant_status"
        ),
    )

    op.add_column(
        "auth_users", sa.Column("display_name", sa.String(256), server_default="", nullable=False)
    )
    op.add_column("auth_users", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "auth_users", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    op.add_column(
        "auth_users",
        sa.Column("is_super_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "auth_users",
        sa.Column("must_change_password", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "auth_users", sa.Column("token_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column(
        "auth_users",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column(
        "auth_users",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column(
        "auth_users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_auth_users_email", "auth_users", ["email"], unique=True)

    op.create_table(
        "tenant_memberships",
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("role", sa.String(32), server_default="viewer", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
        sa.CheckConstraint(
            "role IN ('super_admin', 'tenant_admin', 'analyst', 'viewer')",
            name="ck_membership_role",
        ),
    )
    op.create_index("ix_membership_user_active", "tenant_memberships", ["user_id", "is_active"])

    op.execute(
        """
        INSERT INTO tenants (id, display_name)
        SELECT DISTINCT tenant_id, tenant_id FROM auth_users
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_memberships (tenant_id, user_id, role)
        SELECT tenant_id, id,
               CASE WHEN role = 'admin' THEN 'tenant_admin'
                    WHEN role IN ('tenant_admin', 'analyst', 'viewer') THEN role
                    ELSE 'viewer' END
        FROM auth_users
        ON CONFLICT (tenant_id, user_id) DO NOTHING
        """
    )
    op.drop_index("ix_auth_users_tenant_id", table_name="auth_users")
    op.drop_column("auth_users", "role")
    op.drop_column("auth_users", "tenant_id")

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index(
        "ix_auth_sessions_refresh_token_hash",
        "auth_sessions",
        ["refresh_token_hash"],
        unique=True,
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), server_default="functional", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_group_tenant_slug"),
        sa.CheckConstraint("kind IN ('functional', 'project')", name="ck_group_kind"),
    )
    op.create_index("ix_groups_tenant_id", "groups", ["tenant_id"])

    op.create_table(
        "group_memberships",
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "user_id"),
    )

    op.create_table(
        "document_access_templates",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column(
            "default_classification", sa.String(32), server_default="internal", nullable=False
        ),
        sa.Column("owner_rule", sa.String(32), server_default="uploader", nullable=False),
        sa.Column("allowed_group_ids", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("direct_viewer_ids", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "document_type", name="uq_access_template_type"),
        sa.CheckConstraint(
            "default_classification IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_access_template_classification",
        ),
        sa.CheckConstraint(
            "owner_rule IN ('uploader', 'tenant_admin')", name="ck_access_template_owner_rule"
        ),
    )
    op.create_index(
        "ix_document_access_templates_tenant_id",
        "document_access_templates",
        ["tenant_id"],
    )

    _create_control_plane_tables()
    _extend_audit_rows()
    _extend_ingestion_jobs()


def _create_control_plane_tables() -> None:
    jsonb = postgresql.JSONB()
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("actor_user_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(256), nullable=False),
        sa.Column("outcome", sa.String(32), server_default="success", nullable=False),
        sa.Column("event_metadata", jsonb, server_default="{}", nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_admin_audit_events_tenant_id", "admin_audit_events", ["tenant_id"])
    op.create_index("ix_admin_audit_events_actor_user_id", "admin_audit_events", ["actor_user_id"])

    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="configured", nullable=False),
        sa.Column("last_tested_at", timestamp, nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_provider_credential_scope"),
    )
    op.create_index("ix_provider_credentials_tenant_id", "provider_credentials", ["tenant_id"])

    op.create_table(
        "runtime_settings",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", jsonb, server_default="{}", nullable=False),
        sa.Column("restart_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_runtime_setting_scope"),
    )
    op.create_index("ix_runtime_settings_tenant_id", "runtime_settings", ["tenant_id"])

    op.create_table(
        "model_catalog",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(256), nullable=False),
        sa.Column("capabilities", jsonb, server_default="[]", nullable=False),
        sa.Column("status", sa.String(32), server_default="available", nullable=False),
        sa.Column("discovered_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", "model_id", name="uq_model_catalog_scope"),
    )
    op.create_index("ix_model_catalog_tenant_id", "model_catalog", ["tenant_id"])
    op.create_index("ix_model_catalog_provider", "model_catalog", ["provider"])

    op.create_table(
        "operation_jobs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), server_default="queued", nullable=False),
        sa.Column("parameters", jsonb, server_default="{}", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("result", jsonb, server_default="{}", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp, nullable=True),
    )
    op.create_index("ix_operation_jobs_tenant_id", "operation_jobs", ["tenant_id"])
    op.create_index("ix_operation_jobs_kind", "operation_jobs", ["kind"])
    op.create_index("ix_operation_jobs_requested_by", "operation_jobs", ["requested_by"])

    op.create_table(
        "audit_chain_heads",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("chain_name", sa.String(64), primary_key=True),
        sa.Column("last_hash", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "audit_anchors",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("from_sequence", sa.Integer(), nullable=False),
        sa.Column("to_sequence", sa.Integer(), nullable=False),
        sa.Column("merkle_root", sa.String(64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("object_version", sa.String(256), nullable=True),
        sa.Column("anchored_at", timestamp, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_anchors_tenant_id", "audit_anchors", ["tenant_id"])


def _extend_ingestion_jobs() -> None:
    op.add_column("ingestion_jobs", sa.Column("requested_by", sa.String(128), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("owner_id", sa.String(128), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("current_step", sa.String(64), server_default="queued", nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("ingestion_jobs", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ingestion_jobs_requested_by", "ingestion_jobs", ["requested_by"])
    op.create_index("ix_ingestion_jobs_owner_id", "ingestion_jobs", ["owner_id"])
    op.create_unique_constraint(
        "uq_ingestion_jobs_idempotency_key", "ingestion_jobs", ["idempotency_key"]
    )


def _extend_audit_rows() -> None:
    tables = (
        (
            "query_traces",
            "trace_id",
            "started_at, trace_id",
            "query_traces",
            "genesis_trace",
            "uq_trace_chain_sequence",
        ),
        (
            "retrieval_evidence",
            "evidence_id",
            "evidence_id",
            "retrieval_evidence",
            "genesis_evidence",
            "uq_evidence_chain_sequence",
        ),
        (
            "policy_decisions",
            "decision_id",
            "evaluated_at, decision_id",
            "policy_decisions",
            "genesis_decision",
            "uq_policy_chain_sequence",
        ),
    )
    for table, primary_key, ordering, chain_name, genesis, constraint in tables:
        op.add_column(table, sa.Column("chain_sequence", sa.Integer(), nullable=True))
        op.execute(
            f"""
            WITH ranked AS (
                SELECT {primary_key},
                       ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY {ordering}) AS sequence
                FROM {table}
            )
            UPDATE {table} AS target
            SET chain_sequence = ranked.sequence
            FROM ranked
            WHERE target.{primary_key} = ranked.{primary_key}
            """
        )
        op.create_unique_constraint(constraint, table, ["tenant_id", "chain_sequence"])
        op.execute(
            f"""
            INSERT INTO audit_chain_heads (tenant_id, chain_name, last_hash, sequence)
            SELECT DISTINCT ON (tenant_id)
                   tenant_id,
                   '{chain_name}',
                   COALESCE(tamper_hash, '{genesis}'),
                   chain_sequence
            FROM {table}
            ORDER BY tenant_id, chain_sequence DESC
            ON CONFLICT (tenant_id, chain_name) DO NOTHING
            """
        )


def downgrade() -> None:
    op.drop_constraint("uq_ingestion_jobs_idempotency_key", "ingestion_jobs", type_="unique")
    op.drop_index("ix_ingestion_jobs_owner_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_requested_by", table_name="ingestion_jobs")
    for column in (
        "updated_at",
        "idempotency_key",
        "attempt_count",
        "current_step",
        "owner_id",
        "requested_by",
    ):
        op.drop_column("ingestion_jobs", column)

    for table, constraint in (
        ("query_traces", "uq_trace_chain_sequence"),
        ("retrieval_evidence", "uq_evidence_chain_sequence"),
        ("policy_decisions", "uq_policy_chain_sequence"),
    ):
        op.drop_constraint(constraint, table, type_="unique")
        op.drop_column(table, "chain_sequence")

    for table in (
        "audit_anchors",
        "audit_chain_heads",
        "operation_jobs",
        "model_catalog",
        "runtime_settings",
        "provider_credentials",
        "admin_audit_events",
        "document_access_templates",
        "group_memberships",
        "groups",
        "auth_sessions",
    ):
        op.drop_table(table)

    op.add_column(
        "auth_users",
        sa.Column("tenant_id", sa.String(128), server_default="default", nullable=False),
    )
    op.add_column(
        "auth_users", sa.Column("role", sa.String(64), server_default="viewer", nullable=False)
    )
    op.execute(
        """
        UPDATE auth_users AS u
        SET tenant_id = m.tenant_id,
            role = CASE WHEN m.role = 'tenant_admin' THEN 'admin' ELSE m.role END
        FROM tenant_memberships AS m
        WHERE m.user_id = u.id
        """
    )
    op.create_index("ix_auth_users_tenant_id", "auth_users", ["tenant_id"])
    op.drop_index("ix_membership_user_active", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_index("ix_auth_users_email", table_name="auth_users")
    for column in (
        "last_login_at",
        "updated_at",
        "created_at",
        "token_version",
        "must_change_password",
        "is_super_admin",
        "is_active",
        "email",
        "display_name",
    ):
        op.drop_column("auth_users", column)
    op.drop_table("tenants")
