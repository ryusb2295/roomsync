import tempfile
import unittest
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from backend.app.main import create_app
from backend.app.receipt_analyzer import (
    MISSING_TAX_WARNING,
    ReceiptAnalysisError,
    ReceiptAnalyzer,
    extract_json_from_text,
    normalize_gemini_receipt,
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
            files={"file": ("receipt.jpg", b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")},
        )
        self.assertEqual(oversized_response.status_code, 400)

        forbidden_response = await self.client.post(
            "/houses/99999/receipts/analyze",
            headers=self.headers,
            files={"file": ("receipt.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        self.assertEqual(forbidden_response.status_code, 403)

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
        self.assertIn("품목 합계와 영수증 총액이 일치하지 않습니다.", result["warnings"])

    def test_gemini_aliases_are_normalized(self) -> None:
        result = normalize_gemini_receipt({
            "merchant": "Coles", "purchase_date": "20/07/2026",
            "items": [{"name": "Milk", "quantity": 2, "unit_price": 3.5}],
            "total": 7, "tax": 0,
        })
        self.assertEqual(result["store_name"], "Coles")
        self.assertEqual(result["merchant_name"], "Coles")
        self.assertEqual(result["receipt_date"], "2026-07-20")
        self.assertEqual(result["items"][0]["price"], 3.5)
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

    def test_missing_tax_and_confidence_use_conservative_defaults(self) -> None:
        result = normalize_gemini_receipt({
            "merchant_name": "Coles",
            "items": [{"name": "Bread", "amount": 4}],
            "total": 4,
        })
        self.assertEqual(result["subtotal"], 4)
        self.assertEqual(result["tax"], 0)
        self.assertEqual(result["confidence"], 0.5)
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

    def test_gemini_quantity_defaults_to_one(self) -> None:
        analyzer = ReceiptAnalyzer(provider="gemini", gemini_generate=lambda *_: {
            "merchant_name": None, "receipt_date": None, "currency": None,
            "items": [{"name": "Milk", "unit_price": None, "amount": 3.4}],
            "subtotal": None, "tax": None, "total": 3.4, "confidence": 0.5, "warnings": [],
        })
        self.assertEqual(analyzer.analyze(b"image", "image/png")["items"][0]["quantity"], 1)

    def test_invalid_or_empty_gemini_output_is_422(self) -> None:
        for output in ({"invalid": True}, {"merchant_name": None, "receipt_date": None, "currency": None, "items": [], "subtotal": None, "tax": None, "total": 0, "confidence": 0.5, "warnings": []}):
            with self.subTest(output=output):
                analyzer = ReceiptAnalyzer(provider="gemini", gemini_generate=lambda *_, value=output: value)
                with self.assertRaises(ReceiptAnalysisError) as context:
                    analyzer.analyze(b"image", "image/webp")
                self.assertEqual(context.exception.status_code, 422)

    def test_gemini_errors_are_mapped_without_mock_fallback(self) -> None:
        class RateLimitError(Exception):
            code = 429

        for error, status_code in ((RateLimitError("secret-key"), 429), (TimeoutError("secret-key"), 504), (RuntimeError("secret-key"), 502)):
            with self.subTest(status_code=status_code):
                def fail(*_: object, failure: Exception = error) -> None:
                    raise failure
                analyzer = ReceiptAnalyzer(provider="gemini", mock_enabled=True, gemini_generate=fail)
                with self.assertRaises(ReceiptAnalysisError) as context:
                    analyzer.analyze(b"image", "image/jpeg")
                self.assertEqual(context.exception.status_code, status_code)
                self.assertNotIn("secret-key", str(context.exception))

    def test_missing_key_and_invalid_provider_are_configuration_errors(self) -> None:
        for analyzer in (ReceiptAnalyzer(provider="gemini", gemini_api_key=""), ReceiptAnalyzer(provider="invalid")):
            with self.assertRaises(ReceiptAnalysisError) as context:
                analyzer.analyze(b"image", "image/jpeg")
            self.assertEqual(context.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
