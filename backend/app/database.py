import secrets
import sqlite3
import re
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from backend.app.security import hash_access_token

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at ON auth_sessions(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_house_members_unique_name
ON house_members(house_id, user_name);
CREATE INDEX IF NOT EXISTS idx_house_members_user_name ON house_members(user_name);
"""

LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS houses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS house_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    role TEXT NOT NULL,
    FOREIGN KEY (house_id) REFERENCES houses(id)
);
CREATE TABLE IF NOT EXISTS chores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    assigned_to TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shopping_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    added_by TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    house_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    debtor TEXT NOT NULL,
    creditor TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    total_amount REAL,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    deleted_by INTEGER REFERENCES users(id)
);
"""


class DuplicateUserError(Exception):
    def __init__(self, field: str):
        self.field = field
        super().__init__(field)


class HouseNotFoundError(Exception):
    pass


class AlreadyHouseMemberError(Exception):
    pass


class NotHouseMemberError(Exception):
    pass


class NotHouseOwnerError(Exception):
    pass


class OwnerCannotLeaveError(Exception):
    pass


class InvalidOwnerTransferError(Exception):
    pass


class HouseConfirmationError(Exception):
    pass


class AccountOwnershipConflictError(Exception):
    def __init__(self, houses: list[dict[str, Any]]):
        self.houses = houses
        super().__init__("Account owns shared houses")


class ShoppingItemNotFoundError(Exception):
    pass


class ShoppingItemNotCompletedError(Exception):
    pass


class SettlementNotFoundError(Exception):
    pass


class SettlementAlreadyDeletedError(Exception):
    pass


class SettlementDeleteForbiddenError(Exception):
    pass


class InvalidSettlementParticipantsError(Exception):
    pass


class Database:
    INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self._backup_before_house_invite_migration()
        self._backup_before_chore_migration()
        self._backup_before_account_deletion_migration()
        self._backup_before_settlement_migration()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as connection:
            connection.executescript(LEGACY_SCHEMA)
            connection.executescript(AUTH_SCHEMA)
            member_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(house_members)")
            }
            if "user_id" not in member_columns:
                connection.execute(
                    "ALTER TABLE house_members ADD COLUMN user_id INTEGER REFERENCES users(id)"
                )
            house_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(houses)")
            }
            if "owner_id" not in house_columns:
                connection.execute(
                    "ALTER TABLE houses ADD COLUMN owner_id INTEGER REFERENCES users(id)"
                )
            if "created_at" not in house_columns:
                connection.execute("ALTER TABLE houses ADD COLUMN created_at TEXT")
            if "joined_at" not in member_columns:
                connection.execute("ALTER TABLE house_members ADD COLUMN joined_at TEXT")

            connection.execute(
                """
                UPDATE house_members
                SET user_id = (
                    SELECT users.id FROM users
                    WHERE users.display_name = house_members.user_name
                )
                WHERE user_id IS NULL
                  AND EXISTS (
                    SELECT 1 FROM users
                    WHERE users.display_name = house_members.user_name
                  )
                """
            )
            connection.execute(
                """
                UPDATE houses
                SET owner_id = (
                    SELECT member.user_id
                    FROM house_members member
                    WHERE member.house_id = houses.id
                      AND member.role = 'owner'
                      AND member.user_id IS NOT NULL
                    ORDER BY member.id ASC
                    LIMIT 1
                )
                WHERE owner_id IS NULL
                """
            )
            now = self._now()
            connection.execute(
                "UPDATE houses SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
                (now,),
            )
            connection.execute(
                "UPDATE house_members SET joined_at = ? WHERE joined_at IS NULL OR joined_at = ''",
                (now,),
            )

            used_codes: set[str] = set()
            invalid_houses = connection.execute(
                "SELECT id, invite_code FROM houses ORDER BY id"
            ).fetchall()
            for house in invalid_houses:
                current_code = str(house["invite_code"]).upper()
                if re.fullmatch(r"[A-Z0-9]{6,8}", current_code) and current_code not in used_codes:
                    if current_code != house["invite_code"]:
                        connection.execute(
                            "UPDATE houses SET invite_code = ? WHERE id = ?",
                            (current_code, int(house["id"])),
                        )
                    used_codes.add(current_code)
                    continue
                replacement = self._new_invite_code(used_codes)
                connection.execute(
                    "UPDATE houses SET invite_code = ? WHERE id = ?",
                    (replacement, int(house["id"])),
                )
                used_codes.add(replacement)
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_house_members_unique_user
                ON house_members(house_id, user_id)
                WHERE user_id IS NOT NULL
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (1, self._now()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (2, self._now()),
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (3, self._now()),
            )

            chore_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(chores)")
            }
            chore_column_definitions = {
                "description": "TEXT",
                "assignee_user_id": "INTEGER REFERENCES users(id)",
                "scheduled_date": "TEXT",
                "completed_at": "TEXT",
                "created_by": "INTEGER REFERENCES users(id)",
                "created_at": "TEXT",
            }
            for column, definition in chore_column_definitions.items():
                if column not in chore_columns:
                    connection.execute(
                        f"ALTER TABLE chores ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "UPDATE chores SET description = '' WHERE description IS NULL"
            )
            connection.execute(
                """
                UPDATE chores
                SET assignee_user_id = (
                    SELECT member.user_id
                    FROM house_members member
                    WHERE member.house_id = chores.house_id
                      AND member.user_name = chores.assigned_to
                      AND member.user_id IS NOT NULL
                    LIMIT 1
                )
                WHERE assignee_user_id IS NULL
                """
            )
            connection.execute(
                """
                UPDATE chores
                SET scheduled_date = due_date
                WHERE scheduled_date IS NULL OR scheduled_date = ''
                """
            )
            connection.execute(
                """
                UPDATE chores
                SET created_by = COALESCE(
                    (SELECT houses.owner_id FROM houses WHERE houses.id = chores.house_id),
                    assignee_user_id
                )
                WHERE created_by IS NULL
                """
            )
            connection.execute(
                "UPDATE chores SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
                (now,),
            )
            connection.execute(
                """
                UPDATE chores
                SET completed_at = ?
                WHERE status = '완료' AND completed_at IS NULL
                """,
                (now,),
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chores_house_date ON chores(house_id, scheduled_date)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chores_assignee ON chores(house_id, assignee_user_id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (4, self._now()),
            )

            user_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(users)")
            }
            if "is_deleted" not in user_columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
                )
            if "deleted_at" not in user_columns:
                connection.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (5, self._now()),
            )

            settlement_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(settlements)")
            }
            settlement_column_definitions = {
                "title": "TEXT",
                "total_amount": "REAL",
                "created_by": "INTEGER REFERENCES users(id)",
                "created_at": "TEXT",
                "is_deleted": "INTEGER NOT NULL DEFAULT 0",
                "deleted_at": "TEXT",
                "deleted_by": "INTEGER REFERENCES users(id)",
            }
            for column, definition in settlement_column_definitions.items():
                if column not in settlement_columns:
                    connection.execute(
                        f"ALTER TABLE settlements ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "UPDATE settlements SET title = content WHERE title IS NULL OR title = ''"
            )
            connection.execute(
                "UPDATE settlements SET total_amount = amount WHERE total_amount IS NULL"
            )
            connection.execute(
                "UPDATE settlements SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
                (self._now(),),
            )
            connection.execute(
                """
                UPDATE settlements
                SET created_by = (
                    SELECT member.user_id
                    FROM house_members member
                    WHERE member.house_id = settlements.house_id
                      AND member.user_name = settlements.creditor
                      AND member.user_id IS NOT NULL
                    LIMIT 1
                )
                WHERE created_by IS NULL
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS settlement_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    settlement_id INTEGER NOT NULL REFERENCES settlements(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    amount REAL NOT NULL,
                    payment_status TEXT NOT NULL DEFAULT '미납',
                    UNIQUE(settlement_id, user_id)
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO settlement_participants
                    (settlement_id, user_id, amount, payment_status)
                SELECT settlement.id, member.user_id, settlement.amount,
                       CASE WHEN settlement.status IN ('완료', 'completed')
                            THEN '완료' ELSE '미납' END
                FROM settlements settlement
                JOIN house_members member
                  ON member.house_id = settlement.house_id
                 AND member.user_name = settlement.debtor
                 AND member.user_id IS NOT NULL
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_settlements_house_deleted ON settlements(house_id, is_deleted, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_settlement_participants_settlement ON settlement_participants(settlement_id)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (6, self._now()),
            )

    def create_user(self, email: str, password_hash: str, display_name: str) -> dict[str, Any]:
        with self.session() as connection:
            existing_email = connection.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing_email:
                raise DuplicateUserError("email")

            existing_name = connection.execute(
                "SELECT id FROM users WHERE display_name = ?", (display_name,)
            ).fetchone()
            if existing_name:
                raise DuplicateUserError("display_name")

            cursor = connection.execute(
                """
                INSERT INTO users (email, password_hash, display_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (email, password_hash, display_name, self._now()),
            )
            user_id = int(cursor.lastrowid)
            row = connection.execute(
                "SELECT id, email, display_name FROM users WHERE id = ? AND is_deleted = 0",
                (user_id,),
            ).fetchone()
        return dict(row)

    def get_user_with_password(self, email: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT id, email, display_name, password_hash
                FROM users WHERE email = ? AND is_deleted = 0
                """,
                (email,),
            ).fetchone()
        return dict(row) if row else None

    def create_session(self, user_id: int, token: str, days: int = 30) -> None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=days)
        with self.session() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at <= ?", (now.isoformat(),)
            )
            connection.execute(
                """
                INSERT INTO auth_sessions (user_id, token_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, hash_access_token(token), now.isoformat(), expires_at.isoformat()),
            )

    def get_user_by_token(self, token: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT u.id, u.email, u.display_name
                FROM auth_sessions session
                JOIN users u ON u.id = session.user_id
                WHERE session.token_hash = ? AND session.expires_at > ?
                  AND u.is_deleted = 0
                """,
                (hash_access_token(token), self._now()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        with self.session() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (hash_access_token(token),)
            )

    def get_user_password(self, user_id: int) -> str | None:
        with self.session() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ? AND is_deleted = 0",
                (user_id,),
            ).fetchone()
        return str(row["password_hash"]) if row else None

    def list_houses(self, user_id: int) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT h.id, h.name, h.location, h.invite_code, h.created_by,
                       h.owner_id, h.created_at,
                       COUNT(all_members.id) AS member_count
                FROM houses h
                JOIN house_members own_membership ON own_membership.house_id = h.id
                LEFT JOIN house_members all_members
                  ON all_members.house_id = h.id AND all_members.user_id IS NOT NULL
                WHERE own_membership.user_id = ?
                GROUP BY h.id
                ORDER BY h.id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_house(
        self, name: str, location: str, user_id: int, display_name: str
    ) -> dict[str, Any]:
        with self.session() as connection:
            for _ in range(10):
                invite_code = self._new_invite_code()
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO houses
                            (name, location, invite_code, created_by, owner_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (name, location, invite_code, display_name, user_id, self._now()),
                    )
                    house_id = int(cursor.lastrowid)
                    connection.execute(
                        """
                        INSERT INTO house_members
                            (house_id, user_name, role, user_id, joined_at)
                        VALUES (?, ?, 'owner', ?, ?)
                        """,
                        (house_id, display_name, user_id, self._now()),
                    )
                    row = connection.execute(
                        """
                        SELECT id, name, location, invite_code, created_by,
                               owner_id, created_at, 1 AS member_count
                        FROM houses WHERE id = ?
                        """,
                        (house_id,),
                    ).fetchone()
                    return dict(row)
                except sqlite3.IntegrityError as error:
                    if "invite_code" not in str(error):
                        raise

        raise RuntimeError("고유한 초대코드를 생성하지 못했습니다.")

    def get_house(self, house_id: int, user_id: int) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT h.id, h.name, h.location, h.invite_code, h.created_by,
                       h.owner_id, h.created_at,
                       COUNT(member.id) AS member_count
                FROM houses h
                JOIN house_members own_membership
                  ON own_membership.house_id = h.id AND own_membership.user_id = ?
                LEFT JOIN house_members member
                  ON member.house_id = h.id AND member.user_id IS NOT NULL
                WHERE h.id = ?
                GROUP BY h.id
                """,
                (user_id, house_id),
            ).fetchone()
        return dict(row) if row else None

    def join_house_by_invite(
        self, invite_code: str, user_id: int, display_name: str
    ) -> dict[str, Any]:
        with self.session() as connection:
            house = connection.execute(
                "SELECT id FROM houses WHERE invite_code = ?",
                (invite_code,),
            ).fetchone()
            if house is None:
                raise HouseNotFoundError(invite_code)
            house_id = int(house["id"])
            existing = connection.execute(
                "SELECT id FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            ).fetchone()
            if existing:
                raise AlreadyHouseMemberError(invite_code)
            try:
                connection.execute(
                    """
                    INSERT INTO house_members
                        (house_id, user_name, role, user_id, joined_at)
                    VALUES (?, ?, 'member', ?, ?)
                    """,
                    (house_id, display_name, user_id, self._now()),
                )
            except sqlite3.IntegrityError as error:
                raise AlreadyHouseMemberError(invite_code) from error
            row = connection.execute(
                """
                SELECT h.id, h.name, h.location, h.invite_code, h.created_by,
                       h.owner_id, h.created_at,
                       COUNT(member.id) AS member_count
                FROM houses h
                LEFT JOIN house_members member
                  ON member.house_id = h.id AND member.user_id IS NOT NULL
                WHERE h.id = ?
                GROUP BY h.id
                """,
                (house_id,),
            ).fetchone()
        return dict(row)

    def list_house_members(self, house_id: int) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT member.id, member.house_id, member.user_id,
                       user.display_name, user.email, member.role, member.joined_at
                FROM house_members member
                JOIN users user ON user.id = member.user_id
                WHERE member.house_id = ?
                ORDER BY CASE member.role WHEN 'owner' THEN 0 ELSE 1 END,
                         member.joined_at ASC, member.id ASC
                """,
                (house_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def is_house_member(self, house_id: int, user_id: int) -> bool:
        with self.session() as connection:
            row = connection.execute(
                "SELECT 1 FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            ).fetchone()
        return row is not None

    def leave_house(self, house_id: int, user_id: int) -> None:
        with self.session() as connection:
            house = connection.execute(
                "SELECT id, owner_id FROM houses WHERE id = ?", (house_id,)
            ).fetchone()
            if house is None:
                raise HouseNotFoundError(str(house_id))
            membership = connection.execute(
                "SELECT id, role FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            ).fetchone()
            if membership is None:
                raise NotHouseMemberError()
            if house["owner_id"] == user_id or membership["role"] == "owner":
                raise OwnerCannotLeaveError()
            connection.execute(
                "DELETE FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            )

    def transfer_house_owner(
        self, house_id: int, current_owner_id: int, new_owner_user_id: int
    ) -> dict[str, Any]:
        with self.session() as connection:
            house = connection.execute(
                "SELECT id, owner_id FROM houses WHERE id = ?", (house_id,)
            ).fetchone()
            if house is None:
                raise HouseNotFoundError(str(house_id))
            requester = connection.execute(
                "SELECT role FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, current_owner_id),
            ).fetchone()
            if requester is None:
                raise NotHouseMemberError()
            if house["owner_id"] != current_owner_id or requester["role"] != "owner":
                raise NotHouseOwnerError()
            if new_owner_user_id == current_owner_id:
                raise InvalidOwnerTransferError()
            new_owner = connection.execute(
                """
                SELECT member.id, user.display_name
                FROM house_members member
                JOIN users user ON user.id = member.user_id
                WHERE member.house_id = ? AND member.user_id = ? AND user.is_deleted = 0
                """,
                (house_id, new_owner_user_id),
            ).fetchone()
            if new_owner is None:
                raise InvalidOwnerTransferError()

            connection.execute(
                "UPDATE house_members SET role = 'member' WHERE house_id = ? AND user_id = ?",
                (house_id, current_owner_id),
            )
            connection.execute(
                "UPDATE house_members SET role = 'owner' WHERE house_id = ? AND user_id = ?",
                (house_id, new_owner_user_id),
            )
            connection.execute(
                "UPDATE houses SET owner_id = ?, created_by = ? WHERE id = ?",
                (new_owner_user_id, str(new_owner["display_name"]), house_id),
            )
            row = self._get_house_row(connection, house_id)
        return dict(row)

    def delete_house(
        self, house_id: int, user_id: int, confirmation: str
    ) -> dict[str, int]:
        with self.session() as connection:
            house = connection.execute(
                "SELECT id, name, owner_id FROM houses WHERE id = ?", (house_id,)
            ).fetchone()
            if house is None:
                raise HouseNotFoundError(str(house_id))
            membership = connection.execute(
                "SELECT role FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            ).fetchone()
            if membership is None:
                raise NotHouseMemberError()
            if house["owner_id"] != user_id or membership["role"] != "owner":
                raise NotHouseOwnerError()
            if confirmation != str(house["name"]):
                raise HouseConfirmationError()
            return self._delete_house_in_connection(connection, house_id)

    def get_house_deletion_impact(self, house_id: int, user_id: int) -> dict[str, int]:
        with self.session() as connection:
            house = connection.execute(
                "SELECT id, owner_id FROM houses WHERE id = ?", (house_id,)
            ).fetchone()
            if house is None:
                raise HouseNotFoundError(str(house_id))
            membership = connection.execute(
                "SELECT role FROM house_members WHERE house_id = ? AND user_id = ?",
                (house_id, user_id),
            ).fetchone()
            if membership is None:
                raise NotHouseMemberError()
            if house["owner_id"] != user_id or membership["role"] != "owner":
                raise NotHouseOwnerError()
            return self._count_house_data(connection, house_id)

    def delete_account(self, user_id: int) -> dict[str, Any]:
        with self.session() as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE id = ? AND is_deleted = 0", (user_id,)
            ).fetchone()
            if user is None:
                return {"deleted_solo_houses": 0, "deleted_at": self._now()}

            owned_houses = connection.execute(
                """
                SELECT house.id, house.name, COUNT(member.id) AS member_count
                FROM houses house
                LEFT JOIN house_members member
                  ON member.house_id = house.id AND member.user_id IS NOT NULL
                WHERE house.owner_id = ?
                GROUP BY house.id
                ORDER BY house.id
                """,
                (user_id,),
            ).fetchall()
            blocking = [
                {"id": int(row["id"]), "name": str(row["name"])}
                for row in owned_houses
                if int(row["member_count"]) > 1
            ]
            if blocking:
                raise AccountOwnershipConflictError(blocking)

            deleted_solo_houses = 0
            for row in owned_houses:
                self._delete_house_in_connection(connection, int(row["id"]))
                deleted_solo_houses += 1

            connection.execute("DELETE FROM house_members WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            deleted_at = self._now()
            anonymous_email = f"deleted+{user_id}-{secrets.token_hex(8)}@roomsync.invalid"
            anonymous_name = f"탈퇴한 사용자 {user_id}"
            connection.execute(
                """
                UPDATE users
                SET email = ?, display_name = ?, password_hash = ?,
                    is_deleted = 1, deleted_at = ?
                WHERE id = ?
                """,
                (
                    anonymous_email,
                    anonymous_name,
                    f"deleted${secrets.token_urlsafe(32)}",
                    deleted_at,
                    user_id,
                ),
            )
        return {
            "deleted_solo_houses": deleted_solo_houses,
            "deleted_at": deleted_at,
        }

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None

    def _delete_house_in_connection(
        self, connection: sqlite3.Connection, house_id: int
    ) -> dict[str, int]:
        counts = self._count_house_data(connection, house_id)
        if self._table_exists(connection, "receipt_items") and self._table_exists(
            connection, "receipts"
        ):
            connection.execute(
                "DELETE FROM receipt_items WHERE receipt_id IN (SELECT id FROM receipts WHERE house_id = ?)",
                (house_id,),
            )
        if self._table_exists(connection, "settlement_participants") and self._table_exists(
            connection, "settlements"
        ):
            connection.execute(
                "DELETE FROM settlement_participants WHERE settlement_id IN (SELECT id FROM settlements WHERE house_id = ?)",
                (house_id,),
            )
        if self._table_exists(connection, "settlement_details") and self._table_exists(
            connection, "settlements"
        ):
            connection.execute(
                "DELETE FROM settlement_details WHERE settlement_id IN (SELECT id FROM settlements WHERE house_id = ?)",
                (house_id,),
            )
        for table in ("receipts", "settlements", "chores", "shopping_items", "house_members"):
            if self._table_exists(connection, table):
                connection.execute(f"DELETE FROM {table} WHERE house_id = ?", (house_id,))
        connection.execute("DELETE FROM houses WHERE id = ?", (house_id,))
        return counts

    def _count_house_data(
        self, connection: sqlite3.Connection, house_id: int
    ) -> dict[str, int]:
        counts = {
            "receipt_items": 0,
            "receipts": 0,
            "settlement_details": 0,
            "settlement_participants": 0,
            "settlements": 0,
            "chores": 0,
            "shopping_items": 0,
            "house_members": 0,
        }
        if self._table_exists(connection, "receipt_items") and self._table_exists(
            connection, "receipts"
        ):
            counts["receipt_items"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM receipt_items
                    WHERE receipt_id IN (SELECT id FROM receipts WHERE house_id = ?)
                    """,
                    (house_id,),
                ).fetchone()[0]
            )
        if self._table_exists(connection, "settlement_participants") and self._table_exists(
            connection, "settlements"
        ):
            counts["settlement_participants"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM settlement_participants
                    WHERE settlement_id IN (SELECT id FROM settlements WHERE house_id = ?)
                    """,
                    (house_id,),
                ).fetchone()[0]
            )
        if self._table_exists(connection, "settlement_details") and self._table_exists(
            connection, "settlements"
        ):
            counts["settlement_details"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM settlement_details
                    WHERE settlement_id IN (SELECT id FROM settlements WHERE house_id = ?)
                    """,
                    (house_id,),
                ).fetchone()[0]
            )
        for table in ("receipts", "settlements", "chores", "shopping_items", "house_members"):
            if not self._table_exists(connection, table):
                continue
            counts[table] = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE house_id = ?", (house_id,)
                ).fetchone()[0]
            )
        return counts

    @staticmethod
    def _get_house_row(connection: sqlite3.Connection, house_id: int) -> sqlite3.Row:
        return connection.execute(
            """
            SELECT h.id, h.name, h.location, h.invite_code, h.created_by,
                   h.owner_id, h.created_at,
                   COUNT(member.id) AS member_count
            FROM houses h
            LEFT JOIN house_members member
              ON member.house_id = h.id AND member.user_id IS NOT NULL
            WHERE h.id = ?
            GROUP BY h.id
            """,
            (house_id,),
        ).fetchone()

    def list_validated_chores(self, house_id: int) -> list[dict[str, Any]]:
        """Return only chores assigned to a registered member of the same house."""
        with self.session() as connection:
            rows = connection.execute(
                f"""
                {self._chore_select_sql()}
                WHERE chore.house_id = ?
                ORDER BY chore.scheduled_date ASC, chore.id ASC
                """,
                (house_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_chore(
        self,
        house_id: int,
        title: str,
        description: str,
        assignee_user_id: int,
        scheduled_date: str,
        created_by: int,
    ) -> dict[str, Any] | None:
        with self.session() as connection:
            assignee = connection.execute(
                """
                SELECT user.display_name
                FROM house_members member
                JOIN users user ON user.id = member.user_id
                WHERE member.house_id = ? AND member.user_id = ?
                """,
                (house_id, assignee_user_id),
            ).fetchone()
            if assignee is None:
                return None
            cursor = connection.execute(
                """
                INSERT INTO chores (
                    house_id, title, assigned_to, due_date, status,
                    description, assignee_user_id, scheduled_date,
                    completed_at, created_by, created_at
                ) VALUES (?, ?, ?, ?, '진행 전', ?, ?, ?, NULL, ?, ?)
                """,
                (
                    house_id,
                    title,
                    str(assignee["display_name"]),
                    scheduled_date,
                    description,
                    assignee_user_id,
                    scheduled_date,
                    created_by,
                    self._now(),
                ),
            )
            row = self._get_chore_row(connection, house_id, int(cursor.lastrowid))
        return dict(row) if row else None

    def update_chore(
        self,
        house_id: int,
        chore_id: int,
        changes: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self.session() as connection:
            if self._get_chore_row(connection, house_id, chore_id) is None:
                return None
            assignments: list[str] = []
            values: list[Any] = []
            if "title" in changes:
                assignments.append("title = ?")
                values.append(changes["title"])
            if "description" in changes:
                assignments.append("description = ?")
                values.append(changes["description"])
            if "scheduled_date" in changes:
                assignments.extend(["scheduled_date = ?", "due_date = ?"])
                values.extend([changes["scheduled_date"], changes["scheduled_date"]])
            if "assignee_user_id" in changes:
                assignee = connection.execute(
                    """
                    SELECT user.display_name
                    FROM house_members member
                    JOIN users user ON user.id = member.user_id
                    WHERE member.house_id = ? AND member.user_id = ?
                    """,
                    (house_id, changes["assignee_user_id"]),
                ).fetchone()
                if assignee is None:
                    return None
                assignments.extend(["assignee_user_id = ?", "assigned_to = ?"])
                values.extend(
                    [changes["assignee_user_id"], str(assignee["display_name"])]
                )
            if assignments:
                values.extend([chore_id, house_id])
                connection.execute(
                    f"UPDATE chores SET {', '.join(assignments)} WHERE id = ? AND house_id = ?",
                    values,
                )
            row = self._get_chore_row(connection, house_id, chore_id)
        return dict(row) if row else None

    def set_chore_completion(
        self, house_id: int, chore_id: int, is_completed: bool
    ) -> dict[str, Any] | None:
        with self.session() as connection:
            cursor = connection.execute(
                """
                UPDATE chores
                SET status = ?, completed_at = ?
                WHERE id = ? AND house_id = ?
                """,
                (
                    "완료" if is_completed else "진행 전",
                    self._now() if is_completed else None,
                    chore_id,
                    house_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = self._get_chore_row(connection, house_id, chore_id)
        return dict(row) if row else None

    def delete_chore(self, house_id: int, chore_id: int) -> bool:
        with self.session() as connection:
            cursor = connection.execute(
                "DELETE FROM chores WHERE id = ? AND house_id = ?",
                (chore_id, house_id),
            )
        return cursor.rowcount > 0

    def list_shopping_items(self, house_id: int) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT id, house_id, item_name, added_by, status
                FROM shopping_items
                WHERE house_id = ?
                ORDER BY CASE status WHEN '구매 완료' THEN 1 ELSE 0 END, id DESC
                """,
                (house_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_shopping_item(
        self, house_id: int, item_name: str, added_by: str
    ) -> dict[str, Any]:
        with self.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO shopping_items (house_id, item_name, added_by, status)
                VALUES (?, ?, ?, '구매 필요')
                """,
                (house_id, item_name, added_by),
            )
            row = connection.execute(
                """
                SELECT id, house_id, item_name, added_by, status
                FROM shopping_items WHERE id = ? AND house_id = ?
                """,
                (int(cursor.lastrowid), house_id),
            ).fetchone()
        return dict(row)

    def set_shopping_item_completion(
        self, house_id: int, item_id: int, is_completed: bool
    ) -> dict[str, Any] | None:
        with self.session() as connection:
            cursor = connection.execute(
                """
                UPDATE shopping_items SET status = ?
                WHERE id = ? AND house_id = ?
                """,
                ("구매 완료" if is_completed else "구매 필요", item_id, house_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                """
                SELECT id, house_id, item_name, added_by, status
                FROM shopping_items WHERE id = ? AND house_id = ?
                """,
                (item_id, house_id),
            ).fetchone()
        return dict(row)

    def delete_completed_shopping_item(self, house_id: int, item_id: int) -> int:
        with self.session() as connection:
            item = connection.execute(
                "SELECT id, status FROM shopping_items WHERE id = ? AND house_id = ?",
                (item_id, house_id),
            ).fetchone()
            if item is None:
                raise ShoppingItemNotFoundError()
            if str(item["status"]) not in {"구매 완료", "완료", "completed"}:
                raise ShoppingItemNotCompletedError()
            connection.execute(
                "DELETE FROM shopping_items WHERE id = ? AND house_id = ?",
                (item_id, house_id),
            )
        return item_id

    def delete_completed_shopping_items(self, house_id: int) -> int:
        with self.session() as connection:
            cursor = connection.execute(
                """
                DELETE FROM shopping_items
                WHERE house_id = ? AND status IN ('구매 완료', '완료', 'completed')
                """,
                (house_id,),
            )
        return cursor.rowcount

    def list_settlements(self, house_id: int) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT settlement.id, settlement.house_id,
                       COALESCE(settlement.title, settlement.content) AS title,
                       COALESCE(settlement.total_amount, settlement.amount) AS total_amount,
                       settlement.created_by, settlement.created_at, settlement.status,
                       creator.display_name AS creator_name
                FROM settlements settlement
                LEFT JOIN users creator ON creator.id = settlement.created_by
                WHERE settlement.house_id = ? AND COALESCE(settlement.is_deleted, 0) = 0
                ORDER BY settlement.created_at DESC, settlement.id DESC
                """,
                (house_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                settlement = dict(row)
                participants = connection.execute(
                    """
                    SELECT participant.user_id, user.display_name AS name,
                           participant.amount, participant.payment_status
                    FROM settlement_participants participant
                    JOIN settlements settlement ON settlement.id = participant.settlement_id
                    JOIN house_members member
                      ON member.house_id = settlement.house_id
                     AND member.user_id = participant.user_id
                    JOIN users user ON user.id = participant.user_id
                    WHERE participant.settlement_id = ?
                    ORDER BY participant.id
                    """,
                    (int(row["id"]),),
                ).fetchall()
                settlement["participants"] = [dict(participant) for participant in participants]
                result.append(settlement)
        return result

    def create_settlement(
        self,
        house_id: int,
        title: str,
        total_amount: float,
        participant_user_ids: list[int],
        created_by: int,
        creator_name: str,
    ) -> dict[str, Any]:
        unique_participants = list(dict.fromkeys(participant_user_ids))
        with self.session() as connection:
            placeholders = ",".join("?" for _ in unique_participants)
            members = connection.execute(
                f"""
                SELECT member.user_id, user.display_name
                FROM house_members member
                JOIN users user ON user.id = member.user_id
                WHERE member.house_id = ? AND member.user_id IN ({placeholders})
                  AND user.is_deleted = 0
                """,
                (house_id, *unique_participants),
            ).fetchall()
            if len(members) != len(unique_participants):
                raise InvalidSettlementParticipantsError()
            names = {int(member["user_id"]): str(member["display_name"]) for member in members}
            now = self._now()
            first_participant = unique_participants[0]
            cursor = connection.execute(
                """
                INSERT INTO settlements (
                    house_id, content, debtor, creditor, amount, status,
                    title, total_amount, created_by, created_at, is_deleted
                ) VALUES (?, ?, ?, ?, ?, '진행 중', ?, ?, ?, ?, 0)
                """,
                (
                    house_id,
                    title,
                    names[first_participant],
                    creator_name,
                    total_amount,
                    title,
                    total_amount,
                    created_by,
                    now,
                ),
            )
            settlement_id = int(cursor.lastrowid)
            base_amount = round(total_amount / len(unique_participants), 2)
            allocated = 0.0
            for index, user_id in enumerate(unique_participants):
                amount = (
                    round(total_amount - allocated, 2)
                    if index == len(unique_participants) - 1
                    else base_amount
                )
                allocated = round(allocated + amount, 2)
                connection.execute(
                    """
                    INSERT INTO settlement_participants
                        (settlement_id, user_id, amount, payment_status)
                    VALUES (?, ?, ?, '미납')
                    """,
                    (settlement_id, user_id, amount),
                )
        return next(
            settlement
            for settlement in self.list_settlements(house_id)
            if int(settlement["id"]) == settlement_id
        )

    def soft_delete_settlement(
        self, house_id: int, settlement_id: int, user_id: int
    ) -> dict[str, Any]:
        with self.session() as connection:
            settlement = connection.execute(
                """
                SELECT settlement.id, settlement.created_by, settlement.is_deleted,
                       house.owner_id
                FROM settlements settlement
                JOIN houses house ON house.id = settlement.house_id
                WHERE settlement.id = ? AND settlement.house_id = ?
                """,
                (settlement_id, house_id),
            ).fetchone()
            if settlement is None:
                raise SettlementNotFoundError()
            if int(settlement["is_deleted"] or 0) == 1:
                raise SettlementAlreadyDeletedError()
            if settlement["created_by"] != user_id and settlement["owner_id"] != user_id:
                raise SettlementDeleteForbiddenError()
            deleted_at = self._now()
            connection.execute(
                """
                UPDATE settlements
                SET is_deleted = 1, deleted_at = ?, deleted_by = ?
                WHERE id = ? AND house_id = ? AND is_deleted = 0
                """,
                (deleted_at, user_id, settlement_id, house_id),
            )
        return {
            "deleted_settlement_id": settlement_id,
            "deleted_at": deleted_at,
            "deleted_by": user_id,
        }

    @staticmethod
    def _chore_select_sql() -> str:
        return """
            SELECT chore.id, chore.house_id, chore.title,
                   COALESCE(chore.description, '') AS description,
                   chore.assignee_user_id, chore.scheduled_date,
                   chore.status, chore.completed_at,
                   chore.created_by, chore.created_at,
                   user.display_name AS assignee_display_name
            FROM chores chore
            JOIN house_members member
              ON member.house_id = chore.house_id
             AND member.user_id = chore.assignee_user_id
            JOIN users user ON user.id = member.user_id
        """

    def _get_chore_row(
        self, connection: sqlite3.Connection, house_id: int, chore_id: int
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"""
            {self._chore_select_sql()}
            WHERE chore.house_id = ? AND chore.id = ?
            """,
            (house_id, chore_id),
        ).fetchone()

    def _backup_before_house_invite_migration(self) -> Path | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as connection:
            has_houses = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'houses'"
            ).fetchone()
            has_migrations = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            already_migrated = (
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 3"
                ).fetchone()
                if has_migrations
                else None
            )
        if not has_houses or already_migrated:
            return None

        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory / f"{self.path.stem}-before-house-invites-{timestamp}.db"
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)
        return backup_path

    def _backup_before_chore_migration(self) -> Path | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as connection:
            has_chores = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'chores'"
            ).fetchone()
            has_migrations = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            already_migrated = (
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 4"
                ).fetchone()
                if has_migrations
                else None
            )
        if not has_chores or already_migrated:
            return None

        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory / f"{self.path.stem}-before-chores-{timestamp}.db"
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)
        return backup_path

    def _backup_before_account_deletion_migration(self) -> Path | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as connection:
            has_users = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            has_migrations = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            already_migrated = (
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 5"
                ).fetchone()
                if has_migrations
                else None
            )
        if not has_users or already_migrated:
            return None

        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory / f"{self.path.stem}-before-account-deletion-{timestamp}.db"
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)
        return backup_path

    def _backup_before_settlement_migration(self) -> Path | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as connection:
            has_settlements = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settlements'"
            ).fetchone()
            has_migrations = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            already_migrated = (
                connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 6"
                ).fetchone()
                if has_migrations
                else None
            )
        if not has_settlements or already_migrated:
            return None

        backup_directory = self.path.parent / "backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory / f"{self.path.stem}-before-settlements-{timestamp}.db"
        with closing(sqlite3.connect(self.path)) as source:
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)
        return backup_path

    def _new_invite_code(self, existing: set[str] | None = None) -> str:
        occupied = existing or set()
        for _ in range(100):
            code = "".join(secrets.choice(self.INVITE_CODE_ALPHABET) for _ in range(8))
            if code not in occupied:
                return code
        raise RuntimeError("고유한 초대코드를 생성하지 못했습니다.")

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
