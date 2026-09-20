"""
ROSE Persistent Memory Module
Standalone SQLite-backed memory system for the ROSE voice assistant.

Design:
  - MemoryManager class is fully self-contained — no imports from rose_app.
  - All public methods catch exceptions and never crash the caller.
  - Database auto-initialises on first use; missing/corrupt DB is handled gracefully.
  - Retrieval uses keyword overlap + importance weighting (can be upgraded to
    embeddings later without changing the public API).

Database: rose_memory.db (created automatically next to this file)
"""

import os
import re
import sqlite3
import threading
import time
import logging

logger = logging.getLogger("rose.memory")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rose_memory.db")

# Stop-words ignored during keyword matching (keeps retrieval focused)
_STOP_WORDS = frozenset(
    "a an the is are was were do does did has have had i you he she it we they "
    "me my your his her our their what which who whom this that these those "
    "am be been being about into from for of with on at by to in not no so "
    "if or and but can could would should will just also very really".split()
)


def _tokenize(text: str) -> list[str]:
    """Lowercase + split into meaningful words (no stop-words, min 3 chars)."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) >= 3]


def _parse_dt(dt_string: str) -> float:
    """Parse SQLite datetime string to timestamp. Returns 0 on failure."""
    try:
        return time.mktime(time.strptime(dt_string, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


class MemoryManager:
    """Thread-safe persistent memory backed by SQLite."""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._available = False
        self._init_db()

    # ------------------------------------------------------------------ #
    #  Initialisation
    # ------------------------------------------------------------------ #

    def _init_db(self):
        """Create the database and tables if they don't exist."""
        try:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category    TEXT    NOT NULL DEFAULT 'fact',
                    content     TEXT    NOT NULL,
                    tags        TEXT    NOT NULL DEFAULT '',
                    importance  INTEGER NOT NULL DEFAULT 5,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                    last_accessed TEXT,
                    active      INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_active
                ON memories(active)
            """)
            conn.commit()
            conn.close()
            self._available = True
            logger.info(f"Memory database ready: {self._db_path}")
        except Exception as e:
            logger.error(f"Memory DB init failed: {e}")
            self._available = False

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=5)

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """True if the database is operational."""
        return self._available

    def store(self, content: str, category: str = "fact",
              tags: str = "", importance: int = 5) -> bool:
        """
        Store a new memory.

        Args:
            content:    The text to remember.
            category:   One of 'name', 'preference', 'fact', 'context'.
            tags:       Comma-separated keywords for retrieval.
            importance: 1-10 (higher = more likely to be recalled).

        Returns:
            True on success, False on failure.
        """
        if not self._available:
            return False
        try:
            # Auto-generate tags from content if none provided
            if not tags:
                tokens = _tokenize(content)
                tags = ",".join(tokens[:8])

            with self._lock:
                conn = self._connect()
                # Check for duplicate (same content, even if previously deleted)
                cur = conn.execute(
                    "SELECT id, active FROM memories WHERE content = ? ORDER BY active DESC LIMIT 1",
                    (content.strip(),),
                )
                existing = cur.fetchone()
                if existing:
                    if existing[1] == 0:  # was soft-deleted — reactivate
                        conn.execute(
                            "UPDATE memories SET active = 1, updated_at = datetime('now') WHERE id = ?",
                            (existing[0],),
                        )
                        conn.commit()
                        logger.debug(f"Memory reactivated (was previously deleted): {content[:50]}")
                    else:
                        logger.debug(f"Memory already exists, skipping: {content[:50]}")
                    conn.close()
                    return True

                conn.execute(
                    """INSERT INTO memories (category, content, tags, importance)
                       VALUES (?, ?, ?, ?)""",
                    (category, content.strip(), tags, max(1, min(10, importance))),
                )
                conn.commit()
                conn.close()
            logger.info(f"Memory stored [{category}]: {content[:60]}")
            return True
        except Exception as e:
            logger.error(f"Memory store failed: {e}")
            return False

    def recall(self, query: str, limit: int = 10) -> list[dict]:
        """
        Retrieve memories relevant to the query.

        Always includes high-importance memories (>= 8).
        Remaining slots filled by keyword overlap scoring + recency bonus.

        Returns:
            List of dicts: [{id, category, content, importance, created_at}, ...]
        """
        if not self._available:
            return []
        try:
            with self._lock:
                conn = self._connect()
                conn.row_factory = sqlite3.Row

                # 1) Always-fetch: high importance, name/preference, AND most recent chats
                always = conn.execute(
                    """SELECT * FROM memories
                       WHERE active = 1 AND (importance >= 8 OR category IN ('name', 'preference'))
                       ORDER BY importance DESC, updated_at DESC"""
                ).fetchall()

                # Also always include the 5 most recent chat memories
                recent_chats = conn.execute(
                    """SELECT * FROM memories
                       WHERE active = 1 AND category = 'chat'
                       ORDER BY created_at DESC LIMIT 5"""
                ).fetchall()

                # Merge, avoiding duplicates
                always_ids = {row["id"] for row in always}
                for row in recent_chats:
                    if row["id"] not in always_ids:
                        always.append(row)
                        always_ids.add(row["id"])

                # 2) Keyword-match the rest
                keywords = _tokenize(query)
                scored = []
                if keywords:
                    all_active = conn.execute(
                        "SELECT * FROM memories WHERE active = 1 ORDER BY updated_at DESC"
                    ).fetchall()

                    always_ids = {row["id"] for row in always}
                    for row in all_active:
                        if row["id"] in always_ids:
                            continue
                        memory_tags = set(_tokenize(row["tags"] + " " + row["content"]))
                        overlap = len(set(keywords) & memory_tags)
                        # Recency bonus: newer memories score higher
                        age_hours = (time.time() - _parse_dt(row["created_at"])) / 3600
                        recency_bonus = max(0, 5 - age_hours / 24)  # 5pts for today, fades over 5 days
                        if overlap > 0:
                            score = overlap * 2 + row["importance"] * 0.5 + recency_bonus
                            scored.append((score, row))
                        elif recency_bonus > 1 and row["category"] == "chat":
                            # Recent chat memories with no keyword match still get a small score
                            scored.append((recency_bonus * 0.3, row))

                scored.sort(key=lambda x: x[0], reverse=True)

                # Merge: always-fetch first, then top scored up to limit
                result = []
                seen_ids = set()
                for row in always:
                    if len(result) >= limit:
                        break
                    result.append(dict(row))
                    seen_ids.add(row["id"])
                for score, row in scored:
                    if len(result) >= limit:
                        break
                    if row["id"] not in seen_ids:
                        result.append(dict(row))
                        seen_ids.add(row["id"])

                # Touch last_accessed for returned memories
                if result:
                    ids = [r["id"] for r in result]
                    placeholders = ",".join("?" * len(ids))
                    conn.execute(
                        f"""UPDATE memories
                            SET last_accessed = datetime('now')
                            WHERE id IN ({placeholders})""",
                        ids,
                    )
                    conn.commit()

                conn.close()
            return result
        except Exception as e:
            logger.error(f"Memory recall failed: {e}")
            return []

    def get_all(self) -> list[dict]:
        """Return all active memories (for UI display)."""
        if not self._available:
            return []
        try:
            with self._lock:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT id, category, content, tags, importance, created_at
                       FROM memories WHERE active = 1
                       ORDER BY importance DESC, created_at DESC"""
                ).fetchall()
                conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Memory get_all failed: {e}")
            return []

    def delete(self, memory_id: int) -> bool:
        """Soft-delete a single memory by ID."""
        if not self._available:
            return False
        try:
            with self._lock:
                conn = self._connect()
                conn.execute(
                    "UPDATE memories SET active = 0, updated_at = datetime('now') WHERE id = ?",
                    (memory_id,),
                )
                conn.commit()
                conn.close()
            logger.info(f"Memory #{memory_id} deleted")
            return True
        except Exception as e:
            logger.error(f"Memory delete failed: {e}")
            return False

    def delete_by_keyword(self, keyword: str) -> int:
        """Soft-delete all memories whose content contains the keyword. Returns count."""
        if not self._available:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute(
                    """UPDATE memories SET active = 0, updated_at = datetime('now')
                       WHERE active = 1 AND content LIKE ?""",
                    (f"%{keyword}%",),
                )
                conn.commit()
                count = cur.rowcount
                conn.close()
            logger.info(f"Deleted {count} memories matching '{keyword}'")
            return count
        except Exception as e:
            logger.error(f"Memory delete_by_keyword failed: {e}")
            return 0

    def clear(self) -> int:
        """Soft-delete ALL active memories. Returns count deleted."""
        if not self._available:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute(
                    "UPDATE memories SET active = 0, updated_at = datetime('now') WHERE active = 1"
                )
                conn.commit()
                count = cur.rowcount
                conn.close()
            logger.info(f"All memories cleared ({count} removed)")
            return count
        except Exception as e:
            logger.error(f"Memory clear failed: {e}")
            return 0

    def count(self) -> int:
        """Return number of active memories."""
        if not self._available:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute("SELECT COUNT(*) FROM memories WHERE active = 1").fetchone()
                conn.close()
            return cur[0]
        except Exception as e:
            logger.error(f"Memory count failed: {e}")
            return 0

    def format_for_prompt(self, query: str, max_tokens: int = 400) -> str:
        """
        Return a compact text block of relevant memories for injection into the LLM prompt.
        Keeps output under roughly max_tokens words.
        """
        memories = self.recall(query, limit=10)
        if not memories:
            return ""

        # Separate high-importance facts from casual chat memories
        facts = []
        chats = []
        for m in memories:
            if m["category"] in ("name", "preference") or m["importance"] >= 7:
                facts.append(m)
            elif m["category"] == "chat":
                chats.append(m)
            else:
                facts.append(m)

        lines = []
        total_words = 0

        if facts:
            lines.append("USER FACTS:")
            total_words += 2
            for m in facts:
                line = f"- {m['content']}"
                total_words += len(line.split())
                if total_words > max_tokens:
                    break
                lines.append(line)

        if chats and total_words < max_tokens:
            lines.append("RECENT CONVERSATION:")
            total_words += 2
            for m in chats:
                line = f"- User said: \"{m['content']}\""
                total_words += len(line.split())
                if total_words > max_tokens:
                    break
                lines.append(line)

        return "\n".join(lines)
