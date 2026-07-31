import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.app.database import Database
from backend.app.security import create_access_token, hash_access_token, hash_password


class DatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test.db"
        self.database = Database(self.database_path)
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_fresh_database_has_all_tables_and_migrations(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]

        self.assertEqual(
            tables,
            {
                "auth_sessions",
                "chores",
                "house_members",
                "houses",
                "schema_migrations",
                "settlement_participants",
                "settlements",
                "shopping_items",
                "users",
            },
        )
        self.assertEqual(versions, [1, 2, 3, 4, 5, 6])

    def test_session_stores_token_hash_instead_of_bearer_token(self) -> None:
        user = self.database.create_user(
            "member@example.com", hash_password("correct-password"), "Member User"
        )
        token = create_access_token()
        self.database.create_session(int(user["id"]), token)

        with closing(sqlite3.connect(self.database_path)) as connection:
            stored_hash = connection.execute(
                "SELECT token_hash FROM auth_sessions"
            ).fetchone()[0]

        self.assertNotEqual(stored_hash, token)
        self.assertEqual(stored_hash, hash_access_token(token))
        self.assertEqual(self.database.get_user_by_token(token)["email"], "member@example.com")


if __name__ == "__main__":
    unittest.main()
