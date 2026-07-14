import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app


class DeletionApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test.db"
        self.app = create_app(self.database_path)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp_directory.cleanup()

    async def signup(self, email: str, name: str) -> dict[str, object]:
        response = await self.client.post(
            "/auth/signup",
            json={"email": email, "password": "correct-password", "display_name": name},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    @staticmethod
    def headers(auth: dict[str, object]) -> dict[str, str]:
        return {"Authorization": f"Bearer {auth['access_token']}"}

    async def create_shared_house(self):
        owner = await self.signup("owner@example.com", "Owner User")
        member = await self.signup("member@example.com", "Member User")
        house_response = await self.client.post(
            "/houses",
            headers=self.headers(owner),
            json={"name": "House A", "location": "Sydney"},
        )
        self.assertEqual(house_response.status_code, 201)
        house = house_response.json()
        join = await self.client.post(
            "/houses/join",
            headers=self.headers(member),
            json={"invite_code": house["invite_code"]},
        )
        self.assertEqual(join.status_code, 200)
        return owner, member, house

    async def test_member_leave_and_lost_access(self) -> None:
        owner, member, house = await self.create_shared_house()
        forbidden_delete = await self.client.request(
            "DELETE",
            f"/houses/{house['id']}",
            headers=self.headers(member),
            json={"confirmation": "House A"},
        )
        self.assertEqual(forbidden_delete.status_code, 403)

        leave = await self.client.post(
            f"/houses/{house['id']}/leave", headers=self.headers(member)
        )
        self.assertEqual(leave.status_code, 204)
        self.assertEqual(
            (await self.client.get(f"/houses/{house['id']}", headers=self.headers(member))).status_code,
            403,
        )
        self.assertEqual(
            (await self.client.get(f"/houses/{house['id']}/chores", headers=self.headers(member))).status_code,
            403,
        )
        self.assertEqual((await self.client.get("/houses", headers=self.headers(member))).json(), [])
        owner_leave = await self.client.post(
            f"/houses/{house['id']}/leave", headers=self.headers(owner)
        )
        self.assertEqual(owner_leave.status_code, 409)

    async def test_transfer_confirmation_and_house_cascade_policy(self) -> None:
        owner, member, house = await self.create_shared_house()
        owner_id = owner["user"]["id"]
        member_id = member["user"]["id"]

        self_transfer = await self.client.patch(
            f"/houses/{house['id']}/owner",
            headers=self.headers(owner),
            json={"new_owner_user_id": owner_id},
        )
        self.assertEqual(self_transfer.status_code, 400)
        transfer = await self.client.patch(
            f"/houses/{house['id']}/owner",
            headers=self.headers(owner),
            json={"new_owner_user_id": member_id},
        )
        self.assertEqual(transfer.status_code, 200)
        self.assertEqual(transfer.json()["owner_id"], member_id)
        members = (await self.client.get(
            f"/houses/{house['id']}/members", headers=self.headers(member)
        )).json()
        roles = {item["user_id"]: item["role"] for item in members}
        self.assertEqual(roles, {owner_id: "member", member_id: "owner"})

        old_owner_delete = await self.client.request(
            "DELETE",
            f"/houses/{house['id']}",
            headers=self.headers(owner),
            json={"confirmation": "House A"},
        )
        self.assertEqual(old_owner_delete.status_code, 403)
        wrong_name = await self.client.request(
            "DELETE",
            f"/houses/{house['id']}",
            headers=self.headers(member),
            json={"confirmation": "Wrong"},
        )
        self.assertEqual(wrong_name.status_code, 400)

        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO shopping_items (house_id, item_name, added_by, status) VALUES (?, 'Soap', 'Member User', 'pending')",
                (house["id"],),
            )
            connection.execute(
                "INSERT INTO settlements (house_id, content, debtor, creditor, amount, status) VALUES (?, 'Shared bill', 'Owner User', 'Member User', 20, 'pending')",
                (house["id"],),
            )
            connection.commit()
        chore = await self.client.post(
            f"/houses/{house['id']}/chores",
            headers=self.headers(member),
            json={
                "title": "Bathroom",
                "description": "",
                "assignee_user_id": member_id,
                "scheduled_date": "2026-07-15",
            },
        )
        self.assertEqual(chore.status_code, 201)
        impact = await self.client.get(
            f"/houses/{house['id']}/deletion-impact", headers=self.headers(member)
        )
        self.assertEqual(impact.status_code, 200)
        self.assertEqual(impact.json()["deleted_counts"]["house_members"], 2)
        self.assertEqual(impact.json()["deleted_counts"]["chores"], 1)
        self.assertEqual(impact.json()["deleted_counts"]["shopping_items"], 1)
        self.assertEqual(impact.json()["deleted_counts"]["settlements"], 1)

        deleted = await self.client.request(
            "DELETE",
            f"/houses/{house['id']}",
            headers=self.headers(member),
            json={"confirmation": "House A"},
        )
        self.assertEqual(deleted.status_code, 200)
        with closing(sqlite3.connect(self.database_path)) as connection:
            for table in ("houses", "house_members", "chores", "shopping_items", "settlements"):
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE "
                    + ("id = ?" if table == "houses" else "house_id = ?"),
                    (house["id"],),
                ).fetchone()[0]
                self.assertEqual(count, 0, table)

        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.app = create_app(self.database_path)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        for email in ("owner@example.com", "member@example.com"):
            login = await self.client.post(
                "/auth/login",
                json={"email": email, "password": "correct-password"},
            )
            self.assertEqual(login.status_code, 200)
            houses = await self.client.get("/houses", headers=self.headers(login.json()))
            self.assertEqual(houses.json(), [])

    async def test_account_block_transfer_soft_delete_and_restart(self) -> None:
        owner, member, house = await self.create_shared_house()
        owner_id = owner["user"]["id"]
        member_id = member["user"]["id"]
        blocked = await self.client.request(
            "DELETE",
            "/auth/me",
            headers=self.headers(owner),
            json={"password": "correct-password", "confirmation": "DELETE"},
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["detail"]["blocking_houses"][0]["id"], house["id"])

        await self.client.patch(
            f"/houses/{house['id']}/owner",
            headers=self.headers(owner),
            json={"new_owner_user_id": member_id},
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO settlements (house_id, content, debtor, creditor, amount, status) VALUES (?, 'History', 'Owner User', 'Member User', 10, 'completed')",
                (house["id"],),
            )
            connection.commit()

        wrong_password = await self.client.request(
            "DELETE",
            "/auth/me",
            headers=self.headers(owner),
            json={"password": "wrong", "confirmation": "DELETE"},
        )
        self.assertEqual(wrong_password.status_code, 401)
        deleted = await self.client.request(
            "DELETE",
            "/auth/me",
            headers=self.headers(owner),
            json={"password": "correct-password", "confirmation": "DELETE"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            (await self.client.get("/auth/me", headers=self.headers(owner))).status_code, 401
        )
        self.assertEqual(
            (await self.client.post(
                "/auth/login",
                json={"email": "owner@example.com", "password": "correct-password"},
            )).status_code,
            401,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            user_row = connection.execute(
                "SELECT email, display_name, password_hash, is_deleted, deleted_at FROM users WHERE id = ?",
                (owner_id,),
            ).fetchone()
            self.assertEqual(user_row["is_deleted"], 1)
            self.assertTrue(user_row["email"].endswith("@roomsync.invalid"))
            self.assertTrue(user_row["display_name"].startswith("탈퇴한 사용자"))
            self.assertTrue(user_row["deleted_at"])
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM house_members WHERE user_id = ?", (owner_id,)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (owner_id,)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM settlements WHERE house_id = ?", (house["id"],)
            ).fetchone()[0], 1)

        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.app = create_app(self.database_path)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        login_after_restart = await self.client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "correct-password"},
        )
        self.assertEqual(login_after_restart.status_code, 401)
        member_houses = await self.client.get("/houses", headers=self.headers(member))
        self.assertEqual([item["id"] for item in member_houses.json()], [house["id"]])

    async def test_solo_owner_account_deletion_removes_solo_house(self) -> None:
        owner = await self.signup("solo@example.com", "Solo Owner")
        house = (await self.client.post(
            "/houses",
            headers=self.headers(owner),
            json={"name": "Solo House", "location": "Sydney"},
        )).json()
        deleted = await self.client.request(
            "DELETE",
            "/auth/me",
            headers=self.headers(owner),
            json={"password": "correct-password", "confirmation": "DELETE"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_solo_houses"], 1)
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM houses WHERE id = ?", (house["id"],)
            ).fetchone()[0], 0)

    async def test_management_endpoints_require_authentication(self) -> None:
        self.assertEqual((await self.client.post("/houses/1/leave")).status_code, 401)
        self.assertEqual((await self.client.patch(
            "/houses/1/owner", json={"new_owner_user_id": 2}
        )).status_code, 401)
        self.assertEqual((await self.client.request(
            "DELETE", "/houses/1", json={"confirmation": "House"}
        )).status_code, 401)
        self.assertEqual((await self.client.request(
            "DELETE", "/auth/me", json={"password": "password", "confirmation": "DELETE"}
        )).status_code, 401)


if __name__ == "__main__":
    unittest.main()
