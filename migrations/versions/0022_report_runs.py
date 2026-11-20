"""Durable report roots and bounded watches; conservative legacy adoption."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE TABLE research_runs (
      id uuid PRIMARY KEY, location_id uuid NOT NULL REFERENCES locations(id) ON DELETE RESTRICT,
      status varchar(20) NOT NULL, coverage varchar(20) NOT NULL,
      created_at timestamptz, started_at timestamptz, finished_at timestamptz,
      last_result_update_at timestamptz, last_completed_at timestamptz,
      pattern_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
      observations jsonb NOT NULL DEFAULT '{}'::jsonb,
      stop_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT ck_research_run_status CHECK(status IN
        ('queued','researching','completed','partial','failed')),
      CONSTRAINT ck_run_coverage CHECK(coverage IN ('complete','partial','unknown')),
      CONSTRAINT ck_run_patterns CHECK(jsonb_typeof(pattern_ids)='array'),
      CONSTRAINT ck_run_observations CHECK(jsonb_typeof(observations)='object'),
      CONSTRAINT ck_run_reasons CHECK(jsonb_typeof(stop_reasons)='array'))""")
    op.create_index("ix_research_runs_location_id", "research_runs", ["location_id"])
    op.add_column("location_research_states", sa.Column("current_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_location_current_run",
        "location_research_states",
        "research_runs",
        ["current_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_location_research_states_current_run_id", "location_research_states", ["current_run_id"]
    )
    op.add_column("research_jobs", sa.Column("report_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_job_report_run",
        "research_jobs",
        "research_runs",
        ["report_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_research_jobs_report_run_id", "research_jobs", ["report_run_id"])
    # No historical check/result timestamps are fabricated. Preserve the legacy tables
    # and cursor budgets. Only the demonstrably owned active job is linked.
    op.execute("""INSERT INTO research_runs(id,location_id,status,coverage)
      SELECT gen_random_uuid(),location_id,
        CASE WHEN status='researching' THEN 'queued' WHEN status='failed' THEN 'failed'
        WHEN progress->>'complete'='true' AND enriched_at IS NOT NULL THEN 'completed'
        ELSE 'partial' END,
        CASE WHEN progress->>'complete'='true' AND enriched_at IS NOT NULL THEN 'complete'
        WHEN progress <> '{}'::jsonb THEN 'partial' ELSE 'unknown' END
      FROM location_research_states WHERE status <> 'never_researched'""")
    op.execute("""UPDATE location_research_states s SET current_run_id=r.id
      FROM research_runs r WHERE r.location_id=s.location_id""")
    op.execute("""WITH RECURSIVE paths(a,d,seen) AS (
      SELECT id,id,ARRAY[id] FROM locations UNION ALL
      SELECT p.a,l.id,p.seen||l.id FROM paths p JOIN locations l ON l.parent_id=p.d
      WHERE NOT l.id=ANY(p.seen) AND cardinality(p.seen)<32)
      UPDATE research_runs r SET pattern_ids=COALESCE((SELECT jsonb_agg(x.id::text) FROM
        (SELECT h.id FROM hypotheses h WHERE EXISTS(SELECT 1 FROM paths p WHERE
          (p.a=r.location_id AND p.d=h.primary_location_id) OR
          (p.d=r.location_id AND p.a=h.primary_location_id))
          ORDER BY h.id LIMIT 1000) x),'[]'::jsonb)""")
    op.execute("""UPDATE research_jobs j SET report_run_id=s.current_run_id
      FROM location_research_states s WHERE s.active_job_id=j.id
      AND j.kind='location_research' AND j.payload->>'location_id'=s.location_id::text""")
    op.execute("""CREATE TABLE report_watches (
      id uuid PRIMARY KEY,run_id uuid NOT NULL REFERENCES research_runs(id) ON DELETE RESTRICT,
      expires_at timestamptz NOT NULL, waits integer NOT NULL,
      CONSTRAINT ck_watch_waits CHECK(waits BETWEEN 0 AND 60))""")
    op.create_index("ix_report_watches_run_id", "report_watches", ["run_id"])
    op.create_index("ix_report_watches_expires_at", "report_watches", ["expires_at"])


def downgrade():
    op.drop_table("report_watches")
    op.drop_constraint("fk_job_report_run", "research_jobs", type_="foreignkey")
    op.drop_column("research_jobs", "report_run_id")
    op.drop_constraint("fk_location_current_run", "location_research_states", type_="foreignkey")
    op.drop_column("location_research_states", "current_run_id")
    op.drop_table("research_runs")
