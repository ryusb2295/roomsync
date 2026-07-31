import base64
import json
import logging
import math
import os
import re
from datetime import date, datetime
from typing import Any, Callable

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.app.config import (
    get_gemini_model,
    get_receipt_debug_enabled,
    get_receipt_mock_enabled,
    get_receipt_mode,
    get_receipt_model,
    get_receipt_provider,
)

logger = logging.getLogger(__name__)

ITEM_TOTAL_WARNING = "품목 합계와 영수증 총액이 일치하지 않습니다."
MISSING_TAX_WARNING = "세금 정보가 없어 0으로 처리했습니다."
DEFAULT_CONFIDENCE = 0.5
RECEIPT_PROMPT = """
This image is a purchase receipt. Extract the merchant, receipt date, currency,
purchased items, quantities, unit prices, line amounts, subtotal, tax, and final total.
Do not invent information that is not visible. Use null and warnings when uncertain.
Do not include subtotal, GST, tax, total, cash, card, payment, or change as purchased items.
Keep item names in the receipt's original language. Return monetary values as numbers
without currency symbols or thousands separators. Use quantity 1 when it is unclear.
Do not force item amounts to equal the receipt total. Follow the supplied response schema.
""".strip()


class ReceiptAnalysisError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class ParsedReceiptItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: str = Field(min_length=1)
    quantity: float = Field(default=1, gt=0)
    unit_price: float | None = None
    amount: float

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("품목명이 비어 있습니다.")
        return normalized


class ParsedReceipt(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    merchant_name: str | None = None
    receipt_date: date | None = None
    currency: str | None = None
    items: list[ParsedReceiptItem] = Field(min_length=1, max_length=100)
    subtotal: float | None = None
    tax: float | None = None
    total: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class GeminiReceiptItem(BaseModel):
    name: str | None = None
    quantity: float | str | None = None
    price: float | str | None = None
    unit_price: float | str | None = None
    amount: float | str | None = None


class GeminiReceiptPayload(BaseModel):
    merchant: str | None = None
    merchant_name: str | None = None
    store_name: str | None = None
    purchase_date: str | None = None
    date: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[GeminiReceiptItem] = Field(default_factory=list)
    subtotal: float | str | None = None
    tax: float | str | None = None
    total: float | str | None = None
    confidence: float | str | None = None
    warnings: list[str] | str | None = None


class GeminiStructuredReceiptItem(BaseModel):
    name: str
    quantity: float = 1
    unit_price: float | None = None
    amount: float | None = None


class GeminiStructuredReceipt(BaseModel):
    merchant_name: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[GeminiStructuredReceiptItem]
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class LegacyReceiptItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    name: str
    quantity: float = Field(default=1, gt=0)
    price: float


class LegacyReceipt(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    store_name: str = "Unknown Store"
    items: list[LegacyReceiptItem] = Field(min_length=1, max_length=100)
    total: float


def get_mock_receipt_data(warning: str | None = None) -> dict[str, Any]:
    return {
        "store_name": "Sample Receipt",
        "merchant_name": "Sample Receipt",
        "receipt_date": None,
        "currency": None,
        "items": [
            {"name": "MATCHA GELATO SINGLE", "quantity": 1, "unit_price": 15.0, "amount": 15.0, "price": 15.0},
            {"name": "Discount", "quantity": 1, "unit_price": -3.0, "amount": -3.0, "price": -3.0},
        ],
        "subtotal": None,
        "tax": None,
        "total": 12.0,
        "confidence": 1.0,
        "warnings": [warning] if warning else [],
        "analysis_mode": "mock",
        "warning": warning,
    }


def extract_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ReceiptAnalysisError("AI 응답에서 JSON 객체를 찾을 수 없습니다.", 422)
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ReceiptAnalysisError("AI 응답이 올바른 JSON 형식이 아닙니다.", 422) from error
    if not isinstance(parsed, dict):
        raise ReceiptAnalysisError("AI 분석 결과가 JSON 객체 형식이 아닙니다.", 422)
    return parsed


def _to_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field_name} 값이 없습니다.")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        normalized = value.strip()
        negative = normalized.startswith("(") and normalized.endswith(")")
        normalized = re.sub(r"(?i)AUD", "", normalized)
        normalized = normalized.replace("$", "").replace(",", "").strip(" ()")
        if not normalized:
            raise ValueError(f"{field_name} 값이 비어 있습니다.")
        number = float(normalized)
        if negative:
            number = -number
    else:
        raise ValueError(f"{field_name} 값의 형식이 올바르지 않습니다.")
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 값이 유한한 숫자가 아닙니다.")
    return number


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            pass
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def _response_shape(payload: dict[str, Any]) -> dict[str, Any]:
    shape: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            shape[key] = sorted(str(item) for item in value.keys())
        elif isinstance(value, list):
            first = value[0] if value else None
            shape[key] = sorted(str(item) for item in first.keys()) if isinstance(first, dict) else "list"
        else:
            shape[key] = type(value).__name__
    return shape


def _unwrap_receipt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("receipt", "receipt_data", "receiptData", "data", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            logger.info("Gemini receipt payload was nested under '%s'", key)
            return nested
    return payload


def normalize_gemini_receipt(raw: str | dict[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(raw, str):
        payload = extract_json_from_text(raw)
    elif isinstance(raw, BaseModel):
        payload = raw.model_dump(exclude_none=True)
    elif isinstance(raw, dict):
        payload = raw
    else:
        raise ReceiptAnalysisError("Gemini 응답이 JSON 객체가 아닙니다.", 422)

    payload = _unwrap_receipt_payload(payload)

    merchant_value = next(
        (payload.get(key) for key in ("merchant", "merchant_name", "store_name") if payload.get(key)),
        None,
    )
    merchant_name = str(merchant_value).strip() if merchant_value else None
    raw_items = next(
        (
            payload.get(key)
            for key in ("items", "purchased_items", "line_items", "products", "entries")
            if payload.get(key) is not None
        ),
        None,
    )
    if not isinstance(raw_items, list) or not raw_items:
        logger.warning("Gemini receipt response has no item list; shape=%s", _response_shape(payload))
        raise ReceiptAnalysisError("Gemini 응답에 영수증 품목이 없습니다.", 422)

    items: list[dict[str, Any]] = []
    try:
        for index, raw_item in enumerate(raw_items, start=1):
            if isinstance(raw_item, BaseModel):
                raw_item = raw_item.model_dump(exclude_none=True)
            if not isinstance(raw_item, dict):
                raise ValueError(f"items[{index}]가 객체가 아닙니다.")
            name = str(raw_item.get("name") or "").strip()
            if not name:
                raise ValueError(f"items[{index}].name이 없습니다.")
            quantity = _to_float(raw_item.get("quantity", 1), f"items[{index}].quantity")
            if quantity <= 0:
                raise ValueError(f"items[{index}].quantity는 0보다 커야 합니다.")
            unit_source = raw_item.get("unit_price")
            if unit_source is None:
                unit_source = raw_item.get("price")
            amount_source = raw_item.get("amount")
            if unit_source is None and amount_source is None:
                raise ValueError(f"items[{index}]에 price, unit_price, amount가 없습니다.")
            amount = _to_float(amount_source, f"items[{index}].amount") if amount_source is not None else None
            unit_price = _to_float(unit_source, f"items[{index}].unit_price") if unit_source is not None else amount / quantity
            if amount is None:
                amount = quantity * unit_price
            items.append({"name": name, "quantity": quantity, "price": unit_price, "unit_price": unit_price, "amount": amount})
    except (TypeError, ValueError) as error:
        logger.warning("Gemini receipt normalization failed: %s", str(error))
        raise ReceiptAnalysisError("Gemini 분석 결과의 품목 구조가 올바르지 않습니다.", 422) from error

    item_total = math.fsum(item["amount"] for item in items)
    try:
        subtotal = _to_float(payload["subtotal"], "subtotal") if payload.get("subtotal") is not None else item_total
        tax_missing = payload.get("tax") is None
        tax = 0.0 if tax_missing else _to_float(payload["tax"], "tax")
        total = _to_float(payload["total"], "total") if payload.get("total") is not None else subtotal + tax
        confidence = _to_float(payload["confidence"], "confidence") if payload.get("confidence") is not None else DEFAULT_CONFIDENCE
    except ValueError as error:
        logger.warning("Gemini receipt totals normalization failed: %s", str(error))
        raise ReceiptAnalysisError("Gemini 분석 결과의 금액 구조가 올바르지 않습니다.", 422) from error

    confidence = min(max(confidence, 0.0), 1.0)
    raw_warnings = payload.get("warnings")
    if isinstance(raw_warnings, str):
        warnings = [raw_warnings.strip()] if raw_warnings.strip() else []
    elif isinstance(raw_warnings, list):
        warnings = [str(item).strip() for item in raw_warnings if str(item).strip()]
    else:
        warnings = []
    if tax_missing and MISSING_TAX_WARNING not in warnings:
        warnings.append(MISSING_TAX_WARNING)
    if abs(item_total - total) > 0.05 and ITEM_TOTAL_WARNING not in warnings:
        warnings.append(ITEM_TOTAL_WARNING)

    receipt_date = next(
        (payload.get(key) for key in ("purchase_date", "date", "receipt_date") if payload.get(key)),
        None,
    )
    currency = str(payload["currency"]).strip().upper() if payload.get("currency") else None
    warnings = list(dict.fromkeys(warnings))
    return {
        "store_name": merchant_name or "Unknown Store",
        "merchant_name": merchant_name,
        "receipt_date": _normalize_date(receipt_date),
        "currency": currency,
        "items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "confidence": confidence,
        "warnings": warnings,
        "analysis_mode": "gemini",
        "warning": warnings[0] if warnings else "",
    }


def _validated_result(receipt: ParsedReceipt, provider: str) -> dict[str, Any]:
    warnings = list(dict.fromkeys(item.strip() for item in receipt.warnings if item.strip()))
    item_total = math.fsum(item.amount for item in receipt.items)
    if abs(item_total - receipt.total) > 0.05 and ITEM_TOTAL_WARNING not in warnings:
        warnings.append(ITEM_TOTAL_WARNING)
    merchant_name = receipt.merchant_name.strip() if receipt.merchant_name else None
    currency = receipt.currency.strip().upper() if receipt.currency else None
    return {
        "store_name": merchant_name or "Unknown Store",
        "merchant_name": merchant_name,
        "receipt_date": receipt.receipt_date.isoformat() if receipt.receipt_date else None,
        "currency": currency,
        "items": [{"name": item.name, "quantity": item.quantity, "unit_price": item.unit_price if item.unit_price is not None else item.amount / item.quantity, "amount": item.amount, "price": item.amount} for item in receipt.items],
        "subtotal": receipt.subtotal,
        "tax": receipt.tax,
        "total": receipt.total,
        "confidence": receipt.confidence,
        "warnings": warnings,
        "analysis_mode": provider,
        "warning": "\n".join(warnings) if warnings else None,
    }


class ReceiptAnalyzer:
    def __init__(
        self,
        api_key: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        mock_enabled: bool | None = None,
        provider: str | None = None,
        gemini_api_key: str | None = None,
        gemini_model: str | None = None,
        gemini_generate: Callable[[bytes, str, type[GeminiReceiptPayload]], Any] | None = None,
    ):
        legacy_mode = mode or get_receipt_mode()
        self.provider = (provider or (legacy_mode if mode else get_receipt_provider())).strip().lower()
        self.legacy_auto = self.provider == "auto"
        if self.legacy_auto:
            self.provider = "openai"
        self.openai_api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self.openai_model = model or get_receipt_model()
        self.gemini_api_key = gemini_api_key if gemini_api_key is not None else os.getenv("GEMINI_API_KEY")
        self.gemini_model = gemini_model or get_gemini_model()
        self.mock_enabled = mock_enabled if mock_enabled is not None else get_receipt_mock_enabled()
        self.gemini_generate = gemini_generate

    def analyze(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        if self.provider not in {"gemini", "openai", "mock"}:
            raise ReceiptAnalysisError("ROOMSYNC_RECEIPT_PROVIDER 설정이 올바르지 않습니다.", 503)
        if self.provider == "mock":
            if not self.mock_enabled:
                raise ReceiptAnalysisError("Mock 분석을 사용하려면 ROOMSYNC_RECEIPT_MOCK_ENABLED=true가 필요합니다.", 503)
            return get_mock_receipt_data()
        if self.provider == "gemini":
            if not self.gemini_api_key and self.gemini_generate is None:
                raise ReceiptAnalysisError("Gemini API 키가 설정되지 않았습니다.", 503)
            return self._analyze_with_gemini(image_bytes, mime_type)
        if not self.openai_api_key:
            if self.legacy_auto and self.mock_enabled:
                return get_mock_receipt_data("OpenAI API 키가 없어 Mock 결과를 표시합니다.")
            raise ReceiptAnalysisError("OpenAI API 키가 설정되지 않았습니다.", 503)
        try:
            return self._analyze_with_openai(image_bytes, mime_type)
        except Exception as error:
            if self.legacy_auto and self.mock_enabled:
                return get_mock_receipt_data("OpenAI 분석을 사용할 수 없어 Mock 결과를 표시합니다.")
            if isinstance(error, ReceiptAnalysisError):
                raise
            logger.exception("OpenAI receipt analysis failed")
            raise ReceiptAnalysisError("OpenAI 영수증 분석에 실패했습니다.") from error

    def _analyze_with_gemini(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        try:
            if self.gemini_generate is not None:
                raw = self.gemini_generate(image_bytes, mime_type, GeminiReceiptPayload)
            else:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.gemini_api_key, http_options=types.HttpOptions(timeout=30_000))
                response = client.models.generate_content(
                    model=self.gemini_model,
                    contents=[RECEIPT_PROMPT, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeminiStructuredReceipt,
                    ),
                )
                if get_receipt_debug_enabled():
                    logger.debug("Gemini raw receipt response: %s", response.text)
                raw = response.parsed if response.parsed is not None else response.text
            return normalize_gemini_receipt(raw)
        except ValidationError as error:
            logger.warning("Gemini response schema validation failed: %s", type(error).__name__)
            raise ReceiptAnalysisError("Gemini 분석 결과 구조가 올바르지 않습니다.", 422) from error
        except ReceiptAnalysisError as error:
            logger.warning("Gemini receipt response rejected: %s", str(error))
            raise
        except Exception as error:
            code = getattr(error, "code", None)
            class_name = type(error).__name__.lower()
            if code == 429:
                raise ReceiptAnalysisError("Gemini 사용량 한도를 초과했습니다. 잠시 후 다시 시도해주세요.", 429) from error
            if "timeout" in class_name or isinstance(error, TimeoutError):
                raise ReceiptAnalysisError("Gemini 분석 시간이 초과되었습니다.", 504) from error
            if mime_type in {"image/heic", "image/heif"}:
                raise ReceiptAnalysisError("HEIC/HEIF 이미지를 분석하지 못했습니다. JPEG 또는 PNG로 변환한 후 다시 시도해주세요.", 422) from error
            logger.warning("Gemini receipt analysis failed: %s", type(error).__name__)
            raise ReceiptAnalysisError("Gemini 영수증 분석에 실패했습니다.", 502) from error

    def _analyze_with_openai(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        client = OpenAI(api_key=self.openai_api_key)
        response = client.responses.create(
            model=self.openai_model,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": "Analyze this receipt. Return JSON with store_name, items (name, quantity, price), and total. Do not include markdown."},
                {"type": "input_image", "image_url": f"data:{mime_type};base64,{base64_image}"},
            ]}],
        )
        try:
            legacy = LegacyReceipt.model_validate(extract_json_from_text(response.output_text))
        except ValidationError as error:
            raise ReceiptAnalysisError("OpenAI 분석 결과 구조가 올바르지 않습니다.", 422) from error
        return {
            "store_name": legacy.store_name,
            "merchant_name": legacy.store_name,
            "receipt_date": None,
            "currency": None,
            "items": [{"name": item.name, "quantity": item.quantity, "unit_price": item.price / item.quantity, "amount": item.price, "price": item.price} for item in legacy.items],
            "subtotal": None,
            "tax": None,
            "total": legacy.total,
            "confidence": None,
            "warnings": [],
            "analysis_mode": "openai",
            "warning": None,
        }
