import os
import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

# Resolve the absolute path to the database file in the project root directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "bqca_sessions.db")

def init_db() -> None:
    """Initialise SQLite tables if they do not exist."""
    logger.info("Initialising SQLite database at: %s", DB_PATH)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")  # Enable WAL mode for better concurrency

        # 1. Processed messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                msg_id TEXT PRIMARY KEY,
                processed_at REAL NOT NULL
            )
        """)

        # 2. Chat sessions table (Feishu webhook conversations)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_key TEXT PRIMARY KEY,
                conversation_name TEXT NOT NULL,
                last_active REAL NOT NULL
            )
        """)

        # 3. Role sessions table (API query demo sessions)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS role_sessions (
                session_id TEXT PRIMARY KEY,
                selected_role TEXT NOT NULL,
                conversation_name TEXT,
                last_domain TEXT,
                last_active REAL NOT NULL
            )
        """)

        role_session_columns = {row[1] for row in conn.execute("PRAGMA table_info(role_sessions)")}
        if "last_domain" not in role_session_columns:
            conn.execute("ALTER TABLE role_sessions ADD COLUMN last_domain TEXT;")

        # 4. Chat room types registry table (Group vs P2P)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_room_types (
                chat_id TEXT PRIMARY KEY,
                chat_type TEXT NOT NULL
            )
        """)
        conn.commit()

# ---------------------------------------------------------------------------
# 1. Processed messages helper functions
# ---------------------------------------------------------------------------

def claim_message_processing(msg_id: str, event_id: str = "") -> bool:
    """Atomically claim a Feishu event. Return False when either ID was seen."""
    keys = []
    if msg_id:
        keys.append(msg_id)
    if event_id:
        keys.append(f"event:{event_id}")
    if not keys:
        return True

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("BEGIN IMMEDIATE")
            placeholders = ", ".join("?" for _ in keys)
            existing = conn.execute(
                f"SELECT 1 FROM processed_messages WHERE msg_id IN ({placeholders}) LIMIT 1",
                keys,
            ).fetchone()
            if existing:
                return False

            conn.executemany(
                "INSERT INTO processed_messages (msg_id, processed_at) VALUES (?, ?)",
                [(key, time.time()) for key in keys],
            )
            conn.commit()
            return True
    except Exception as e:
        logger.error("Failed to claim Feishu event %s/%s: %s", event_id, msg_id, e)
        return True

# ---------------------------------------------------------------------------
# 2. Chat sessions (Feishu Webhook) helper functions
# ---------------------------------------------------------------------------

def get_chat_conversation(session_key: str, ttl: float) -> str | None:
    """Retrieve active BQCA conversation for a Feishu chat ID, checking TTL."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT conversation_name, last_active FROM chat_sessions WHERE session_key = ?",
            (session_key,)
        )
        row = cursor.fetchone()
        if row is None:
            return None

        convo_name, last_active = row
        if time.time() - last_active > ttl:
            # Session expired, delete it
            cursor.execute("DELETE FROM chat_sessions WHERE session_key = ?", (session_key,))
            conn.commit()
            return None

        # Refresh last active timestamp
        cursor.execute(
            "UPDATE chat_sessions SET last_active = ? WHERE session_key = ?",
            (time.time(), session_key)
        )
        conn.commit()
        return convo_name

def save_chat_conversation(session_key: str, conversation_name: str) -> None:
    """Save or update BQCA conversation for a Feishu chat ID."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chat_sessions (session_key, conversation_name, last_active) VALUES (?, ?, ?)",
            (session_key, conversation_name, time.time())
        )
        conn.commit()

# ---------------------------------------------------------------------------
# 3. Role sessions (Web API query Demo) helper functions
# ---------------------------------------------------------------------------

def get_role_session(session_id: str, ttl: float) -> tuple[str | None, str | None, str | None]:
    """
    Retrieve selected role, BQCA conversation name, and last_domain for a session ID.
    Returns (role, conversation_name, last_domain) or (None, None, None) if expired/not found.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_role, conversation_name, last_domain, last_active FROM role_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None, None, None

        role, convo_name, last_domain, last_active = row
        if time.time() - last_active > ttl:
            # Session expired, delete it
            cursor.execute("DELETE FROM role_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return None, None, None

        # Refresh last active timestamp
        cursor.execute(
            "UPDATE role_sessions SET last_active = ? WHERE session_id = ?",
            (time.time(), session_id)
        )
        conn.commit()
        return role, convo_name, last_domain

def save_role_session(session_id: str, role: str, conversation_name: str | None = None, last_domain: str | None = None) -> None:
    """Save or update role, optional conversation name, and optional last_domain for a session ID."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        # Do an upsert: preserve existing values if they are not provided
        cursor.execute(
            "SELECT conversation_name, last_domain FROM role_sessions WHERE session_id = ?",
            (session_id,)
        )
        existing = cursor.fetchone()

        convo_to_save = conversation_name if conversation_name is not None else (existing[0] if existing else None)
        domain_to_save = last_domain if last_domain is not None else (existing[1] if existing else None)

        cursor.execute(
            """
            INSERT OR REPLACE INTO role_sessions (session_id, selected_role, conversation_name, last_domain, last_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, role, convo_to_save, domain_to_save, time.time())
        )
        conn.commit()

def clear_role_conversation(session_id: str) -> None:
    """Clear BQCA conversation name from a role session (e.g. when role switches)."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE role_sessions SET conversation_name = NULL WHERE session_id = ?",
            (session_id,)
        )
        conn.commit()

# ---------------------------------------------------------------------------
# 4. Periodic cleanup helper
# ---------------------------------------------------------------------------

def cleanup_expired_sessions(ttl: float) -> None:
    """Periodic task to remove expired sessions and old processed messages."""
    now = time.time()
    logger.info("Running periodic cleanup of expired sessions from SQLite...")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Delete expired chat sessions
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_sessions WHERE ? - last_active > ?", (now, ttl))
            chat_deleted = cursor.rowcount

            # Delete expired role sessions
            cursor.execute("DELETE FROM role_sessions WHERE ? - last_active > ?", (now, ttl))
            role_deleted = cursor.rowcount

            # Delete very old processed messages (older than 24 hours) to keep DB compact
            cursor.execute("DELETE FROM processed_messages WHERE ? - processed_at > 86400", (now,))
            msg_deleted = cursor.rowcount

            conn.commit()
            logger.info(
                "Cleanup finished. Deleted %d chat sessions, %d role sessions, %d old processed messages.",
                chat_deleted, role_deleted, msg_deleted
            )
    except Exception as e:
        logger.error("Failed to run periodic SQLite cleanup: %s", e)


def save_chat_type(chat_id: str, chat_type: str) -> None:
    """Save the chat room type (e.g. 'p2p' or 'group') to prevent cross-channel hijackings."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chat_room_types (chat_id, chat_type) VALUES (?, ?)",
                (chat_id, chat_type)
            )
            conn.commit()
    except Exception as e:
        logger.error("Failed to save chat type for %s: %s", chat_id, e)


def get_chat_type(chat_id: str) -> str | None:
    """Retrieve the stored chat room type ('p2p' or 'group'), or None if not registered."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_type FROM chat_room_types WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error("Failed to get chat type for %s: %s", chat_id, e)
        return None
