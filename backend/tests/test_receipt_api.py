import tempfile
import unittest
import io
from pathlib import Path
from PIL import Image

from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app
from backend.app.receipt_analyzer import (
    MISSING_TAX_WARNING,
    ReceiptAnalysisError,
    ReceiptAnalyzer,
    allocate_shared_item_cents,
    extract_json_from_text,
    normalize_gemini_receipt,
    preprocess_receipt_image,
    reconcile_receipt_amounts,
)


class ReceiptApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_directory.name) / "test.db"
        self.app = create_app(
            database_path,
            receipt_analyzer=ReceiptAnalyzer(mode="mock", mock_enabled=True),
        )
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = AsyncClient(
            transport=ASGITransport(app=self.app),
            base_url="http://testserver",
        )

        signup_response = await self.client.post(
            "/auth/signup",
            json={
                "email": "receipt@example.com",
                "password": "correct-password",
                "display_name": "영수증테스터",
            },
        )
        self.token = signup_response.json()["access_token"]
        self.user_id = signup_response.json()["user"]["id"]
        self.headers = {"Authorization": f"Bearer {self.token}"}
        house_response = await self.client.post(
            "/houses",
            headers=self.headers,
            json={"name": "Receipt House", "location": "Sydney"},
        )
        self.house_id = house_response.json()["id"]

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp_directory.cleanup()

    async def test_supported_image_uploads_succeed(self) -> None:
        for filename, mime_type in (("receipt.jpg", "image/jpeg"), ("receipt.png", "image/png"), ("receipt.webp", "image/webp")):
            with self.subTest(mime_type=mime_type):
                response = await self.client.post(f"/houses/{self.house_id}/receipts/analyze", headers=self.headers, files={"file": (filename, b"fake-image-content", mime_type)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["analysis_mode"], "mock")
                self.assertIsInstance(response.json()["receipt_id"], int)
                self.assertEqual(response.json()["uploaded_by"], self.user_id)
                self.assertTrue(all(item["receipt_item_id"] for item in response.json()["items"]))

    async def test_upload_validation_and_house_permission(self) -> None:
        text_response = await self.client.post(
            f"/houses/{self.house_id}/receipts/analyze",
            headers=self.headers,
            files={"file": ("receipt.txt", b"not-an-image", "text/plain")},
        )
        self.assertEqual(text_response.status_code, 400)

        empty_response = await self.client.post(
            f"/houses/{self.house_id}/receipts/analyze",
            headers=self.headers,
            files={"file": ("receipt.jpg", b"", "image/jpeg")},
        )
        self.assertEqual(empty_response.status_code, 400)

        oversized_response = await self.client.post(
            f"/houses/{self.house_id}/receipts/analyze",
            headers=self.headers,
            files={"file": ("receipt.jpg", b"x" * (25 * 1024 * 1024 + 1), "image/jpeg")},
        )
        self.assertEqual(oversized_response.status_code, 413)

        forbidden_response = await self.client.post(
            "/houses/99999/receipts/analyze",
            headers=self.headers,
            files={"file": ("receipt.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        self.assertEqual(forbidden_response.status_code, 403)

    async def test_edited_receipt_can_be_reconciled(self) -> None:
        response = await self.client.post(
            f"/houses/{self.house_id}/receipts/reconcile",
            headers=self.headers,
            json={
                "store_name": "Coles",
                "receipt_date": "19/06/22",
                "items": [{"name": "Item", "quantity": 1, "price": 110, "line_total": 110, "gst_status": "taxable"}],
                "subtotal": 110,
                "gst": 10,
                "total": 110,
                "amount_paid": 110,
                "gst_inclusion_type": "included",
                "gst_displayed": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["verified_total"], 110)
        self.assertEqual(response.json()["reconciliation_status"], "verified_gst_included")
        self.assertEqual(response.json()["receipt_date"], "2022-06-19")

    def test_json_extraction_supports_wrapped_response(self) -> None:
        result = extract_json_from_text(
            'Result: {"store_name":"Test","items":[{"name":"Milk","quantity":1,"price":3.5}],"total":3.5}'
        )
        self.assertEqual(result["store_name"], "Test")

    def test_auto_mode_falls_back_only_when_mock_is_explicitly_enabled(self) -> None:
        auto_result = ReceiptAnalyzer(api_key="", mode="auto", mock_enabled=True).analyze(
            b"fake-image", "image/jpeg"
        )
        self.assertEqual(auto_result["analysis_mode"], "mock")
        self.assertIn("OpenAI API", auto_result["warning"])

        with self.assertRaises(ReceiptAnalysisError):
            ReceiptAnalyzer(api_key="", mode="auto", mock_enabled=False).analyze(
                b"fake-image", "image/jpeg"
            )

        with self.assertRaises(ReceiptAnalysisError):
            ReceiptAnalyzer(api_key="", mode="openai").analyze(
                b"fake-image", "image/jpeg"
            )

    def test_gemini_structured_output_and_total_warning(self) -> None:
        analyzer = ReceiptAnalyzer(provider="gemini", gemini_generate=lambda *_: {
            "merchant_name": "Coles", "receipt_date": "2026-07-19", "currency": "AUD",
            "items": [{"name": "Milk", "quantity": 1, "unit_price": 3.4, "amount": 3.4}],
            "subtotal": 3.4, "tax": 0, "total": 4.0, "confidence": 0.92, "warnings": [],
        })
        result = analyzer.analyze(b"image", "image/jpeg")
        self.assertEqual(result["analysis_mode"], "gemini")
        self.assertEqual(result["items"][0]["price"], 3.4)
        self.assertIn("품목 합계와 최종 총액이 0.60 AUD 차이 납니다.", result["warnings"])

    def test_gemini_aliases_are_normalized(self) -> None:
        result = normalize_gemini_receipt({
            "merchant": "Coles", "purchase_date": "20/07/2026",
            "items": [{"name": "Milk", "quantity": 2, "unit_price": 3.5}],
            "total": 7, "tax": 0,
        })
        self.assertEqual(result["store_name"], "Coles")
        self.assertEqual(result["merchant_name"], "Coles")
        self.assertEqual(result["receipt_date"], "2026-07-20")
        self.assertEqual(result["items"][0]["price"], 7.0)
        self.assertEqual(result["items"][0]["unit_price"], 3.5)
        self.assertEqual(result["items"][0]["amount"], 7.0)

    def test_markdown_json_is_parsed_and_normalized(self) -> None:
        result = normalize_gemini_receipt(
            """```json
            {"store_name":"Coles","date":"2026-07-20","items":[{"name":"Bread","price":4}],"tax":0,"total":4}
            ```"""
        )
        self.assertEqual(result["merchant_name"], "Coles")
        self.assertEqual(result["receipt_date"], "2026-07-20")

    def test_nested_receipt_and_purchased_items_are_normalized(self) -> None:
        result = normalize_gemini_receipt({
            "receipt": {
                "merchant": "Coles",
                "purchase_date": "20/07/2026",
                "purchased_items": [{"name": "Milk", "quantity": 2, "price": 3.5}],
                "tax": 0,
                "total": 7,
            }
        })
        self.assertEqual(result["store_name"], "Coles")
        self.assertEqual(result["receipt_date"], "2026-07-20")
        self.assertEqual(result["items"][0]["amount"], 7)

    def test_missing_tax_and_confidence_remain_explicitly_unknown(self) -> None:
        result = normalize_gemini_receipt({
            "merchant_name": "Coles",
            "items": [{"name": "Bread", "amount": 4}],
            "total": 4,
        })
        self.assertIsNone(result["subtotal"])
        self.assertIsNone(result["tax"])
        self.assertIsNone(result["model_reported_confidence"])
        self.assertIn(MISSING_TAX_WARNING, result["warnings"])
        self.assertEqual(result["warning"], MISSING_TAX_WARNING)

    def test_currency_symbols_and_commas_are_converted(self) -> None:
        result = normalize_gemini_receipt({
            "merchant": "Coles",
            "items": [{"name": "Groceries", "quantity": "2", "price": "AUD $1,234.50"}],
            "subtotal": "$2,469.00", "tax": "$0.00", "total": "AUD 2,469.00",
            "confidence": "0.7",
        })
        self.assertEqual(result["items"][0]["amount"], 2469.0)
        self.assertEqual(result["total"], 2469.0)

    def test_invalid_gemini_json_is_422(self) -> None:
        with self.assertRaises(ReceiptAnalysisError) as context:
            normalize_gemini_receipt("not json")
        self.assertEqual(context.exception.status_code, 422)

    def test_injected_gemini_response_does_not_call_external_api(self) -> None:
        calls = []

        def fake_generate(*args: object) -> dict[str, object]:
            calls.append(args)
            return {
                "merchant": "Coles",
                "items": [{"name": "Milk", "price": 3.4}],
                "tax": 0,
                "total": 3.4,
            }

        analyzer = ReceiptAnalyzer(
            provider="gemini", gemini_api_key="", gemini_generate=fake_generate
        )
        result = analyzer.analyze(b"image", "image/jpeg")
        self.assertEqual(result["analysis_mode"], "gemini")
        self.assertEqual(len(calls), 1)

    def test_gemini_missing_quantity_remains_null(self) -> None:
        analyzer = ReceiptAnalyzer(provider="gemini", gemini_generate=lambda *_: {
            "merchant_name": None, "receipt_date": None, "currency": None,
            "items": [{"name": "Milk", "unit_price": None, "amount": 3.4}],
            "subtotal": None, "tax": None, "total": 3.4, "confidence": 0.5, "warnings": [],
        })
        self.assertIsNone(analyzer.analyze(b"image", "image/png")["items"][0]["quantity"])

    def test_invalid_or_empty_gemini_output_needs_review(self) -> None:
        for output in ({"invalid": True}, {"merchant_name": None, "receipt_date": None, "currency": None, "items": [], "subtotal": None, "tax": None, "total": 0, "confidence": 0.5, "warnings": []}):
            with self.subTest(output=output):
                analyzer = ReceiptAnalyzer(provider="gemini", gemini_generate=lambda *_, value=output: value)
                result = analyzer.analyze(b"image", "image/webp")
                self.assertEqual(result["reconciliation_status"], "needs_review")
                self.assertIsNone(result["verified_total"])

    def test_gst_excluded_then_added_is_verified(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [], "subtotal": 100, "gst": 10, "total": 110,
            "gst_inclusion_type": "excluded_then_added", "gst_displayed": True,
        })
        self.assertEqual(result["gst_inclusion_type"], "excluded_then_added")
        self.assertEqual(result["reconciliation_status"], "verified_gst_added")
        self.assertEqual(result["verified_total"], 110)

    def test_gst_included_is_not_added_again(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [], "subtotal": 110, "gst": 10, "total": 110,
            "gst_inclusion_type": "included", "gst_displayed": True,
        })
        self.assertEqual(result["gst_inclusion_type"], "included")
        self.assertEqual(result["reconciliation_status"], "verified_gst_included")
        self.assertEqual(result["verified_total"], 110)
        self.assertIn("GST가 총액에 포함되어 있으므로 별도로 더하지 않았습니다.", result["warnings"])

    def test_exact_coles_gst_included_values_are_verified(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 30.44}],
            "items_total": 30.44,
            "subtotal": 30.44,
            "gst": 1.68,
            "total": 30.44,
            "amount_paid": 30.44,
            "gst_inclusion_type": "included",
            "gst_displayed": True,
            "confidence": 0.5,
        })
        self.assertEqual(result["verified_total"], 30.44)
        self.assertEqual(result["reconciliation_status"], "verified_gst_included")
        self.assertFalse(result["requires_review"])

    def test_shared_item_cents_are_distributed_exactly(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
        ])
        self.assertEqual(total_cents, 375)
        self.assertEqual(allocations, {1: 188, 2: 187})
        self.assertEqual(sum(allocations.values()), 375)

    def test_identical_participant_items_are_grouped_before_split(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
        ])
        self.assertEqual(total_cents, 750)
        self.assertEqual(allocations, {1: 375, 2: 375})

    def test_common_group_remainder_differs_by_at_most_one_cent(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 22.94, "participant_user_ids": [1, 2, 3, 4, 5]},
        ])
        self.assertEqual(total_cents, 2294)
        self.assertEqual(sorted(allocations.values()), [458, 459, 459, 459, 459])
        self.assertEqual(max(allocations.values()) - min(allocations.values()), 1)

    def test_pizza_and_common_groups_preserve_total_and_fairness(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
            {"line_total": 22.94, "participant_user_ids": [1, 2, 3, 4, 5]},
        ])
        self.assertEqual(total_cents, 3044)
        self.assertEqual(sum(allocations.values()), 3044)
        self.assertLessEqual(abs(allocations[1] - allocations[2]), 1)

    def test_repeated_odd_cent_items_do_not_repeat_remainder_per_item(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 0.01, "participant_user_ids": [1, 2]},
            {"line_total": 0.01, "participant_user_ids": [1, 2]},
            {"line_total": 0.01, "participant_user_ids": [1, 2]},
        ])
        self.assertEqual(total_cents, 3)
        self.assertEqual(sorted(allocations.values()), [1, 2])

    def test_multiple_shared_items_use_their_own_participants(self) -> None:
        allocations, total_cents = allocate_shared_item_cents([
            {"line_total": 3.75, "participant_user_ids": [1, 2]},
            {"line_total": 2.00, "participant_user_ids": [2, 3]},
        ])
        self.assertEqual(total_cents, 575)
        self.assertEqual(allocations, {1: 188, 2: 287, 3: 100})

    def test_shared_item_without_participants_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "참여자를 선택"):
            allocate_shared_item_cents([
                {"line_total": 3.75, "participant_user_ids": []},
            ])

    def test_gst_free_receipt_keeps_printed_total(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 8, "gst_status": "gst_free"}],
            "gst": None, "total": 8, "gst_inclusion_type": "not_displayed",
            "gst_displayed": False,
        })
        self.assertIsNone(result["gst"])
        self.assertEqual(result["verified_total"], 8)

    def test_mixed_tax_receipt_uses_printed_total(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [
                {"line_total": 5, "gst_status": "taxable"},
                {"line_total": 4, "gst_status": "gst_free"},
            ],
            "gst": 0.45, "total": 9, "gst_inclusion_type": "mixed", "gst_displayed": True,
        })
        self.assertEqual(result["gst_inclusion_type"], "mixed")
        self.assertEqual(result["verified_total"], 9)

    def test_gst_not_displayed_is_not_invented(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 12}], "total": 12,
            "gst_inclusion_type": "not_displayed", "gst_displayed": False,
        })
        self.assertIsNone(result["gst"])
        self.assertEqual(result["gst_inclusion_type"], "not_displayed")
        self.assertEqual(result["verified_total"], 12)

    def test_discount_fees_and_rounding_are_reconciled(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 100}], "discount": 5, "fees": 2,
            "rounding": 0.01, "total": 97.01, "gst_inclusion_type": "not_displayed",
        })
        self.assertEqual(result["reconciliation_status"], "verified")
        self.assertEqual(result["verified_total"], 97.01)

    def test_item_total_mismatch_needs_review(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 10}], "total": 9.5,
            "gst_inclusion_type": "not_displayed", "gst_displayed": False,
        })
        self.assertEqual(result["reconciliation_status"], "needs_review")
        self.assertIsNone(result["verified_total"])
        self.assertIn("품목 합계와 최종 총액이 0.50 AUD 차이 납니다.", result["warnings"])

    def test_total_and_amount_paid_mismatch_is_blocked(self) -> None:
        result = reconcile_receipt_amounts({
            "items": [{"line_total": 100}], "total": 100, "amount_paid": 90,
            "gst_inclusion_type": "not_displayed", "gst_displayed": False,
        })
        self.assertEqual(result["reconciliation_status"], "mismatch")
        self.assertEqual(result["verified_total"], 100)
        self.assertIn("최종 총액과 결제금액이 10.00 AUD 차이 납니다.", result["warnings"])

    def test_null_and_invalid_gemini_values_need_review_without_server_error(self) -> None:
        result = normalize_gemini_receipt({
            "items": [{"name": None, "quantity": "bad", "line_total": "unreadable"}],
            "subtotal": "bad", "gst": None, "total": None, "amount_paid": None,
            "gst_inclusion_type": "unknown", "warnings": ["숫자를 읽을 수 없습니다."],
        })
        self.assertEqual(result["reconciliation_status"], "needs_review")
        self.assertIsNone(result["verified_total"])
        self.assertIn("숫자를 읽을 수 없습니다.", result["warnings"])

    def test_confidence_fraction_and_percent_are_normalized(self) -> None:
        fraction = normalize_gemini_receipt({
            "items": [{"name": "Item", "line_total": 1}], "total": 1,
            "confidence": 0.5,
        })
        percent = normalize_gemini_receipt({
            "items": [{"name": "Item", "line_total": 1}], "total": 1,
            "confidence": 50,
        })
        self.assertEqual(fraction["model_reported_confidence"], 0.5)
        self.assertEqual(percent["model_reported_confidence"], 0.5)

    @staticmethod
    def woolworths_validation_payload(**changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "merchant_name": "Woolworths",
            "receipt_date": "2026-08-07",
            "items": [
                {"name": "Groceries", "quantity": 1, "unit_price": 51, "line_total": 51, "type": "item"},
                {"name": "PROMOTION", "line_total": -3, "type": "discount"},
                {"name": "ANY 2 FOR", "line_total": -6, "type": "discount"},
            ],
            "subtotal": 42,
            "gst": 3.82,
            "total": 42,
            "amount_paid": 42,
            "gst_inclusion_type": "included",
            "gst_displayed": True,
        }
        payload.update(changes)
        return payload

    def test_validation_score_is_verified_when_all_amounts_match(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload())
        self.assertGreaterEqual(result["validation_score"], 90)
        self.assertEqual(result["validation_status"], "verified")
        self.assertEqual(result["verified_total"], 42)

    def test_missing_amount_paid_reduces_score_without_invalidating_total(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload(amount_paid=None))
        self.assertLess(result["validation_score"], 90)
        self.assertEqual(result["validation_status"], "mostly_verified")

    def test_item_total_mismatch_caps_validation_below_75(self) -> None:
        items = [{"name": "Incomplete extraction", "quantity": 1, "unit_price": 40, "line_total": 40, "type": "item"}]
        result = normalize_gemini_receipt(self.woolworths_validation_payload(items=items))
        self.assertLess(result["validation_score"], 75)
        self.assertEqual(result["validation_status"], "needs_review")

    def test_gst_included_but_added_to_payment_fails_validation(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload(amount_paid=45.82))
        self.assertLess(result["validation_score"], 75)
        self.assertEqual(result["validation_status"], "needs_review")

    def test_valid_negative_discounts_do_not_reduce_validation_score(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload())
        self.assertEqual(result["calculated_items_total"], 42)
        self.assertGreaterEqual(result["validation_score"], 90)

    def test_total_savings_duplicate_causes_validation_failure(self) -> None:
        items = list(self.woolworths_validation_payload()["items"])
        items.append({"name": "Total Savings", "line_total": -18.5, "type": "discount"})
        result = normalize_gemini_receipt(self.woolworths_validation_payload(items=items))
        self.assertLess(result["validation_score"], 75)
        self.assertEqual(result["validation_status"], "needs_review")

    def test_missing_model_confidence_is_null_not_half(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload())
        self.assertIsNone(result["model_reported_confidence"])

    def test_low_model_confidence_does_not_override_verified_amounts(self) -> None:
        result = normalize_gemini_receipt(self.woolworths_validation_payload(confidence=0.5))
        self.assertEqual(result["model_reported_confidence"], 0.5)
        self.assertGreaterEqual(result["validation_score"], 90)
        self.assertEqual(result["validation_status"], "verified")

    def test_missing_item_fields_add_warning_and_reduce_validation(self) -> None:
        items = [
            {"name": "Groceries", "quantity": 1, "unit_price": 30, "line_total": 30, "type": "item"},
            {"name": None, "line_total": 12, "type": "item"},
        ]
        result = normalize_gemini_receipt(self.woolworths_validation_payload(items=items))
        self.assertLess(result["validation_score"], 75)
        self.assertEqual(result["validation_status"], "needs_review")
        self.assertTrue(any("이름 또는 금액" in warning for warning in result["warnings"]))

    def test_gemini_errors_are_mapped_without_mock_fallback(self) -> None:
        class RateLimitError(Exception):
            code = 429

        for error, status_code in ((RateLimitError("secret-key"), 429), (TimeoutError("secret-key"), 504), (RuntimeError("secret-key"), 502)):
            with self.subTest(status_code=status_code):
                def fail(*_: object, failure: Exception = error) -> None:
                    raise failure
                analyzer = ReceiptAnalyzer(provider="gemini", mock_enabled=True, gemini_generate=fail, sleep_fn=lambda _: None)
                with self.assertRaises(ReceiptAnalysisError) as context:
                    analyzer.analyze(b"image", "image/jpeg")
                self.assertEqual(context.exception.status_code, status_code)
                self.assertNotIn("secret-key", str(context.exception))

    def test_high_resolution_coles_image_is_resized_to_jpeg(self) -> None:
        source = Image.new("RGB", (4200, 1400), "white")
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        processed, mime_type, metadata = preprocess_receipt_image(buffer.getvalue(), "image/png")
        self.assertEqual(mime_type, "image/jpeg")
        self.assertLessEqual(max(metadata["processed_width"], metadata["processed_height"]), 1800)
        self.assertLess(len(processed), len(buffer.getvalue()))

    def test_image_larger_than_eight_mb_is_automatically_compressed(self) -> None:
        source = Image.effect_noise((3200, 3200), 100).convert("RGB")
        buffer = io.BytesIO()
        source.save(buffer, format="BMP")
        self.assertGreater(len(buffer.getvalue()), 8 * 1024 * 1024)
        processed, mime_type, metadata = preprocess_receipt_image(buffer.getvalue(), "image/png")
        self.assertEqual(mime_type, "image/jpeg")
        self.assertLess(metadata["processed_bytes"], metadata["original_bytes"])

    def test_retryable_gemini_errors_retry_then_succeed(self) -> None:
        class TemporaryError(Exception):
            def __init__(self, code: int):
                self.code = code

        success = {"merchant_name": "Woolworths", "items": [{"name": "Milk", "line_total": 3}], "total": 3, "amount_paid": 3}
        for first_error in (TimeoutError(), TemporaryError(429), TemporaryError(503)):
            calls = []
            def generate(*_: object, failure: Exception = first_error):
                calls.append(1)
                if len(calls) == 1:
                    raise failure
                return success
            result = ReceiptAnalyzer(provider="gemini", gemini_generate=generate, sleep_fn=lambda _: None).analyze(b"image", "image/jpeg")
            self.assertEqual(result["merchant_name"], "Woolworths")
            self.assertEqual(len(calls), 2)

    def test_all_gemini_retries_fail_with_specific_message(self) -> None:
        calls = []
        def fail(*_: object) -> None:
            calls.append(1)
            raise TimeoutError()
        with self.assertRaisesRegex(ReceiptAnalysisError, "이미지를 잘라서 다시 시도"):
            ReceiptAnalyzer(provider="gemini", gemini_generate=fail, sleep_fn=lambda _: None).analyze(b"image", "image/jpeg")
        self.assertEqual(len(calls), 3)

    def test_woolworths_promotional_lines_reconcile_to_42(self) -> None:
        lines = [
            ("Arnotts Biscuits", 6, "item", None), ("Cadbury Flake", 7.5, "item", None),
            ("TNCC Soft Jellies", 10, "item", "jellies"), ("ANY 2 FOR", -3, "discount", "jellies"),
            ("Hazelnut", 8, "item", "cadbury"), ("Roast Almond", 8, "item", "cadbury"),
            ("ANY 2 FOR 10", -6, "discount", "cadbury"), ("Jila Mints", 11.5, "item", None),
        ]
        result = reconcile_receipt_amounts({
            "items": [{"name": name, "line_total": amount, "type": kind, "discount_group_id": group} for name, amount, kind, group in lines],
            "total": 42, "amount_paid": 42, "gst_inclusion_type": "not_displayed", "gst_displayed": False,
        })
        self.assertEqual(result["calculated_items_total"], 42)
        self.assertEqual(result["verified_total"], 42)
        self.assertEqual(result["reconciliation_status"], "verified")

    def test_promotion_groups_keep_negative_values_and_net_totals(self) -> None:
        allocations, total = allocate_shared_item_cents([
            {"line_total": 8, "type": "item", "discount_group_id": "cadbury", "participant_user_ids": [1, 2]},
            {"line_total": 8, "type": "item", "discount_group_id": "cadbury", "participant_user_ids": [1, 2]},
            {"line_total": -6, "type": "discount", "discount_group_id": "cadbury", "participant_user_ids": []},
        ])
        self.assertEqual((total, allocations), (1000, {1: 500, 2: 500}))
        _, second_total = allocate_shared_item_cents([
            {"line_total": 10, "type": "item", "discount_group_id": "jellies", "participant_user_ids": [1]},
            {"line_total": -3, "type": "discount", "discount_group_id": "jellies", "participant_user_ids": []},
        ])
        self.assertEqual(second_total, 700)

    def test_discount_with_different_participants_is_distributed_proportionally(self) -> None:
        allocations, total = allocate_shared_item_cents([
            {"line_total": 8, "type": "item", "discount_group_id": "deal", "participant_user_ids": [1]},
            {"line_total": 8, "type": "item", "discount_group_id": "deal", "participant_user_ids": [2]},
            {"line_total": -6, "type": "discount", "discount_group_id": "deal"},
        ])
        self.assertEqual(total, 1000)
        self.assertEqual(allocations, {1: 500, 2: 500})

    def test_negative_item_requires_review_but_negative_discount_does_not(self) -> None:
        valid = reconcile_receipt_amounts({"items": [{"line_total": 10, "type": "item"}, {"line_total": -3, "type": "discount"}], "total": 7})
        self.assertEqual(valid["verified_total"], 7)
        invalid = reconcile_receipt_amounts({"items": [{"line_total": -3, "type": "item"}], "total": -3})
        self.assertEqual(invalid["reconciliation_status"], "needs_review")
        self.assertTrue(any("일반 상품" in warning for warning in invalid["warnings"]))

    def test_total_savings_summary_is_not_counted_twice(self) -> None:
        result = normalize_gemini_receipt({
            "items": [{"name": "Product", "line_total": 42, "type": "item"}, {"name": "You saved $18.50", "line_total": 18.5, "type": "discount"}],
            "total": 42, "amount_paid": 42,
        })
        self.assertEqual(result["calculated_items_total"], 42)
        self.assertEqual(len(result["items"]), 1)

    def test_missing_key_and_invalid_provider_are_configuration_errors(self) -> None:
        for analyzer in (ReceiptAnalyzer(provider="gemini", gemini_api_key=""), ReceiptAnalyzer(provider="invalid")):
            with self.assertRaises(ReceiptAnalysisError) as context:
                analyzer.analyze(b"image", "image/jpeg")
            self.assertEqual(context.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
