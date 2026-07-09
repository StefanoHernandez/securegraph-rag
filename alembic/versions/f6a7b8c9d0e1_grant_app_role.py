"""Grant least-privilege application role for PostgreSQL RLS enforcement."""

from __future__ import annotations

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'securgraph_app') THEN
            CREATE ROLE securgraph_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD 'securgraph_app';
          END IF;
        END
        $$;
        """
    )
    op.execute("GRANT CONNECT ON DATABASE securgraph TO securgraph_app")
    op.execute("GRANT USAGE ON SCHEMA public TO securgraph_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO securgraph_app"
    )
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO securgraph_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO securgraph_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO securgraph_app"
    )


def downgrade() -> None:
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM securgraph_app")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM securgraph_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM securgraph_app")
    op.execute("REVOKE CONNECT ON DATABASE securgraph FROM securgraph_app")
    op.execute("DROP ROLE IF EXISTS securgraph_app")
