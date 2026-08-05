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

    async def create_payable_settlement(self, participant_ids: list[int] | None = None) -> dict[str, object]:
        ids = participant_ids or [int(user["user"]["id"]) for user in self.users[:3]]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.owner),
            json={"title": "Payable", "total_amount": 30, "payer_id": int(self.owner["user"]["id"]),
                  "participant_user_ids": ids, "receipt_verified_total": 30,
                  "receipt_reconciliation_status": "verified",
                  "receipt_items": [{"name": "Shared", "line_total": 30, "participant_user_ids": ids}]},
        )
        self.assertEqual(response.status_code, 201, response.text)
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

    async def test_unverified_receipt_settlement_is_not_saved(self) -> None:
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(self.creator),
            json={
                "title": "Unverified receipt",
                "total_amount": 100,
                "participant_user_ids": [int(self.creator["user"]["id"])],
                "receipt_verified_total": 100,
                "receipt_reconciliation_status": "needs_review",
            },
        )
        self.assertEqual(response.status_code, 409)
        listed = await self.client.get(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(self.creator),
        )
        self.assertEqual(listed.json(), [])

    async def test_receipt_items_are_distributed_by_item_participants(self) -> None:
        user_ids = [int(user["user"]["id"]) for user in self.users[:3]]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(self.creator),
            json={
                "title": "Item allocation",
                "total_amount": 5.75,
                "participant_user_ids": user_ids,
                "receipt_verified_total": 5.75,
                "receipt_reconciliation_status": "verified_gst_included",
                "payer_id": user_ids[0],
                "receipt_items": [
                    {"name": "Shared A", "line_total": 3.75, "participant_user_ids": user_ids[:2]},
                    {"name": "Shared B", "line_total": 2.00, "participant_user_ids": user_ids[1:]},
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        participants = {
            item["user_id"]: item["amount"] for item in response.json()["participants"]
        }
        statuses = {
            item["user_id"]: item["payment_status"] for item in response.json()["participants"]
        }
        self.assertEqual(participants, {user_ids[0]: 1.88, user_ids[1]: 2.87, user_ids[2]: 1.0})
        self.assertEqual(round(sum(participants.values()), 2), 5.75)
        self.assertEqual(statuses[user_ids[0]], "payer")
        self.assertNotEqual(statuses[user_ids[0]], "미납")
        self.assertEqual((await self.client.delete(
            f"/houses/{self.house['id']}/settlements/1"
        )).status_code, 401)

    async def test_uploader_and_explicit_payer_are_persisted_separately(self) -> None:
        uploader_id = int(self.users[4]["user"]["id"])
        payer_id = int(self.users[0]["user"]["id"])
        participant_ids = [int(user["user"]["id"]) for user in self.users]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements",
            headers=self.headers(self.users[4]),
            json={
                "title": "Woolworths", "total_amount": 34.10,
                "total_amount_cents": 3410, "uploaded_by": uploader_id,
                "payer_id": payer_id, "participant_user_ids": participant_ids,
                "receipt_verified_total": 34.10,
                "receipt_reconciliation_status": "verified_gst_included",
                "receipt_items": [{"name": "Groceries", "line_total": 34.10, "amount_cents": 3410, "participant_user_ids": participant_ids}],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["uploaded_by"], uploader_id)
        self.assertEqual(body["payer_id"], payer_id)

    async def test_uploader_can_be_payer_and_receives_no_unpaid_debt(self) -> None:
        uploader_id = int(self.users[4]["user"]["id"])
        participant_ids = [int(user["user"]["id"]) for user in self.users]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.users[4]),
            json={"title": "Coles", "total_amount": 34.10, "total_amount_cents": 3410,
                  "uploaded_by": uploader_id, "payer_id": uploader_id,
                  "participant_user_ids": participant_ids, "receipt_verified_total": 34.10,
                  "receipt_reconciliation_status": "verified",
                  "receipt_items": [{"name": "All items", "line_total": 34.10, "participant_user_ids": participant_ids}]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        participants = response.json()["participants"]
        self.assertEqual([item["share_amount_cents"] for item in participants], [682] * 5)
        payer = next(item for item in participants if item["user_id"] == uploader_id)
        self.assertEqual((payer["role"], payer["payment_status"]), ("payer", "payer"))
        self.assertEqual(sum(item["share_amount_cents"] for item in participants if item["role"] != "payer"), 2728)

    async def test_payer_outside_participants_has_zero_share_and_no_debt_row(self) -> None:
        payer_id = int(self.users[4]["user"]["id"])
        participant_ids = [int(user["user"]["id"]) for user in self.users[:4]]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.creator),
            json={"title": "Payer excluded", "total_amount": 20, "payer_id": payer_id,
                  "participant_user_ids": participant_ids, "receipt_verified_total": 20,
                  "receipt_reconciliation_status": "verified",
                  "receipt_items": [{"name": "Shared", "line_total": 20, "participant_user_ids": participant_ids}]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        payer = next(item for item in response.json()["participants"] if item["user_id"] == payer_id)
        self.assertEqual((payer["share_amount_cents"], payer["payment_status"]), (0, "payer"))
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM settlement_participants WHERE settlement_id = ? AND user_id = ?", (response.json()["settlement_id"], payer_id)).fetchone()[0]
        self.assertEqual(count, 0)

    async def test_receipt_settlement_requires_payer_and_rejects_non_member(self) -> None:
        participant_id = int(self.creator["user"]["id"])
        payload = {"title": "No payer", "total_amount": 10, "participant_user_ids": [participant_id],
                   "receipt_verified_total": 10, "receipt_reconciliation_status": "verified",
                   "receipt_items": [{"name": "Item", "line_total": 10, "participant_user_ids": [participant_id]}]}
        missing = await self.client.post(f"/houses/{self.house['id']}/settlements", headers=self.headers(self.creator), json=payload)
        self.assertEqual(missing.status_code, 400)
        self.assertIn("결제자를 선택", missing.json()["detail"])
        invalid = await self.client.post(f"/houses/{self.house['id']}/settlements", headers=self.headers(self.creator), json={**payload, "payer_id": 99999})
        self.assertEqual(invalid.status_code, 400)

    async def test_woolworths_discount_lines_can_be_saved_at_net_total(self) -> None:
        ids = [int(user["user"]["id"]) for user in self.users[:2]]
        response = await self.client.post(
            f"/houses/{self.house['id']}/settlements", headers=self.headers(self.creator),
            json={"title": "Woolworths", "total_amount": 42, "total_amount_cents": 4200,
                  "payer_id": ids[0], "participant_user_ids": ids, "receipt_verified_total": 42,
                  "receipt_reconciliation_status": "verified",
                  "receipt_items": [
                      {"name": "Items", "line_total": 51, "type": "item", "participant_user_ids": ids},
                      {"name": "Promotions", "line_total": -9, "type": "discount", "participant_user_ids": []},
                  ]},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["total_amount_cents"], 4200)
        self.assertEqual(sum(item["share_amount_cents"] for item in response.json()["participants"]), 4200)

    async def test_payer_updates_payments_and_settlement_completion(self) -> None:
        created = await self.create_payable_settlement()
        participant_ids = [int(self.users[index]["user"]["id"]) for index in (1, 2)]
        first = await self.client.patch(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}/participants/{participant_ids[0]}/payment-status",
            headers=self.headers(self.owner), json={"payment_status": "paid"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["settlement_status"], "in_progress")
        last = await self.client.patch(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}/participants/{participant_ids[1]}/payment-status",
            headers=self.headers(self.owner), json={"payment_status": "paid"},
        )
        self.assertEqual(last.json()["settlement_status"], "completed")
        self.assertIsNotNone(last.json()["completed_at"])
        reopened = await self.client.patch(
            f"/houses/{self.house['id']}/settlements/{created['settlement_id']}/participants/{participant_ids[0]}/payment-status",
            headers=self.headers(self.owner), json={"payment_status": "unpaid"},
        )
        self.assertEqual(reopened.json()["settlement_status"], "in_progress")
        self.assertIsNone(reopened.json()["completed_at"])
        persisted = await self.client.get(f"/houses/{self.house['id']}/settlements", headers=self.headers(self.owner))
        row = next(item for item in persisted.json() if item["settlement_id"] == created["settlement_id"])
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(next(item for item in row["participants"] if item["user_id"] == participant_ids[0])["payment_status"], "unpaid")

    async def test_payment_status_permissions_and_invalid_targets(self) -> None:
        created = await self.create_payable_settlement()
        payer_id = int(self.owner["user"]["id"])
        participant_id = int(self.users[1]["user"]["id"])
        base = f"/houses/{self.house['id']}/settlements/{created['settlement_id']}/participants"
        payer_attempt = await self.client.patch(f"{base}/{payer_id}/payment-status", headers=self.headers(self.owner), json={"payment_status": "paid"})
        self.assertEqual(payer_attempt.status_code, 409)
        forbidden = await self.client.patch(f"{base}/{participant_id}/payment-status", headers=self.headers(self.creator), json={"payment_status": "paid"})
        self.assertEqual(forbidden.status_code, 403)
        missing_settlement = await self.client.patch(f"/houses/{self.house['id']}/settlements/99999/participants/{participant_id}/payment-status", headers=self.headers(self.owner), json={"payment_status": "paid"})
        self.assertEqual(missing_settlement.status_code, 404)
        missing_participant = await self.client.patch(f"{base}/99999/payment-status", headers=self.headers(self.owner), json={"payment_status": "paid"})
        self.assertEqual(missing_participant.status_code, 404)

    async def test_multiple_settlement_payment_states_are_independent(self) -> None:
        first = await self.create_payable_settlement()
        second = await self.create_payable_settlement()
        participant_id = int(self.users[1]["user"]["id"])
        await self.client.patch(
            f"/houses/{self.house['id']}/settlements/{first['settlement_id']}/participants/{participant_id}/payment-status",
            headers=self.headers(self.owner), json={"payment_status": "paid"},
        )
        rows = (await self.client.get(f"/houses/{self.house['id']}/settlements", headers=self.headers(self.owner))).json()
        first_row = next(item for item in rows if item["settlement_id"] == first["settlement_id"])
        second_row = next(item for item in rows if item["settlement_id"] == second["settlement_id"])
        self.assertEqual(next(item for item in first_row["participants"] if item["user_id"] == participant_id)["payment_status"], "paid")
        self.assertEqual(next(item for item in second_row["participants"] if item["user_id"] == participant_id)["payment_status"], "unpaid")

    async def test_existing_payment_statuses_are_safely_normalized(self) -> None:
        created = await self.create_payable_settlement()
        payer_id = int(self.owner["user"]["id"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("UPDATE settlement_participants SET payment_status = '미납', role = 'participant' WHERE settlement_id = ?", (created["settlement_id"],))
            connection.commit()
        await self.stop_app()
        await self.start_app()
        login = await self.client.post("/auth/login", json={"email": "owner@example.com", "password": "correct-password"})
        rows = (await self.client.get(f"/houses/{self.house['id']}/settlements", headers=self.headers(login.json()))).json()
        row = next(item for item in rows if item["settlement_id"] == created["settlement_id"])
        self.assertEqual(next(item for item in row["participants"] if item["user_id"] == payer_id)["payment_status"], "payer")
        self.assertTrue(all(item["payment_status"] == "unpaid" for item in row["participants"] if item["user_id"] != payer_id))


if __name__ == "__main__":
    unittest.main()
