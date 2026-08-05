import base64
import io
import json
import logging
import math
import os
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

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
MISSING_TAX_WARNING = "GST가 영수증에 표시되지 않았습니다."
MONEY_TOLERANCE = Decimal("0.02")
GEMINI_TIMEOUT_MS = 75_000
GEMINI_MAX_RETRIES = 2
RECEIPT_MAX_LONG_EDGE = 1800
RECEIPT_JPEG_QUALITY = 83
RECEIPT_PROMPT = """
Analyse this Australian purchase receipt and follow the supplied JSON Schema exactly.
Extract merchant_name, receipt_date, currency, items, items_total, subtotal, gst,
discount, fees, rounding, total, amount_paid, gst_inclusion_type, gst_displayed,
model_reported_confidence, and warnings independently.

Use values printed on the receipt first. Never calculate, infer, or invent GST or any
missing monetary value. In particular, never assume GST is 1/11 of total. Australian
displayed prices commonly include GST and basic food may be GST-free. When Total and GST
are both printed, GST may already be included in Total; do not add it again. Extract
Amount Paid separately from Total when both appear. Distinguish Total, Grand Total,
Amount Paid, Balance, EFTPOS, GST, Total GST, Tax, Discount, Savings, Fees, and Rounding.

For each receipt line extract name, quantity, unit_price, line_total, type, tax_marker,
gst_status, model_reported_confidence, applies_to_item_ids, and discount_group_id. type must be item,
discount, coupon, refund, fee, tax, or unknown. Preserve any printed tax marker verbatim. If its meaning is
uncertain use gst_status="unknown". Do not include subtotal, GST, tax, total, payment,
cash, card, EFTPOS, balance, change, discount, savings, fees, or rounding as items.
Keep item names in the receipt's original language.

BUY 2 FOR, ANY 2 FOR, PROMOTIONAL PRICE, SPECIAL, DISCOUNT, and SAVING lines may be
discounts. A negative promotional line is not a normal item and its sign must remain
negative. Preserve its original label in name. Set discount_group_id only when the
related products are certain; otherwise return null so it is treated as a receipt-wide
discount. TOTAL SAVINGS, You saved, and Specials summaries near the receipt footer are
informational and must not be returned as additional discount lines. Only negative
discount lines printed among purchased items affect the calculated total.

If a value is not clearly visible or a number cannot be read, return null and add a
specific warning. Do not force item amounts to reconcile with totals. Monetary values
must be numbers without symbols or thousands separators. gst_inclusion_type must be one
of included, excluded_then_added, not_displayed, mixed, or unknown. gst_status must be
taxable, gst_free, or unknown. Follow the JSON Schema without adding fields.
""".strip()


class ReceiptAnalysisError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def preprocess_receipt_image(image_bytes: bytes, mime_type: str) -> tuple[bytes, str, dict[str, object]]:
    """Apply EXIF orientation and produce a legible, bounded JPEG for vision analysis."""
    started = time.monotonic()
    try:
        if mime_type in {"image/heic", "image/heif"}:
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError as error:
                raise ReceiptAnalysisError("HEIC/HEIF 이미지 변환 기능을 사용할 수 없습니다.", 415) from error
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.load()
            original_size = source.size
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            else:
                image = image.convert("RGB")
            image.thumbnail((RECEIPT_MAX_LONG_EDGE, RECEIPT_MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=RECEIPT_JPEG_QUALITY, optimize=True)
            processed = output.getvalue()
            metadata = {
                "original_bytes": len(image_bytes), "original_width": original_size[0], "original_height": original_size[1],
                "processed_bytes": len(processed), "processed_width": image.width, "processed_height": image.height,
                "elapsed_seconds": time.monotonic() - started,
            }
            return processed, "image/jpeg", metadata
    except ReceiptAnalysisError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ReceiptAnalysisError("지원하지 않거나 손상된 이미지 형식입니다.", 415) from error


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
    line_total: float | str | None = None
    tax_marker: str | None = None
    gst_status: Literal["taxable", "gst_free", "unknown"] | None = None
    model_reported_confidence: float | str | None = None
    confidence: float | str | None = None
    type: Literal["item", "discount", "coupon", "refund", "fee", "tax", "unknown"] | None = None
    applies_to_item_ids: list[int] | None = None
    discount_group_id: str | None = None


class GeminiReceiptPayload(BaseModel):
    merchant: str | None = None
    merchant_name: str | None = None
    store_name: str | None = None
    purchase_date: str | None = None
    date: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[GeminiReceiptItem] = Field(default_factory=list)
    items_total: float | str | None = None
    subtotal: float | str | None = None
    tax: float | str | None = None
    gst: float | str | None = None
    discount: float | str | None = None
    fees: float | str | None = None
    rounding: float | str | None = None
    total: float | str | None = None
    amount_paid: float | str | None = None
    gst_inclusion_type: Literal["included", "excluded_then_added", "not_displayed", "mixed", "unknown"] | None = None
    gst_displayed: bool | None = None
    model_reported_confidence: float | str | None = None
    confidence: float | str | None = None
    warnings: list[str] | str | None = None


class GeminiStructuredReceiptItem(BaseModel):
    name: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None
    tax_marker: str | None = None
    gst_status: Literal["taxable", "gst_free", "unknown"] = "unknown"
    model_reported_confidence: float | None = None
    type: Literal["item", "discount", "coupon", "refund", "fee", "tax", "unknown"] = "item"
    applies_to_item_ids: list[int] | None = None
    discount_group_id: str | None = None


class GeminiStructuredReceipt(BaseModel):
    merchant_name: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[GeminiStructuredReceiptItem]
    items_total: float | None = None
    subtotal: float | None = None
    gst: float | None = None
    discount: float | None = None
    fees: float | None = None
    rounding: float | None = None
    total: float | None = None
    amount_paid: float | None = None
    gst_inclusion_type: Literal["included", "excluded_then_added", "not_displayed", "mixed", "unknown"] = "unknown"
    gst_displayed: bool = False
    model_reported_confidence: float | None = None
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
    return reconcile_receipt_amounts({
        "store_name": "Sample Receipt",
        "merchant_name": "Sample Receipt",
        "receipt_date": None,
        "currency": None,
        "items": [
            {"name": "MATCHA GELATO SINGLE", "quantity": 1, "unit_price": 12.0, "line_total": 12.0, "amount": 12.0, "price": 12.0, "tax_marker": None, "gst_status": "unknown", "model_reported_confidence": 1.0},
        ],
        "items_total": 12.0,
        "subtotal": 12.0,
        "gst": None,
        "discount": None,
        "fees": None,
        "rounding": None,
        "total": 12.0,
        "amount_paid": 12.0,
        "gst_inclusion_type": "not_displayed",
        "gst_displayed": False,
        "model_reported_confidence": 1.0,
        "warnings": [warning] if warning else [],
        "analysis_mode": "mock",
        "warning": warning,
    })


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
    if isinstance(value, (int, float, Decimal)):
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
    # Australian receipts use day/month order, including two-digit years.
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y", "%d %b %Y", "%d %B %Y"):
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


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(_to_float(value, "amount"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number


def _money_value(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _confidence_or_default(value: Any, default: float | None) -> float | None:
    confidence = _decimal_or_none(value)
    if confidence is None:
        return default
    if confidence > 1 and confidence <= 100:
        confidence /= 100
    return min(max(float(confidence), 0.0), 1.0)


def _close(left: Decimal | None, right: Decimal | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= MONEY_TOLERANCE


def calculate_validation_score(receipt: dict[str, Any]) -> dict[str, Any]:
    """Score only deterministic extraction and reconciliation evidence."""
    score = 0
    reasons: list[str] = []
    merchant = str(receipt.get("merchant_name") or "").strip()
    receipt_date = _normalize_date(receipt.get("receipt_date"))
    if merchant and merchant.lower() != "unknown store":
        score += 5
        reasons.append("매장명이 확인되었습니다.")
    if receipt_date:
        score += 5
        reasons.append("영수증 날짜 형식이 확인되었습니다.")

    items = [item for item in receipt.get("items", []) if isinstance(item, dict)]
    regular_items = [item for item in items if str(item.get("type") or "item") == "item"]
    if regular_items:
        score += 5
        reasons.append("유효한 일반 품목이 확인되었습니다.")
        complete_count = sum(
            1 for item in regular_items
            if str(item.get("name") or "").strip()
            and _decimal_or_none(item.get("line_total", item.get("amount"))) is not None
        )
        score += round(10 * complete_count / len(regular_items))
        if complete_count == len(regular_items):
            reasons.append("모든 일반 품목의 이름과 금액이 확인되었습니다.")
        relationships: list[bool] = []
        for item in regular_items:
            quantity = _decimal_or_none(item.get("quantity"))
            unit_price = _decimal_or_none(item.get("unit_price"))
            line_total = _decimal_or_none(item.get("line_total", item.get("amount")))
            if quantity is not None and unit_price is not None and line_total is not None:
                relationships.append(_close(quantity * unit_price, line_total))
        if relationships and all(relationships):
            score += 5
            reasons.append("수량과 단가의 관계가 일치합니다.")

    invalid_lines = False
    for item in items:
        line_type = str(item.get("type") or "item")
        value = _decimal_or_none(item.get("line_total", item.get("amount")))
        if line_type == "item" and value is not None and value < 0:
            invalid_lines = True
        if line_type in {"discount", "coupon", "refund"} and value is not None and value > 0:
            invalid_lines = True
    if items and not invalid_lines:
        score += 10
        reasons.append("품목과 할인 라인의 부호가 유효합니다.")

    calculated_total = _decimal_or_none(receipt.get("calculated_items_total"))
    subtotal = _decimal_or_none(receipt.get("subtotal"))
    total = _decimal_or_none(receipt.get("total"))
    amount_paid = _decimal_or_none(receipt.get("amount_paid"))
    if calculated_total is not None and (_close(calculated_total, subtotal) or _close(calculated_total, total)):
        score += 15
        reasons.append("품목 순합계와 영수증 Total이 일치합니다.")

    if total is not None:
        score += 5
        reasons.append("영수증 Total이 확인되었습니다.")
    if amount_paid is not None:
        score += 5
        reasons.append("Amount Paid가 확인되었습니다.")
    if _close(total, amount_paid):
        score += 15
        reasons.append("Total과 Amount Paid가 일치합니다.")

    gst_type = str(receipt.get("gst_inclusion_type") or "unknown")
    reconciliation = str(receipt.get("reconciliation_status") or "needs_review")
    gst_verified = (
        (gst_type == "included" and reconciliation == "verified_gst_included")
        or (gst_type == "excluded_then_added" and reconciliation == "verified_gst_added")
        or (gst_type in {"not_displayed", "mixed"} and reconciliation == "verified")
    )
    if gst_verified:
        score += 8
        reasons.append(
            "GST가 Total에 포함된 것으로 검증되었습니다."
            if gst_type == "included" else "GST 표시 방식이 금액 관계와 일치합니다."
        )

    informational_markers = ("you saved", "total savings", "total saving", "specials")
    duplicate_savings = any(
        any(marker in str(item.get("name") or "").lower() for marker in informational_markers)
        for item in items
    )
    if not invalid_lines and not duplicate_savings:
        score += 7
        reasons.append("할인 라인이 중복 차감되지 않았습니다.")

    informational_warnings = (
        "GST가 총액에 포함되어 있으므로 별도로 더하지 않았습니다.",
        MISSING_TAX_WARNING,
    )
    warnings = [str(value).strip() for value in receipt.get("warnings", []) if str(value).strip()]
    critical_warnings = [
        warning for warning in warnings
        if warning not in informational_warnings
        and not warning.startswith("정보성 절약 합계")
    ]
    if not critical_warnings:
        score += 5
        reasons.append("정산을 막는 핵심 경고가 없습니다.")
    else:
        reasons.extend(f"확인 필요: {warning}" for warning in critical_warnings)

    if receipt.get("requires_review") or critical_warnings:
        score = min(score, 74)
    score = max(0, min(int(score), 100))
    if score >= 90 and not receipt.get("requires_review") and not critical_warnings:
        status = "verified"
    elif score >= 75 and not receipt.get("requires_review") and not critical_warnings:
        status = "mostly_verified"
    elif score < 50:
        status = "invalid"
    else:
        status = "needs_review"
    return {
        "validation_score": score,
        "validation_status": status,
        "validation_reasons": list(dict.fromkeys(reasons)),
    }


def reconcile_receipt_amounts(receipt: dict[str, Any]) -> dict[str, Any]:
    """Reconcile printed receipt values without inventing GST or payment amounts."""
    result = dict(receipt)
    result["receipt_date"] = _normalize_date(receipt.get("receipt_date"))
    generated_warning_prefixes = (
        "인쇄된 품목 합계와 계산한 품목 합계가 ",
        "품목 합계와 최종 총액이 ",
        "최종 총액과 결제금액이 ",
        "GST가 총액에 포함되어 있으므로 ",
        "최종 결제금액을 확인할 수 없습니다.",
    )
    warnings = [
        str(item).strip()
        for item in receipt.get("warnings", [])
        if str(item).strip()
        and not str(item).strip().startswith(generated_warning_prefixes)
    ]
    money_fields = (
        "items_total", "subtotal", "gst", "discount", "fees", "rounding",
        "total", "amount_paid",
    )
    amounts: dict[str, Decimal | None] = {}
    for field in money_fields:
        raw_value = receipt.get(field)
        amounts[field] = _decimal_or_none(raw_value)
        if raw_value is not None and amounts[field] is None:
            warnings.append(f"{field} 금액을 확인할 수 없습니다.")

    calculation_types = {"item", "discount", "coupon", "refund", "fee", "unknown"}
    item_amounts: list[Decimal | None] = []
    has_line_adjustments = False
    invalid_line_classification = False
    for index, item in enumerate(receipt.get("items", []), start=1):
        if not isinstance(item, dict):
            continue
        line_type = str(item.get("type") or "item")
        value = _decimal_or_none(item.get("line_total", item.get("amount")))
        if line_type in calculation_types:
            item_amounts.append(value)
        if line_type in {"discount", "coupon", "refund", "fee"}:
            has_line_adjustments = True
        if line_type in {"discount", "coupon", "refund"} and value is not None and value > 0:
            warnings.append(f"할인 라인 {index}의 금액은 0 이하인지 확인해 주세요.")
            invalid_line_classification = True
        if line_type == "item" and value is not None and value < 0:
            warnings.append(f"품목 {index}이 일반 상품이지만 금액이 음수입니다.")
            invalid_line_classification = True
    calculated_items_total = (
        sum(item_amounts, Decimal("0.00"))
        if item_amounts and all(value is not None for value in item_amounts)
        else None
    )
    printed_items_total = amounts["items_total"]
    if not has_line_adjustments and printed_items_total is not None and calculated_items_total is not None and not _close(
        printed_items_total, calculated_items_total
    ):
        difference = abs(printed_items_total - calculated_items_total)
        warnings.append(f"인쇄된 품목 합계와 계산한 품목 합계가 {difference:.2f} AUD 차이 납니다.")

    discount = amounts["discount"] or Decimal("0.00")
    fees = amounts["fees"] or Decimal("0.00")
    rounding = amounts["rounding"] or Decimal("0.00")
    subtotal = amounts["subtotal"]
    gst = amounts["gst"]
    total = amounts["total"]
    amount_paid = amounts["amount_paid"]
    item_base = calculated_items_total if has_line_adjustments else (printed_items_total if printed_items_total is not None else calculated_items_total)

    included_calculation = subtotal
    added_calculation = (
        subtotal + gst - discount + fees + rounding
        if subtotal is not None and gst is not None
        else None
    )
    item_calculation = (
        item_base + rounding if item_base is not None and has_line_adjustments
        else item_base - discount + fees + rounding if item_base is not None
        else None
    )
    matches_included = _close(total, included_calculation)
    matches_added = _close(total, added_calculation)
    matches_items = _close(total, item_calculation)

    supplied_type = str(receipt.get("gst_inclusion_type") or "unknown")
    allowed_types = {"included", "excluded_then_added", "not_displayed", "mixed", "unknown"}
    gst_type = supplied_type if supplied_type in allowed_types else "unknown"
    gst_displayed = bool(receipt.get("gst_displayed")) or gst is not None
    if gst_type == "mixed":
        pass
    elif matches_added and gst is not None and gst != 0 and not matches_included:
        gst_type = "excluded_then_added"
    elif matches_included and gst is not None:
        gst_type = "included"
    elif not gst_displayed:
        gst_type = "not_displayed"

    if gst_type == "included" and gst is not None:
        warnings.append("GST가 총액에 포함되어 있으므로 별도로 더하지 않았습니다.")
    if not gst_displayed and MISSING_TAX_WARNING not in warnings:
        warnings.append(MISSING_TAX_WARNING)

    verified_calculations: list[Decimal] = []
    if matches_included and included_calculation is not None:
        verified_calculations.append(included_calculation)
    if matches_added and added_calculation is not None:
        verified_calculations.append(added_calculation)
    if matches_items and item_calculation is not None:
        verified_calculations.append(item_calculation)

    total_verified = total is not None and (
        bool(verified_calculations) or _close(total, amount_paid)
    )
    amount_paid_verified = amount_paid is not None and (
        (total_verified and _close(amount_paid, total))
        or any(_close(amount_paid, value) for value in verified_calculations)
    )

    verified_total: Decimal | None = None
    if amount_paid_verified:
        verified_total = amount_paid
    elif total_verified:
        verified_total = total
    elif total is None:
        if gst_type == "excluded_then_added" and added_calculation is not None:
            verified_total = added_calculation
        elif gst_type in {"included", "not_displayed", "mixed"} and item_calculation is not None:
            verified_total = item_calculation

    if item_base is not None and total is not None and not matches_items:
        difference = abs(item_calculation - total) if item_calculation is not None else abs(item_base - total)
        warnings.append(f"품목 합계와 최종 총액이 {difference:.2f} AUD 차이 납니다.")
    payment_mismatch = amount_paid is not None and total is not None and not _close(amount_paid, total)
    if invalid_line_classification:
        reconciliation_status = "needs_review"
        verified_total = None
    elif payment_mismatch:
        warnings.append(f"최종 총액과 결제금액이 {abs(amount_paid - total):.2f} AUD 차이 납니다.")

    if payment_mismatch:
        reconciliation_status = "mismatch"
    elif verified_total is None:
        reconciliation_status = "needs_review"
        warnings.append("최종 결제금액을 확인할 수 없습니다.")
    elif gst_type == "included" and matches_included:
        reconciliation_status = "verified_gst_included"
    elif gst_type == "excluded_then_added" and matches_added:
        reconciliation_status = "verified_gst_added"
    else:
        reconciliation_status = "verified"

    result.update({field: _money_value(value) for field, value in amounts.items()})
    result["calculated_items_total"] = _money_value(calculated_items_total)
    result["gst_inclusion_type"] = gst_type
    result["gst_displayed"] = gst_displayed
    result["verified_total"] = _money_value(verified_total)
    result["reconciliation_status"] = reconciliation_status
    result["requires_review"] = reconciliation_status in {"mismatch", "needs_review"}
    result["warnings"] = list(dict.fromkeys(warnings))
    result["warning"] = result["warnings"][0] if result["warnings"] else None
    # Backward-compatible alias; null remains null and is never manufactured as zero.
    result["tax"] = result["gst"]
    result.update(calculate_validation_score(result))
    return result


def allocate_shared_item_cents(
    items: list[dict[str, Any]],
) -> tuple[dict[int, int], int]:
    """Split item net totals; adjustments inherit participants instead of selecting their own."""
    allocations: dict[int, int] = {}
    grouped_cents: dict[tuple[int, ...], int] = {}
    regular_lines: list[tuple[tuple[int, ...], int, str | None, int | None]] = []
    adjustments: list[tuple[int, str | None, set[int]]] = []
    for index, item in enumerate(items, start=1):
        line_total = _decimal_or_none(item.get("line_total"))
        line_type = str(item.get("type") or "item")
        if line_type == "tax":
            continue
        if line_total is None:
            raise ValueError(f"공용 품목 {index}의 금액을 확인해주세요.")
        cents = int((line_total * 100).to_integral_value(rounding=ROUND_HALF_UP))
        if line_type in {"discount", "coupon", "refund"}:
            if cents > 0:
                raise ValueError(f"할인 라인 {index}의 금액은 0 이하여야 합니다.")
            adjustments.append((cents, item.get("discount_group_id"), set(item.get("applies_to_item_ids") or [])))
            continue
        if line_type == "fee":
            adjustments.append((cents, item.get("discount_group_id"), set(item.get("applies_to_item_ids") or [])))
            continue
        if cents <= 0:
            raise ValueError(f"일반 품목 {index}의 금액은 0보다 커야 합니다.")
        participants = tuple(sorted({int(value) for value in item.get("participant_user_ids", [])}))
        if not participants:
            raise ValueError(f"공용 품목 {index}의 참여자를 선택해주세요.")
        grouped_cents[participants] = grouped_cents.get(participants, 0) + cents
        regular_lines.append((participants, cents, item.get("discount_group_id"), item.get("receipt_item_id")))

    if not regular_lines:
        raise ValueError("정산할 일반 품목이 없습니다.")
    for adjustment_cents, discount_group_id, applies_to_ids in adjustments:
        targets = [line for line in regular_lines if (
            (discount_group_id is not None and line[2] == discount_group_id)
            or (discount_group_id is None and applies_to_ids and line[3] in applies_to_ids)
        )]
        if not targets:
            targets = regular_lines
        weights: dict[tuple[int, ...], int] = {}
        for participants, cents, _, _ in targets:
            weights[participants] = weights.get(participants, 0) + cents
        weight_total = sum(weights.values())
        absolute = abs(adjustment_cents)
        shares = {participants: absolute * weight // weight_total for participants, weight in weights.items()}
        remainder = absolute - sum(shares.values())
        ranked = sorted(weights, key=lambda participants: (-(absolute * weights[participants] % weight_total), participants))
        for participants in ranked[:remainder]:
            shares[participants] += 1
        sign = -1 if adjustment_cents < 0 else 1
        for participants, share in shares.items():
            grouped_cents[participants] = grouped_cents.get(participants, 0) + sign * share

    total_cents = sum(grouped_cents.values())

    for group_index, (participants, group_total) in enumerate(sorted(grouped_cents.items())):
        base, remainder = divmod(group_total, len(participants))
        for user_id in participants:
            allocations[user_id] = allocations.get(user_id, 0) + base
        rotation = (
            group_total
            + group_index
            + sum((index + 1) * user_id for index, user_id in enumerate(participants))
        ) % len(participants)
        rotated = participants[rotation:] + participants[:rotation]
        rotation_rank = {user_id: index for index, user_id in enumerate(rotated)}
        recipients = sorted(
            participants,
            key=lambda user_id: (allocations[user_id], rotation_rank[user_id]),
        )
        for user_id in recipients[:remainder]:
            allocations[user_id] += 1
    return allocations, total_cents


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
    if not isinstance(raw_items, list):
        raw_items = []

    items: list[dict[str, Any]] = []
    normalization_warnings: list[str] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if isinstance(raw_item, BaseModel):
            raw_item = raw_item.model_dump(exclude_none=True)
        if not isinstance(raw_item, dict):
            normalization_warnings.append(f"품목 {index}의 구조를 읽을 수 없습니다.")
            continue
        name = str(raw_item.get("name") or "").strip() or None
        quantity = _decimal_or_none(raw_item.get("quantity"))
        unit_price = _decimal_or_none(raw_item.get("unit_price", raw_item.get("price")))
        line_total = _decimal_or_none(raw_item.get("line_total", raw_item.get("amount")))
        if line_total is None and quantity is not None and unit_price is not None:
            line_total = (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        gst_status = str(raw_item.get("gst_status") or "unknown")
        if gst_status not in {"taxable", "gst_free", "unknown"}:
            gst_status = "unknown"
        if name is None or line_total is None:
            normalization_warnings.append(f"품목 {index}의 이름 또는 금액을 확인할 수 없습니다.")
        line_type = str(raw_item.get("type") or "item").lower()
        allowed_line_types = {"item", "discount", "coupon", "refund", "fee", "tax", "unknown"}
        if line_type not in allowed_line_types:
            line_type = "unknown"
        normalized_name = (name or "").lower()
        if line_total is not None and line_total < 0 and line_type in {"item", "unknown"} and any(
            marker in normalized_name for marker in ("buy 2 for", "any 2 for", "promotional", "special", "discount", "saving")
        ):
            line_type = "discount"
        if line_type == "item" and line_total is not None and line_total < 0:
            normalization_warnings.append(f"품목 {index}이 일반 상품으로 분류됐지만 금액이 음수입니다.")
        if line_total is not None and line_total >= 0 and any(
            marker in normalized_name for marker in ("you saved", "total savings", "total saving", "specials")
        ):
            normalization_warnings.append(f"정보성 절약 합계 '{name}'는 중복 계산하지 않았습니다.")
            continue
        items.append({
            "name": name,
            "quantity": _money_value(quantity),
            "unit_price": _money_value(unit_price),
            "line_total": _money_value(line_total),
            "tax_marker": str(raw_item.get("tax_marker")).strip() if raw_item.get("tax_marker") else None,
            "gst_status": gst_status,
            "model_reported_confidence": _confidence_or_default(
                raw_item.get("model_reported_confidence", raw_item.get("confidence")), None
            ),
            "type": line_type,
            "applies_to_item_ids": raw_item.get("applies_to_item_ids"),
            "discount_group_id": str(raw_item["discount_group_id"]) if raw_item.get("discount_group_id") is not None else None,
            "amount": _money_value(line_total),
            "price": _money_value(line_total),
        })

    model_reported_confidence = _confidence_or_default(
        payload.get("model_reported_confidence", payload.get("confidence")), None
    )
    raw_warnings = payload.get("warnings")
    if isinstance(raw_warnings, str):
        warnings = [raw_warnings.strip()] if raw_warnings.strip() else []
    elif isinstance(raw_warnings, list):
        warnings = [str(item).strip() for item in raw_warnings if str(item).strip()]
    else:
        warnings = []
    warnings.extend(normalization_warnings)

    receipt_date = next(
        (payload.get(key) for key in ("purchase_date", "date", "receipt_date") if payload.get(key)),
        None,
    )
    currency = str(payload["currency"]).strip().upper() if payload.get("currency") else None
    base_result = {
        "store_name": merchant_name or "Unknown Store",
        "merchant_name": merchant_name,
        "receipt_date": _normalize_date(receipt_date),
        "currency": currency,
        "items": items,
        "items_total": payload.get("items_total"),
        "subtotal": payload.get("subtotal"),
        "gst": payload.get("gst", payload.get("tax")),
        "discount": payload.get("discount"),
        "fees": payload.get("fees"),
        "rounding": payload.get("rounding"),
        "total": payload.get("total"),
        "amount_paid": payload.get("amount_paid"),
        "gst_inclusion_type": payload.get("gst_inclusion_type", "unknown"),
        "gst_displayed": payload.get("gst_displayed", payload.get("gst") is not None or payload.get("tax") is not None),
        "model_reported_confidence": model_reported_confidence,
        "warnings": list(dict.fromkeys(warnings)),
        "analysis_mode": "gemini",
    }
    return reconcile_receipt_amounts(base_result)


def _validated_result(receipt: ParsedReceipt, provider: str) -> dict[str, Any]:
    warnings = list(dict.fromkeys(item.strip() for item in receipt.warnings if item.strip()))
    item_total = math.fsum(item.amount for item in receipt.items)
    if abs(item_total - receipt.total) > 0.05 and ITEM_TOTAL_WARNING not in warnings:
        warnings.append(ITEM_TOTAL_WARNING)
    merchant_name = receipt.merchant_name.strip() if receipt.merchant_name else None
    currency = receipt.currency.strip().upper() if receipt.currency else None
    return reconcile_receipt_amounts({
        "store_name": merchant_name or "Unknown Store",
        "merchant_name": merchant_name,
        "receipt_date": receipt.receipt_date.isoformat() if receipt.receipt_date else None,
        "currency": currency,
        "items": [{"name": item.name, "quantity": item.quantity, "unit_price": item.unit_price if item.unit_price is not None else item.amount / item.quantity, "line_total": item.amount, "amount": item.amount, "price": item.amount, "tax_marker": None, "gst_status": "unknown", "model_reported_confidence": None} for item in receipt.items],
        "items_total": item_total,
        "subtotal": receipt.subtotal,
        "gst": receipt.tax,
        "discount": None,
        "fees": None,
        "rounding": None,
        "total": receipt.total,
        "amount_paid": receipt.total,
        "gst_inclusion_type": "unknown",
        "gst_displayed": receipt.tax is not None,
        "model_reported_confidence": receipt.confidence,
        "warnings": warnings,
        "analysis_mode": provider,
        "warning": "\n".join(warnings) if warnings else None,
    })


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
        sleep_fn: Callable[[float], None] = time.sleep,
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
        self.sleep_fn = sleep_fn

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
        gemini_started = time.monotonic()
        for attempt in range(GEMINI_MAX_RETRIES + 1):
            request_started = time.monotonic()
            logger.info("[receipt] gemini_started attempt=%s", attempt + 1)
            try:
                raw = self._generate_with_gemini(image_bytes, mime_type)
                logger.info("[receipt] gemini_completed attempt=%s elapsed=%.2fs", attempt + 1, time.monotonic() - request_started)
                parse_started = time.monotonic()
                result = normalize_gemini_receipt(raw)
                logger.info("[receipt] json_parsed elapsed=%.2fs", time.monotonic() - parse_started)
                logger.info("[receipt] amount_verified elapsed=%.2fs", time.monotonic() - parse_started)
                logger.info("[receipt] gemini_total attempts=%s elapsed=%.2fs", attempt + 1, time.monotonic() - gemini_started)
                return result
            except (ValidationError, ReceiptAnalysisError) as error:
                if isinstance(error, ValidationError):
                    logger.warning("Gemini response schema validation failed: %s", type(error).__name__)
                    raise ReceiptAnalysisError("영수증 내용을 구조화하지 못했습니다. 수동 입력을 이용해 주세요.", 422) from error
                raise
            except Exception as error:
                status_code = self._gemini_error_code(error)
                retryable = self._is_retryable_gemini_error(error, status_code)
                logger.warning("[receipt] gemini_failed attempt=%s code=%s retryable=%s elapsed=%.2fs error=%s", attempt + 1, status_code, retryable, time.monotonic() - request_started, type(error).__name__)
                if retryable and attempt < GEMINI_MAX_RETRIES:
                    self.sleep_fn((2, 5)[attempt])
                    continue
                logger.warning("[receipt] gemini_total attempts=%s last_code=%s elapsed=%.2fs", attempt + 1, status_code, time.monotonic() - gemini_started)
                raise self._map_gemini_error(error, mime_type, status_code) from error
        raise ReceiptAnalysisError("Gemini 영수증 분석에 실패했습니다.", 502)

    def _generate_with_gemini(self, image_bytes: bytes, mime_type: str) -> Any:
        if self.gemini_generate is not None:
            return self.gemini_generate(image_bytes, mime_type, GeminiReceiptPayload)
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.gemini_api_key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
        response = client.models.generate_content(
            model=self.gemini_model,
            contents=[RECEIPT_PROMPT, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=GeminiStructuredReceipt),
        )
        if get_receipt_debug_enabled():
            logger.debug("Gemini receipt response received; parsed=%s", response.parsed is not None)
        return response.parsed if response.parsed is not None else response.text

    @staticmethod
    def _gemini_error_code(error: Exception) -> int | None:
        for value in (getattr(error, "code", None), getattr(error, "status_code", None), getattr(error, "status", None)):
            try:
                if value is not None:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _is_retryable_gemini_error(error: Exception, code: int | None) -> bool:
        name = type(error).__name__.lower()
        return code in {429, 500, 502, 503, 504} or isinstance(error, (TimeoutError, ConnectionError)) or any(token in name for token in ("timeout", "connection", "network"))

    @staticmethod
    def _map_gemini_error(error: Exception, mime_type: str, code: int | None) -> ReceiptAnalysisError:
        name = type(error).__name__.lower()
        if code == 429:
            return ReceiptAnalysisError("AI 요청이 많습니다. 잠시 후 다시 시도해 주세요.", 429)
        if code in {503, 504} and "timeout" not in name:
            return ReceiptAnalysisError("AI 서버가 일시적으로 응답하지 않습니다.", code)
        if isinstance(error, TimeoutError) or "timeout" in name:
            return ReceiptAnalysisError("AI 영수증 분석 시간이 초과되었습니다. 이미지를 잘라서 다시 시도해 주세요.", 504)
        if code in {500, 502} or isinstance(error, ConnectionError) or "network" in name or "connection" in name:
            return ReceiptAnalysisError("AI 서버가 일시적으로 응답하지 않습니다.", 503)
        if mime_type in {"image/heic", "image/heif"}:
            return ReceiptAnalysisError("지원하지 않는 이미지 형식입니다. JPEG로 변환해 주세요.", 415)
        return ReceiptAnalysisError("Gemini 영수증 분석에 실패했습니다.", 502)

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
            "model_reported_confidence": None,
            "warnings": [],
            "analysis_mode": "openai",
            "warning": None,
        }
