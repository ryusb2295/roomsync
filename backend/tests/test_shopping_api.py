import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app


class ShoppingApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test.db"
        await self.start_app()
        self.owner = await self.signup("owner@example.com", "Owner User")
        self.house = (await self.client.post(
            "/houses",
            headers=self.headers(self.owner),
            json={"name": "House A", "location": "Sydney"},
        )).json()

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

    async def create_item(self, name: str):
        response = await self.client.post(
            f"/houses/{self.house['id']}/shopping-items",
            headers=self.headers(self.owner),
            json={"item_name": name},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    async def complete_item(self, item_id: int):
        response = await self.client.patch(
            f"/houses/{self.house['id']}/shopping-items/{item_id}/complete",
            headers=self.headers(self.owner),
            json={"is_completed": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_completed"])
        return response.json()

    async def test_add_complete_individual_delete_and_restart(self) -> None:
        item = await self.create_item("Kitchen soap")
        await self.complete_item(item["id"])

        deleted = await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/{item['id']}",
            headers=self.headers(self.owner),
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted_item_id": item["id"]})
        self.assertEqual((await self.client.get(
            f"/houses/{self.house['id']}/shopping-items",
            headers=self.headers(self.owner),
        )).json(), [])
        with closing(sqlite3.connect(self.database_path)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM shopping_items WHERE id = ?", (item["id"],)
            ).fetchone()[0], 0)

        await self.stop_app()
        await self.start_app()
        login = await self.client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": "correct-password"},
        )
        persisted = await self.client.get(
            f"/houses/{self.house['id']}/shopping-items",
            headers=self.headers(login.json()),
        )
        self.assertEqual(persisted.json(), [])

    async def test_bulk_delete_preserves_incomplete_items(self) -> None:
        completed_a = await self.create_item("Completed A")
        completed_b = await self.create_item("Completed B")
        pending = await self.create_item("Still needed")
        await self.complete_item(completed_a["id"])
        await self.complete_item(completed_b["id"])

        deleted = await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/completed",
            headers=self.headers(self.owner),
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted_count": 2})
        items = (await self.client.get(
            f"/houses/{self.house['id']}/shopping-items",
            headers=self.headers(self.owner),
        )).json()
        self.assertEqual([item["id"] for item in items], [pending["id"]])
        self.assertFalse(items[0]["is_completed"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT id, status FROM shopping_items WHERE house_id = ?",
                (self.house["id"],),
            ).fetchall()
        self.assertEqual(rows, [(pending["id"], "구매 필요")])

    async def test_incomplete_and_other_house_items_cannot_be_deleted(self) -> None:
        pending = await self.create_item("Keep me")
        incomplete_delete = await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/{pending['id']}",
            headers=self.headers(self.owner),
        )
        self.assertEqual(incomplete_delete.status_code, 409)

        outsider = await self.signup("outside@example.com", "Outside User")
        other_house = (await self.client.post(
            "/houses",
            headers=self.headers(outsider),
            json={"name": "House B", "location": "Melbourne"},
        )).json()
        forbidden = await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/{pending['id']}",
            headers=self.headers(outsider),
        )
        hidden_other_item = await self.client.delete(
            f"/houses/{other_house['id']}/shopping-items/{pending['id']}",
            headers=self.headers(outsider),
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(hidden_other_item.status_code, 404)
        self.assertEqual((await self.client.get(
            f"/houses/{self.house['id']}/shopping-items",
            headers=self.headers(self.owner),
        )).json()[0]["id"], pending["id"])

    async def test_authentication_is_required(self) -> None:
        self.assertEqual((await self.client.get(
            f"/houses/{self.house['id']}/shopping-items"
        )).status_code, 401)
        self.assertEqual((await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/completed"
        )).status_code, 401)
        self.assertEqual((await self.client.delete(
            f"/houses/{self.house['id']}/shopping-items/1"
        )).status_code, 401)


if __name__ == "__main__":
    unittest.main()
