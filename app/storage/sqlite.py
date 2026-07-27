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
                last_active REAL NOT NULL
            )
        """)
        conn.commit()

# ---------------------------------------------------------------------------
# 1. Processed messages helper functions
# ---------------------------------------------------------------------------

def is_message_processed(msg_id: str) -> bool:
    """Check if a message ID has already been processed to prevent duplicates."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM processed_messages WHERE msg_id = ?", (msg_id,))
        return cursor.fetchone() is not None

def add_processed_message(msg_id: str) -> None:
    """Add a message ID to the processed list."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO processed_messages (msg_id, processed_at) VALUES (?, ?)",
                (msg_id, time.time())
            )
            conn.commit()
    except Exception as e:
        logger.error("Failed to add processed message %s: %s", msg_id, e)

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

def get_role_session(session_id: str, ttl: float) -> tuple[str | None, str | None]:
    """
    Retrieve selected role and BQCA conversation name for a session ID.
    Returns (role, conversation_name) or (None, None) if expired/not found.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT selected_role, conversation_name, last_active FROM role_sessions WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None, None
        
        role, convo_name, last_active = row
        if time.time() - last_active > ttl:
            # Session expired, delete it
            cursor.execute("DELETE FROM role_sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return None, None
        
        # Refresh last active timestamp
        cursor.execute(
            "UPDATE role_sessions SET last_active = ? WHERE session_id = ?",
            (time.time(), session_id)
        )
        conn.commit()
        return role, convo_name

def save_role_session(session_id: str, role: str, conversation_name: str | None = None) -> None:
    """Save or update role and optional conversation name for a session ID."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        if conversation_name is None:
            # Do an upsert: preserve existing conversation_name if we are only changing/saving the role
            cursor.execute(
                "SELECT conversation_name FROM role_sessions WHERE session_id = ?",
                (session_id,)
            )
            existing = cursor.fetchone()
            convo_to_save = existing[0] if existing else None
        else:
            convo_to_save = conversation_name

        cursor.execute(
            """
            INSERT OR REPLACE INTO role_sessions (session_id, selected_role, conversation_name, last_active)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, convo_to_save, time.time())
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
