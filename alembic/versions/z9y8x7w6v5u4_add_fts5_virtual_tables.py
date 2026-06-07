"""Add FTS5 virtual tables for messages, memories, and agent_session_messages

Captures the raw SQL that was previously only in src/db/session.py's init_db()
so that FTS5 tables are tracked in Alembic.

Revision ID: z9y8x7w6v5u4
Revises: fb56dd543d87
Create Date: 2026-05-31 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "z9y8x7w6v5u4"
down_revision: Union[str, Sequence[str], None] = "fb56dd543d87"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create FTS5 virtual tables and their sync triggers."""

    # --- messages_fts (external-content, synced via triggers) ---
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            text,
            transcript,
            extracted_text,
            sender_name,
            content='messages',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2 categories ''L* N* Co'''
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
            VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
            VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
            VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
            INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
            VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
        END;
        """
    )

    # --- agent_session_messages_fts (external-content, no triggers — reads via content_rowid) ---
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_session_messages_fts USING fts5(
            content, role, session_id UNINDEXED,
            content='agent_session_messages', content_rowid='id',
            tokenize='unicode61 remove_diacritics 2 categories ''L* N* Co'''
        )
        """
    )

    # --- memories_fts (external-content, synced via triggers) ---
    op.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            fact, sentiment, cluster_topic,
            content='memories',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2 categories ''L* N* Co'''
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, fact, sentiment, cluster_topic)
            VALUES (new.id, new.fact, new.sentiment, new.cluster_topic);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, fact, sentiment, cluster_topic)
            VALUES('delete', old.id, old.fact, old.sentiment, old.cluster_topic);
        END;
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, fact, sentiment, cluster_topic)
            VALUES('delete', old.id, old.fact, old.sentiment, old.cluster_topic);
            INSERT INTO memories_fts(rowid, fact, sentiment, cluster_topic)
            VALUES (new.id, new.fact, new.sentiment, new.cluster_topic);
        END;
        """
    )


def downgrade() -> None:
    """Drop FTS5 virtual tables and their triggers."""

    # Drop triggers first (order matters for SQLite)
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ai")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS messages_fts_au")
    op.execute("DROP TRIGGER IF EXISTS memories_fts_ai")
    op.execute("DROP TRIGGER IF EXISTS memories_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS memories_fts_au")

    # Drop FTS5 virtual tables (this drops their internal *_{data,docsize,config,idx} tables too)
    op.execute("DROP TABLE IF EXISTS messages_fts")
    op.execute("DROP TABLE IF EXISTS agent_session_messages_fts")
    op.execute("DROP TABLE IF EXISTS memories_fts")
