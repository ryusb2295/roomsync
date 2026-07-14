import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app


class SettlementApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test.db"
        await self.start_app()
        fixtures = [
            ("owner@example.com", "류승범"),
            ("member1@example.com", "김규성"),
            ("member2@example.com", "장수경"),
            ("member3@example.com", "권정균"),
            ("member4@example.com", "장서현"),
        ]
        self.users = [await self.signup(email, name) for email, name in fixtures]
        self.owner = self.users[0]
        self.creator = self.users[1]
        self.house = (await self.client.post(
            "/houses",
            headers=self.headers(self.owner),
            json={"name": "RSA", "location": "Sydney"},
        )).json()
        for member in self.users[1:]:
            join = await self.client.post(
                "/houses/join",
                headers=self.headers(member),
                json={"invite_code": self.house["invite_code"]},
            )
            self.assertEqual(join.status_code, 200)

    async def start_app(self) -> None:
        self.app = create_app(self.database_path)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def stop_app(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def asyncTearDown(self) -> None:
        await self.stop_app()
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

    async def create_settlement(
        self,
        auth: dict[str, object],
        title: str = "Receipt settlement",
        participant_ids: list[int] | None = None,
    ) -> dict[str, object]:
        ids = participant_ids or [int(user["user"]["id"]) for user in self.users]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(auth),
            json={"title": title, "total_amount": 100, "participant_user_ids": ids},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def test_rsa_five_members_create_list_and_restart_persistence(self) -> None:
        members = await self.client.get(
            f"/houses/{self.house['id']}/members", headers=self.headers(self.creator)
        )
        self.assertEqual(members.status_code, 200)
        self.assertEqual(
            [(item["display_name"], item["role"]) for item in members.json()],
            [("류승범", "owner"), ("김규성", "member"), ("장수경", "member"), ("권정균", "member"), ("장서현", "member")],
        )
        empty = await self.client.get(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.creator)
        )
        self.assertEqual(empty.json(), [])

        created = await self.create_settlement(self.creator)
        self.assertEqual(created["created_by"]["name"], "김규성")
        self.assertEqual(created["total_amount"], 100)
        self.assertEqual(
            [item["name"] for item in created["participants"]],
            ["류승범", "김규성", "장수경", "권정균", "장서현"],
        )
        self.assertEqual([item["amount"] for item in created["participants"]], [20, 20, 20, 20, 20])

        await self.stop_app()
        await self.start_app()
        login = await self.client.post(
            "/auth/login",
            json={"email": "member1@example.com", "password": "correct-password"},
        )
        persisted = await self.client.get(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(login.json())
        )
        self.assertEqual(len(persisted.json()), 1)
        self.assertEqual(persisted.json()[0]["settlement_id"], created["settlement_id"])

    async def test_creator_soft_delete_and_repeat_is_rejected(self) -> None:
        created = await self.create_settlement(self.creator)
        deleted = await self.client.delete(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}",
            headers=self.headers(self.creator),
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_by"], self.creator["user"]["id"])
        repeated = await self.client.delete(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}",
            headers=self.headers(self.creator),
        )
        self.assertEqual(repeated.status_code, 409)
        listed = await self.client.get(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.owner)
        )
        self.assertEqual(listed.json(), [])
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT is_deleted, deleted_at, deleted_by FROM settlements WHERE id = ?",
                (created["settlement_id"],),
            ).fetchone()
        self.assertEqual(row[0], 1)
        self.assertTrue(row[1])
        self.assertEqual(row[2], self.creator["user"]["id"])

    async def test_owner_can_delete_but_other_member_cannot(self) -> None:
        created = await self.create_settlement(self.creator)
        forbidden = await self.client.delete(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}",
            headers=self.headers(self.users[2]),
        )
        self.assertEqual(forbidden.status_code, 403)
        still_listed = await self.client.get(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.users[2])
        )
        self.assertEqual(len(still_listed.json()), 1)
        owner_delete = await self.client.delete(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}",
            headers=self.headers(self.owner),
        )
        self.assertEqual(owner_delete.status_code, 200)

    async def test_other_house_is_isolated_and_participants_are_validated(self) -> None:
        invalid = await self.client.post(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(self.creator),
            json={"title": "Invalid", "total_amount": 20, "participant_user_ids": [99999]},
        )
        self.assertEqual(invalid.status_code, 400)

        outsider = await self.signup("outside@example.com", "Outside User")
        other_house = (await self.client.post(
            "/houses",
            headers=self.headers(outsider),
            json={"name": "Other House", "location": "Perth"},
        )).json()
        other_settlement = (await self.client.post(
            f"/houses/{other_house['id']}/settlements",
            headers=self.headers(outsider),
            json={
                "title": "Other bill",
                "total_amount": 30,
                "participant_user_ids": [outsider["user"]["id"]],
            },
        )).json()
        self.assertEqual((await self.client.get(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.owner)
        )).json(), [])
        hidden_id = await self.client.delete(
            f"/houses/{self.house['id']}/settlements/{other_settlement['settlement_id']}",
            headers=self.headers(self.owner),
        )
        forbidden_house = await self.client.delete(
            f"/houses/{other_house['id']}/settlements/{other_settlement['settlement_id']}",
            headers=self.headers(self.owner),
        )
        self.assertEqual(hidden_id.status_code, 404)
        self.assertEqual(forbidden_house.status_code, 403)

    async def test_authentication_is_required(self) -> None:
        self.assertEqual((await self.client.get(
            f"/houses/{self.house['id']}/settlements"
        )).status_code, 401)
        self.assertEqual((await self.client.delete(
            f"/houses/{self.house['id']}/settlements/1"
        )).status_code, 401)


if __name__ == "__main__":
    unittest.main()
