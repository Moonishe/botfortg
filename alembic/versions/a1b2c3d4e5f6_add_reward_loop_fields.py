"""add reward loop fields

Revision ID: a1b2c3d4e5f6
Revises: 7b7814daa545
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: str = "7b7814daa545"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # trajectories columns
    traj_cols = {c["name"] for c in inspector.get_columns("trajectories")}
    if "reward_value" not in traj_cols:
        op.add_column(
            "trajectories", sa.Column("reward_value", sa.Float(), nullable=True)
        )
    if "reflection" not in traj_cols:
        op.add_column("trajectories", sa.Column("reflection", sa.Text(), nullable=True))
    if "step_index" not in traj_cols:
        op.add_column(
            "trajectories",
            sa.Column("step_index", sa.Integer(), nullable=False, server_default="0"),
        )
    if "value_estimate" not in traj_cols:
        op.add_column(
            "trajectories", sa.Column("value_estimate", sa.Float(), nullable=True)
        )

    # composite index for backprop (user_id, created_at) — perf from C3
    existing_idx = {i["name"] for i in inspector.get_indexes("trajectories")}
    if "ix_traj_user_created" not in existing_idx:
        op.create_index(
            "ix_traj_user_created", "trajectories", ["user_id", "created_at"]
        )

    # skills columns
    skill_cols = {c["name"] for c in inspector.get_columns("skills")}
    if "policy_signature" not in skill_cols:
        op.add_column(
            "skills", sa.Column("policy_signature", sa.String(64), nullable=True)
        )
    if "induction_gain" not in skill_cols:
        op.add_column("skills", sa.Column("induction_gain", sa.Float(), nullable=True))
    if "episode_count" not in skill_cols:
        op.add_column(
            "skills",
            sa.Column(
                "episode_count", sa.Integer(), nullable=False, server_default="0"
            ),
        )
    if "eta_alpha" not in skill_cols:
        op.add_column(
            "skills",
            sa.Column("eta_alpha", sa.Float(), nullable=False, server_default="1.0"),
        )
    if "eta_beta" not in skill_cols:
        op.add_column(
            "skills",
            sa.Column("eta_beta", sa.Float(), nullable=False, server_default="1.0"),
        )

    # policy_signature index
    skill_idx = {i["name"] for i in inspector.get_indexes("skills")}
    if "ix_skills_policy_signature" not in skill_idx:
        op.create_index("ix_skills_policy_signature", "skills", ["policy_signature"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    skill_idx = {i["name"] for i in inspector.get_indexes("skills")}
    if "ix_skills_policy_signature" in skill_idx:
        op.drop_index("ix_skills_policy_signature", table_name="skills")

    skill_cols = {c["name"] for c in inspector.get_columns("skills")}
    for col in [
        "eta_beta",
        "eta_alpha",
        "episode_count",
        "induction_gain",
        "policy_signature",
    ]:
        if col in skill_cols:
            op.drop_column("skills", col)

    traj_idx = {i["name"] for i in inspector.get_indexes("trajectories")}
    if "ix_traj_user_created" in traj_idx:
        op.drop_index("ix_traj_user_created", table_name="trajectories")

    traj_cols = {c["name"] for c in inspector.get_columns("trajectories")}
    for col in ["value_estimate", "step_index", "reflection", "reward_value"]:
        if col in traj_cols:
            op.drop_column("trajectories", col)
