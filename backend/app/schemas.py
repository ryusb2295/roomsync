import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=2, max_length=40)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("올바른 이메일 주소를 입력해주세요.")
        return normalized

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("이름은 2자 이상 입력해주세요.")
        return normalized


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class HouseCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    location: str = Field(min_length=1, max_length=120)

    @field_validator("name", "location")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class HouseResponse(BaseModel):
    id: int
    name: str
    location: str
    invite_code: str
    created_by: str
    owner_id: int | None
    created_at: str
    member_count: int


class HouseJoinRequest(BaseModel):
    invite_code: str = ""


class HouseOwnerTransferRequest(BaseModel):
    new_owner_user_id: int = Field(gt=0)


class HouseDeleteRequest(BaseModel):
    confirmation: str = ""


class HouseDeleteResponse(BaseModel):
    deleted_counts: dict[str, int]


class AccountDeleteRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: str = ""


class AccountDeleteResponse(BaseModel):
    deleted_solo_houses: int
    deleted_at: str


class HouseMemberResponse(BaseModel):
    id: int
    house_id: int
    user_id: int
    display_name: str
    email: str
    role: str
    joined_at: str


class HealthResponse(BaseModel):
    status: str


class ReceiptItemResponse(BaseModel):
    receipt_item_id: int | None = None
    name: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    price: float | None = None
    unit_price: float | None = None
    line_total: float | None = None
    amount: float | None = None
    tax_marker: str | None = None
    gst_status: Literal["taxable", "gst_free", "unknown"] = "unknown"
    model_reported_confidence: float | None = Field(default=None, ge=0, le=1)
    type: Literal["item", "discount", "coupon", "refund", "fee", "tax", "unknown"] = "item"
    applies_to_item_ids: list[int] | None = None
    discount_group_id: str | None = None


class ReceiptAnalysisResponse(BaseModel):
    receipt_id: int | None = None
    uploaded_by: int | None = None
    store_name: str
    merchant_name: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[ReceiptItemResponse]
    items_total: float | None = None
    calculated_items_total: float | None = None
    subtotal: float | None = None
    tax: float | None = None
    gst: float | None = None
    discount: float | None = None
    fees: float | None = None
    rounding: float | None = None
    total: float | None = None
    amount_paid: float | None = None
    gst_inclusion_type: Literal["included", "excluded_then_added", "not_displayed", "mixed", "unknown"] = "unknown"
    gst_displayed: bool = False
    verified_total: float | None = None
    reconciliation_status: Literal["verified", "verified_gst_included", "verified_gst_added", "mismatch", "needs_review"]
    requires_review: bool
    model_reported_confidence: float | None = Field(default=None, ge=0, le=1)
    validation_score: int = Field(ge=0, le=100)
    validation_status: Literal["verified", "mostly_verified", "needs_review", "invalid"]
    validation_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    analysis_mode: str
    warning: str | None = None


class ReceiptReconciliationRequest(BaseModel):
    receipt_id: int | None = None
    uploaded_by: int | None = None
    store_name: str = "Unknown Store"
    merchant_name: str | None = None
    receipt_date: str | None = None
    currency: str | None = None
    items: list[ReceiptItemResponse] = Field(default_factory=list)
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
    model_reported_confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class ChoreAssigneeResponse(BaseModel):
    user_id: int
    display_name: str


class ChoreResponse(BaseModel):
    id: int
    house_id: int
    title: str
    description: str
    assignee_user_id: int
    scheduled_date: date
    is_completed: bool
    status: str
    completed_at: str | None
    created_by: int | None
    created_at: str
    assignee: ChoreAssigneeResponse


class ChoreCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    assignee_user_id: int = Field(gt=0)
    scheduled_date: date

    @field_validator("title", "description")
    @classmethod
    def normalize_chore_text(cls, value: str) -> str:
        return value.strip()


class ChoreUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    assignee_user_id: int | None = Field(default=None, gt=0)
    scheduled_date: date | None = None

    @field_validator("title", "description")
    @classmethod
    def normalize_optional_chore_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ChoreCompleteRequest(BaseModel):
    is_completed: bool


class ShoppingItemCreateRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=100)

    @field_validator("item_name")
    @classmethod
    def normalize_item_name(cls, value: str) -> str:
        return value.strip()


class ShoppingItemCompleteRequest(BaseModel):
    is_completed: bool


class ShoppingItemResponse(BaseModel):
    id: int
    house_id: int
    item_name: str
    added_by: str
    status: str
    is_completed: bool


class ShoppingItemDeleteResponse(BaseModel):
    deleted_item_id: int


class ShoppingItemsBulkDeleteResponse(BaseModel):
    deleted_count: int


class ReceiptSettlementItem(BaseModel):
    receipt_item_id: int | None = None
    name: str | None = None
    line_total: Decimal
    amount_cents: int | None = None
    participant_user_ids: list[int] = Field(default_factory=list)
    type: Literal["item", "discount", "coupon", "refund", "fee", "tax", "unknown"] = "item"
    applies_to_item_ids: list[int] | None = None
    discount_group_id: str | None = None


class SettlementCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    total_amount: float = Field(gt=0)
    participant_user_ids: list[int] = Field(min_length=1)
    receipt_verified_total: float | None = Field(default=None, gt=0)
    receipt_reconciliation_status: Literal["verified", "verified_gst_included", "verified_gst_added", "mismatch", "needs_review"] | None = None
    receipt_items: list[ReceiptSettlementItem] | None = None
    receipt_id: int | None = Field(default=None, gt=0)
    payer_id: int | None = Field(default=None, gt=0)
    uploaded_by: int | None = Field(default=None, gt=0)
    receipt_date: date | None = None
    total_amount_cents: int | None = Field(default=None, gt=0)
    items: list[ReceiptSettlementItem] | None = None

    @field_validator("title")
    @classmethod
    def normalize_settlement_title(cls, value: str) -> str:
        return value.strip()
class SettlementCreatorResponse(BaseModel):
    user_id: int
    name: str


class SettlementParticipantResponse(BaseModel):
    id: int | None = None
    user_id: int
    name: str
    amount: float
    display_name: str
    share_amount_cents: int
    role: Literal["payer", "participant"]
    payment_status: Literal["payer", "unpaid", "paid"]
    paid_at: str | None = None
    confirmed_by: int | None = None


class SettlementResponse(BaseModel):
    settlement_id: int
    house_id: int
    title: str
    total_amount: float
    total_amount_cents: int
    receipt_id: int | None = None
    payer_id: int | None = None
    payer_name: str | None = None
    uploaded_by: int | None = None
    uploaded_by_name: str | None = None
    receipt_date: date | None = None
    created_by: SettlementCreatorResponse | None
    created_at: str
    completed_at: str | None = None
    status: Literal["in_progress", "completed"]
    is_completed: bool
    participants: list[SettlementParticipantResponse]


class SettlementDeleteResponse(BaseModel):
    deleted_settlement_id: int
    deleted_at: str
    deleted_by: int


class SettlementPaymentStatusRequest(BaseModel):
    payment_status: Literal["paid", "unpaid"]


class SettlementPaymentStatusResponse(BaseModel):
    settlement_id: int
    participant_user_id: int
    payment_status: Literal["paid", "unpaid"]
    settlement_status: Literal["in_progress", "completed"]
    paid_at: str | None = None
    completed_at: str | None = None
