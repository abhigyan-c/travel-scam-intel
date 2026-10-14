"""locations and sources"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "country",
                "city",
                "neighborhood",
                "area",
                "point_of_interest",
                name="location_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("is_provisional", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'", name="ck_country_code"
        ),
        sa.CheckConstraint("type <> 'country' OR parent_id IS NULL", name="ck_country_is_root"),
        sa.CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="ck_coordinate_pair"),
        sa.CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_latitude"),
        sa.CheckConstraint("length(trim(canonical_name)) > 0", name="ck_location_canonical_name"),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_location_name"),
        sa.CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_longitude"),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="ck_location_not_self_parent"
        ),
        sa.ForeignKeyConstraint(["parent_id"], ["locations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_id",
            "canonical_name",
            "type",
            name="uq_location_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_locations_canonical_name"), "locations", ["canonical_name"], unique=False
    )
    op.create_index(op.f("ix_locations_country_code"), "locations", ["country_code"], unique=False)
    op.create_index(op.f("ix_locations_parent_id"), "locations", ["parent_id"], unique=False)
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=200), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("author_identifier", sa.String(length=500), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(
                "official_advisory",
                "news",
                "blog",
                "forum",
                "user_report",
                "other",
                name="source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "canonical_url IS NULL OR length(trim(canonical_url)) > 0", name="ck_source_url"
        ),
        sa.CheckConstraint("length(trim(platform)) > 0", name="ck_source_platform"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_url"),
    )
    op.create_table(
        "location_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("alias", sa.String(length=200), nullable=False),
        sa.Column("normalized_alias", sa.String(length=200), nullable=False),
        sa.CheckConstraint("length(trim(alias)) > 0", name="ck_alias_name"),
        sa.CheckConstraint("length(trim(normalized_alias)) > 0", name="ck_normalized_alias"),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "normalized_alias", name="uq_location_alias"),
    )
    op.create_index(
        op.f("ix_location_aliases_location_id"), "location_aliases", ["location_id"], unique=False
    )
    op.create_index(
        op.f("ix_location_aliases_normalized_alias"),
        "location_aliases",
        ["normalized_alias"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_location_aliases_normalized_alias"), table_name="location_aliases")
    op.drop_index(op.f("ix_location_aliases_location_id"), table_name="location_aliases")
    op.drop_table("location_aliases")
    op.drop_table("sources")
    op.drop_index(op.f("ix_locations_parent_id"), table_name="locations")
    op.drop_index(op.f("ix_locations_country_code"), table_name="locations")
    op.drop_index(op.f("ix_locations_canonical_name"), table_name="locations")
    op.drop_table("locations")
