from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
import logging
import time
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from backend.app.config import get_cors_origins, get_database_path
from backend.app.database import (
    AccountOwnershipConflictError,
    AlreadyHouseMemberError,
    Database,
    DuplicateUserError,
    HouseConfirmationError,
    HouseNotFoundError,
    InvalidOwnerTransferError,
    NotHouseMemberError,
    NotHouseOwnerError,
    OwnerCannotLeaveError,
    ShoppingItemNotCompletedError,
    ShoppingItemNotFoundError,
    InvalidSettlementParticipantsError,
    SettlementAlreadyDeletedError,
    SettlementDeleteForbiddenError,
    SettlementNotFoundError,
    SettlementPaymentForbiddenError,
    SettlementParticipantNotFoundError,
    PayerPaymentStatusError,
)
from backend.app.schemas import (
    AccountDeleteRequest,
    AccountDeleteResponse,
    AuthResponse,
    ChoreAssigneeResponse,
    ChoreCompleteRequest,
    ChoreCreateRequest,
    ChoreResponse,
    ChoreUpdateRequest,
    HealthResponse,
    HouseCreateRequest,
    HouseDeleteRequest,
    HouseDeleteResponse,
    HouseJoinRequest,
    HouseMemberResponse,
    HouseOwnerTransferRequest,
    HouseResponse,
    LoginRequest,
    ReceiptAnalysisResponse,
    ReceiptReconciliationRequest,
    SignupRequest,
    ShoppingItemCompleteRequest,
    ShoppingItemCreateRequest,
    ShoppingItemDeleteResponse,
    ShoppingItemResponse,
    ShoppingItemsBulkDeleteResponse,
    SettlementCreateRequest,
    SettlementCreatorResponse,
    SettlementDeleteResponse,
    SettlementParticipantResponse,
    SettlementPaymentStatusRequest,
    SettlementPaymentStatusResponse,
    SettlementResponse,
    UserResponse,
)
from backend.app.receipt_analyzer import ReceiptAnalysisError, ReceiptAnalyzer, allocate_shared_item_cents, preprocess_receipt_image, reconcile_receipt_amounts
from backend.app.security import create_access_token, hash_password, verify_password

bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


MAX_RECEIPT_BYTES = 25 * 1024 * 1024
ALLOWED_RECEIPT_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


def create_app(
    database_path: Path | None = None,
    receipt_analyzer: ReceiptAnalyzer | None = None,
) -> FastAPI:
    database = Database(database_path or get_database_path())
    analyzer = receipt_analyzer or ReceiptAnalyzer()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        application.state.database = database
        application.state.receipt_analyzer = analyzer
        yield

    application = FastAPI(
        title="RoomSync API",
        version="0.1.0",
        lifespan=lifespan,
    )
    origins = get_cors_origins()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_database(request: Request) -> Database:
        return request.app.state.database

    def get_current_user(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
        db: Annotated[Database, Depends(get_database)],
    ) -> dict[str, object]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
        user = db.get_user_by_token(credentials.credentials)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="로그인 세션이 만료되었습니다.",
            )
        return user

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.post("/auth/signup", response_model=AuthResponse, status_code=201)
    def signup(payload: SignupRequest, db: Annotated[Database, Depends(get_database)]) -> AuthResponse:
        try:
            user = db.create_user(
                email=payload.email,
                password_hash=hash_password(payload.password),
                display_name=payload.display_name,
            )
        except DuplicateUserError as error:
            detail = "이미 가입된 이메일입니다." if error.field == "email" else "이미 사용 중인 이름입니다."
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from error

        token = create_access_token()
        db.create_session(int(user["id"]), token)
        return AuthResponse(access_token=token, user=UserResponse(**user))

    @application.post("/auth/login", response_model=AuthResponse)
    def login(payload: LoginRequest, db: Annotated[Database, Depends(get_database)]) -> AuthResponse:
        user = db.get_user_with_password(payload.email)
        if user is None or not verify_password(payload.password, str(user["password_hash"])):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="이메일 또는 비밀번호가 올바르지 않습니다.",
            )

        token = create_access_token()
        db.create_session(int(user["id"]), token)
        public_user = UserResponse(
            id=int(user["id"]),
            email=str(user["email"]),
            display_name=str(user["display_name"]),
        )
        return AuthResponse(access_token=token, user=public_user)

    @application.get("/auth/me", response_model=UserResponse)
    def me(user: Annotated[dict[str, object], Depends(get_current_user)]) -> UserResponse:
        return UserResponse(**user)

    @application.post("/auth/logout", status_code=204)
    def logout(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
        db: Annotated[Database, Depends(get_database)],
        _: Annotated[dict[str, object], Depends(get_current_user)],
    ) -> None:
        if credentials:
            db.delete_session(credentials.credentials)

    @application.delete("/auth/me", response_model=AccountDeleteResponse)
    def delete_account(
        payload: AccountDeleteRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> AccountDeleteResponse:
        if payload.confirmation != "DELETE":
            raise HTTPException(
                status_code=400,
                detail="확인 문구에 DELETE를 정확히 입력해주세요.",
            )
        password_hash = db.get_user_password(int(user["id"]))
        if password_hash is None or not verify_password(payload.password, password_hash):
            raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
        try:
            result = db.delete_account(int(user["id"]))
        except AccountOwnershipConflictError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "다른 멤버가 있는 하우스의 소유권을 이전하거나 하우스를 삭제해주세요.",
                    "blocking_houses": error.houses,
                },
            ) from error
        return AccountDeleteResponse(**result)

    @application.get("/houses", response_model=list[HouseResponse])
    def list_houses(
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> list[HouseResponse]:
        houses = db.list_houses(int(user["id"]))
        return [HouseResponse(**house) for house in houses]

    @application.post("/houses", response_model=HouseResponse, status_code=201)
    def create_house(
        payload: HouseCreateRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseResponse:
        try:
            house = db.create_house(
                payload.name,
                payload.location,
                int(user["id"]),
                str(user["display_name"]),
            )
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return HouseResponse(**house)

    @application.post("/houses/join", response_model=HouseResponse)
    def join_house(
        payload: HouseJoinRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseResponse:
        invite_code = "".join(payload.invite_code.split()).upper()
        if not invite_code:
            raise HTTPException(status_code=400, detail="초대코드를 입력해주세요.")
        if not (6 <= len(invite_code) <= 8 and invite_code.isalnum() and invite_code.isascii()):
            raise HTTPException(
                status_code=400,
                detail="초대코드는 6~8자리 영문 대문자와 숫자 조합입니다.",
            )
        try:
            house = db.join_house_by_invite(
                invite_code,
                int(user["id"]),
                str(user["display_name"]),
            )
        except HouseNotFoundError as error:
            raise HTTPException(status_code=404, detail="초대코드에 해당하는 하우스가 없습니다.") from error
        except AlreadyHouseMemberError as error:
            raise HTTPException(status_code=409, detail="이미 참여 중인 하우스입니다.") from error
        return HouseResponse(**house)

    @application.get("/houses/{house_id}", response_model=HouseResponse)
    def get_house(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        house = db.get_house(house_id, int(user["id"]))
        if house is None:
            raise HTTPException(status_code=404, detail="하우스를 찾을 수 없습니다.")
        return HouseResponse(**house)

    @application.get(
        "/houses/{house_id}/members",
        response_model=list[HouseMemberResponse],
    )
    def list_house_members(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> list[HouseMemberResponse]:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        return [
            HouseMemberResponse(**member)
            for member in db.list_house_members(house_id)
        ]

    @application.post("/houses/{house_id}/leave", status_code=204)
    def leave_house(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> None:
        try:
            db.leave_house(house_id, int(user["id"]))
        except HouseNotFoundError as error:
            raise HTTPException(status_code=404, detail="하우스를 찾을 수 없습니다.") from error
        except NotHouseMemberError as error:
            raise HTTPException(status_code=403, detail="이 하우스의 멤버가 아닙니다.") from error
        except OwnerCannotLeaveError as error:
            raise HTTPException(
                status_code=409,
                detail="하우스 소유자는 바로 나갈 수 없습니다. 소유권을 이전하거나 하우스를 삭제해주세요.",
            ) from error

    @application.patch("/houses/{house_id}/owner", response_model=HouseResponse)
    def transfer_house_owner(
        house_id: int,
        payload: HouseOwnerTransferRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseResponse:
        try:
            house = db.transfer_house_owner(
                house_id, int(user["id"]), payload.new_owner_user_id
            )
        except HouseNotFoundError as error:
            raise HTTPException(status_code=404, detail="하우스를 찾을 수 없습니다.") from error
        except NotHouseMemberError as error:
            raise HTTPException(status_code=403, detail="이 하우스의 멤버가 아닙니다.") from error
        except NotHouseOwnerError as error:
            raise HTTPException(status_code=403, detail="하우스 소유자만 소유권을 이전할 수 있습니다.") from error
        except InvalidOwnerTransferError as error:
            raise HTTPException(
                status_code=400,
                detail="새 소유자는 본인이 아닌 현재 하우스 멤버여야 합니다.",
            ) from error
        return HouseResponse(**house)

    @application.delete(
        "/houses/{house_id}", response_model=HouseDeleteResponse
    )
    def delete_house(
        house_id: int,
        payload: HouseDeleteRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseDeleteResponse:
        try:
            counts = db.delete_house(house_id, int(user["id"]), payload.confirmation)
        except HouseNotFoundError as error:
            raise HTTPException(status_code=404, detail="하우스를 찾을 수 없습니다.") from error
        except NotHouseMemberError as error:
            raise HTTPException(status_code=403, detail="이 하우스의 멤버가 아닙니다.") from error
        except NotHouseOwnerError as error:
            raise HTTPException(status_code=403, detail="하우스 소유자만 하우스를 삭제할 수 있습니다.") from error
        except HouseConfirmationError as error:
            raise HTTPException(status_code=400, detail="하우스 이름이 일치하지 않습니다.") from error
        return HouseDeleteResponse(deleted_counts=counts)

    @application.get(
        "/houses/{house_id}/deletion-impact", response_model=HouseDeleteResponse
    )
    def get_house_deletion_impact(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> HouseDeleteResponse:
        try:
            counts = db.get_house_deletion_impact(house_id, int(user["id"]))
        except HouseNotFoundError as error:
            raise HTTPException(status_code=404, detail="하우스를 찾을 수 없습니다.") from error
        except NotHouseMemberError as error:
            raise HTTPException(status_code=403, detail="이 하우스의 멤버가 아닙니다.") from error
        except NotHouseOwnerError as error:
            raise HTTPException(status_code=403, detail="하우스 소유자만 삭제 영향을 확인할 수 있습니다.") from error
        return HouseDeleteResponse(deleted_counts=counts)

    @application.post(
        "/houses/{house_id}/receipts/analyze",
        response_model=ReceiptAnalysisResponse,
    )
    async def analyze_receipt(
        house_id: int,
        file: UploadFile,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
        request: Request,
    ) -> ReceiptAnalysisResponse:
        total_started = time.monotonic()
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")

        mime_type = (file.content_type or "").lower()
        if mime_type not in ALLOWED_RECEIPT_MIME_TYPES:
            raise HTTPException(status_code=400, detail="JPEG, PNG, WEBP, HEIC 또는 HEIF 이미지만 업로드할 수 있습니다.")

        image_bytes = await file.read(MAX_RECEIPT_BYTES + 1)
        await file.close()
        logger.info("[receipt] upload_received size=%s mime_type=%s", len(image_bytes), mime_type)
        if not image_bytes:
            raise HTTPException(status_code=400, detail="업로드한 이미지가 비어 있습니다.")
        if len(image_bytes) > MAX_RECEIPT_BYTES:
            raise HTTPException(status_code=413, detail="이미지 크기가 너무 큽니다. 25MB 이하 이미지로 다시 시도해 주세요.")

        try:
            if request.app.state.receipt_analyzer.provider == "gemini":
                image_bytes, mime_type, metadata = await run_in_threadpool(
                    preprocess_receipt_image, image_bytes, mime_type
                )
                logger.info("[receipt] image_decoded original=%sx%s elapsed=%.2fs", metadata["original_width"], metadata["original_height"], metadata["elapsed_seconds"])
                logger.info("[receipt] image_preprocessed original_bytes=%s processed_bytes=%s resolution=%sx%s elapsed=%.2fs", metadata["original_bytes"], metadata["processed_bytes"], metadata["processed_width"], metadata["processed_height"], metadata["elapsed_seconds"])
            result = await run_in_threadpool(
                request.app.state.receipt_analyzer.analyze,
                image_bytes,
                mime_type,
            )
        except ReceiptAnalysisError as error:
            logger.info("[receipt] total_elapsed=%.2fs status=failed code=%s", time.monotonic() - total_started, error.status_code)
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        persisted = db.create_receipt_analysis(house_id, int(user["id"]), result)
        result["receipt_id"] = persisted["receipt_id"]
        result["uploaded_by"] = persisted["uploaded_by"]
        for item, item_id in zip(result.get("items", []), persisted["item_ids"]):
            item["receipt_item_id"] = item_id
        logger.info("[receipt] total_elapsed=%.2fs status=completed", time.monotonic() - total_started)
        return ReceiptAnalysisResponse(**result)

    @application.post(
        "/houses/{house_id}/receipts/reconcile",
        response_model=ReceiptAnalysisResponse,
    )
    def reconcile_receipt(
        house_id: int,
        payload: ReceiptReconciliationRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ReceiptAnalysisResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        data = payload.model_dump()
        data["analysis_mode"] = "edited"
        data["warning"] = None
        result = reconcile_receipt_amounts(data)
        if payload.receipt_id is not None:
            receipt = db.get_receipt(house_id, payload.receipt_id)
            if receipt is None:
                raise HTTPException(status_code=404, detail="영수증을 찾을 수 없습니다.")
            if not db.update_receipt_analysis(house_id, payload.receipt_id, result):
                raise HTTPException(status_code=404, detail="영수증을 찾을 수 없습니다.")
            result["receipt_id"] = payload.receipt_id
            result["uploaded_by"] = receipt.get("uploaded_by")
            for output_item, input_item in zip(result.get("items", []), payload.items):
                output_item["receipt_item_id"] = input_item.receipt_item_id
        return ReceiptAnalysisResponse(**result)

    def serialize_chore(row: dict[str, object]) -> ChoreResponse:
        return ChoreResponse(
            id=int(row["id"]),
            house_id=int(row["house_id"]),
            title=str(row["title"]),
            description=str(row["description"]),
            assignee_user_id=int(row["assignee_user_id"]),
            scheduled_date=str(row["scheduled_date"]),
            is_completed=str(row["status"]) == "완료",
            status=str(row["status"]),
            completed_at=str(row["completed_at"])
            if row["completed_at"] is not None
            else None,
            created_by=int(row["created_by"])
            if row["created_by"] is not None
            else None,
            created_at=str(row["created_at"]),
            assignee=ChoreAssigneeResponse(
                user_id=int(row["assignee_user_id"]),
                display_name=str(row["assignee_display_name"]),
            ),
        )

    @application.get("/houses/{house_id}/chores", response_model=list[ChoreResponse])
    def list_chores(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> list[ChoreResponse]:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        return [serialize_chore(row) for row in db.list_validated_chores(house_id)]

    @application.post(
        "/houses/{house_id}/chores",
        response_model=ChoreResponse,
        status_code=201,
    )
    def create_chore(
        house_id: int,
        payload: ChoreCreateRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ChoreResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        if not payload.title:
            raise HTTPException(status_code=400, detail="청소 항목명을 입력해주세요.")
        if not db.is_house_member(house_id, payload.assignee_user_id):
            raise HTTPException(status_code=400, detail="담당자는 현재 하우스 멤버여야 합니다.")
        chore = db.create_chore(
            house_id=house_id,
            title=payload.title,
            description=payload.description,
            assignee_user_id=payload.assignee_user_id,
            scheduled_date=payload.scheduled_date.isoformat(),
            created_by=int(user["id"]),
        )
        if chore is None:
            raise HTTPException(status_code=400, detail="담당자 정보를 확인해주세요.")
        return serialize_chore(chore)

    @application.patch(
        "/houses/{house_id}/chores/{chore_id}",
        response_model=ChoreResponse,
    )
    def update_chore(
        house_id: int,
        chore_id: int,
        payload: ChoreUpdateRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ChoreResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        changes: dict[str, object] = {}
        for field in payload.model_fields_set:
            value = getattr(payload, field)
            if value is not None:
                changes[field] = value.isoformat() if field == "scheduled_date" else value
        if not changes:
            raise HTTPException(status_code=400, detail="수정할 내용을 입력해주세요.")
        if "title" in changes and not str(changes["title"]):
            raise HTTPException(status_code=400, detail="청소 항목명을 입력해주세요.")
        if "assignee_user_id" in changes and not db.is_house_member(
            house_id, int(changes["assignee_user_id"])
        ):
            raise HTTPException(status_code=400, detail="담당자는 현재 하우스 멤버여야 합니다.")
        chore = db.update_chore(house_id, chore_id, changes)
        if chore is None:
            raise HTTPException(status_code=404, detail="청소 일정을 찾을 수 없습니다.")
        return serialize_chore(chore)

    @application.patch(
        "/houses/{house_id}/chores/{chore_id}/complete",
        response_model=ChoreResponse,
    )
    def complete_chore(
        house_id: int,
        chore_id: int,
        payload: ChoreCompleteRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ChoreResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        chore = db.set_chore_completion(house_id, chore_id, payload.is_completed)
        if chore is None:
            raise HTTPException(status_code=404, detail="청소 일정을 찾을 수 없습니다.")
        return serialize_chore(chore)

    @application.delete(
        "/houses/{house_id}/chores/{chore_id}",
        status_code=204,
    )
    def delete_chore(
        house_id: int,
        chore_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> None:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        if not db.delete_chore(house_id, chore_id):
            raise HTTPException(status_code=404, detail="청소 일정을 찾을 수 없습니다.")

    def serialize_shopping_item(row: dict[str, object]) -> ShoppingItemResponse:
        return ShoppingItemResponse(
            id=int(row["id"]),
            house_id=int(row["house_id"]),
            item_name=str(row["item_name"]),
            added_by=str(row["added_by"]),
            status=str(row["status"]),
            is_completed=str(row["status"]) in {"구매 완료", "완료", "completed"},
        )

    @application.get(
        "/houses/{house_id}/shopping-items",
        response_model=list[ShoppingItemResponse],
    )
    def list_shopping_items(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> list[ShoppingItemResponse]:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 장바구니를 조회할 수 있습니다.")
        return [serialize_shopping_item(row) for row in db.list_shopping_items(house_id)]

    @application.post(
        "/houses/{house_id}/shopping-items",
        response_model=ShoppingItemResponse,
        status_code=201,
    )
    def create_shopping_item(
        house_id: int,
        payload: ShoppingItemCreateRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ShoppingItemResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 품목을 추가할 수 있습니다.")
        if not payload.item_name:
            raise HTTPException(status_code=400, detail="품목명을 입력해주세요.")
        return serialize_shopping_item(
            db.create_shopping_item(house_id, payload.item_name, str(user["display_name"]))
        )

    @application.patch(
        "/houses/{house_id}/shopping-items/{item_id}/complete",
        response_model=ShoppingItemResponse,
    )
    def complete_shopping_item(
        house_id: int,
        item_id: int,
        payload: ShoppingItemCompleteRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ShoppingItemResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 품목 상태를 변경할 수 있습니다.")
        item = db.set_shopping_item_completion(house_id, item_id, payload.is_completed)
        if item is None:
            raise HTTPException(status_code=404, detail="장바구니 품목을 찾을 수 없습니다.")
        return serialize_shopping_item(item)

    @application.delete(
        "/houses/{house_id}/shopping-items/completed",
        response_model=ShoppingItemsBulkDeleteResponse,
    )
    def delete_completed_shopping_items(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ShoppingItemsBulkDeleteResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 완료 품목을 정리할 수 있습니다.")
        return ShoppingItemsBulkDeleteResponse(
            deleted_count=db.delete_completed_shopping_items(house_id)
        )

    @application.delete(
        "/houses/{house_id}/shopping-items/{item_id}",
        response_model=ShoppingItemDeleteResponse,
    )
    def delete_completed_shopping_item(
        house_id: int,
        item_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> ShoppingItemDeleteResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 완료 품목을 삭제할 수 있습니다.")
        try:
            deleted_item_id = db.delete_completed_shopping_item(house_id, item_id)
        except ShoppingItemNotFoundError as error:
            raise HTTPException(status_code=404, detail="장바구니 품목을 찾을 수 없습니다.") from error
        except ShoppingItemNotCompletedError as error:
            raise HTTPException(status_code=409, detail="구매 완료된 품목만 삭제할 수 있습니다.") from error
        return ShoppingItemDeleteResponse(deleted_item_id=deleted_item_id)

    def serialize_settlement(row: dict[str, object]) -> SettlementResponse:
        creator = None
        if row["created_by"] is not None and row["creator_name"] is not None:
            creator = SettlementCreatorResponse(
                user_id=int(row["created_by"]), name=str(row["creator_name"])
            )
        raw_participants = row.get("participants", [])
        participants = [
            SettlementParticipantResponse(
                id=int(participant["id"]) if participant.get("id") is not None else None,
                user_id=int(participant["user_id"]),
                name=str(participant["name"]),
                amount=float(participant["amount"]),
                payment_status=str(participant["payment_status"]),
                display_name=str(participant["name"]),
                share_amount_cents=int(participant["share_amount_cents"]),
                role="payer" if participant.get("role") == "payer" else "participant",
                paid_at=str(participant["paid_at"]) if participant.get("paid_at") is not None else None,
                confirmed_by=int(participant["confirmed_by"]) if participant.get("confirmed_by") is not None else None,
            )
            for participant in raw_participants
            if isinstance(participant, dict)
        ]
        if row.get("payer_id") is not None and all(
            item.user_id != int(row["payer_id"]) for item in participants
        ):
            participants.append(SettlementParticipantResponse(
                user_id=int(row["payer_id"]),
                name=str(row.get("payer_name") or "결제자"),
                display_name=str(row.get("payer_name") or "결제자"),
                amount=0,
                share_amount_cents=0,
                role="payer",
                payment_status="payer",
            ))
        raw_status = str(row["status"])
        status_text = "completed" if raw_status in {"완료", "completed"} and row.get("payer_id") is not None else "in_progress"
        return SettlementResponse(
            settlement_id=int(row["id"]),
            house_id=int(row["house_id"]),
            title=str(row["title"]),
            total_amount=float(row["total_amount"]),
            total_amount_cents=int(row.get("total_amount_cents") or Decimal(str(row["total_amount"])) * 100),
            receipt_id=int(row["receipt_id"]) if row.get("receipt_id") is not None else None,
            payer_id=int(row["payer_id"]) if row.get("payer_id") is not None else None,
            payer_name=str(row["payer_name"]) if row.get("payer_name") is not None else None,
            uploaded_by=int(row["uploaded_by"]) if row.get("uploaded_by") is not None else None,
            uploaded_by_name=str(row["uploaded_by_name"]) if row.get("uploaded_by_name") is not None else None,
            receipt_date=str(row["receipt_date"]) if row.get("receipt_date") is not None else None,
            created_by=creator,
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]) if row.get("completed_at") is not None else None,
            status=status_text,
            is_completed=status_text == "completed",
            participants=participants,
        )

    @application.get(
        "/houses/{house_id}/settlements",
        response_model=list[SettlementResponse],
    )
    def list_settlements(
        house_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> list[SettlementResponse]:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 정산 내역을 조회할 수 있습니다.")
        return [serialize_settlement(row) for row in db.list_settlements(house_id)]

    @application.post(
        "/houses/{house_id}/settlements",
        response_model=SettlementResponse,
        status_code=201,
    )
    def create_settlement(
        house_id: int,
        payload: SettlementCreateRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> SettlementResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 정산을 생성할 수 있습니다.")
        if not payload.title:
            raise HTTPException(status_code=400, detail="정산 제목을 입력해주세요.")
        if payload.receipt_reconciliation_status in {"mismatch", "needs_review"}:
            raise HTTPException(
                status_code=409,
                detail="영수증 금액을 다시 확인하고 검증한 후 정산을 저장해주세요.",
            )
        participant_amount_cents: dict[int, int] | None = None
        receipt_items = payload.items if payload.items is not None else payload.receipt_items
        if receipt_items is not None:
            if payload.payer_id is None:
                raise HTTPException(status_code=400, detail="결제자를 선택해 주세요.")
            if payload.receipt_reconciliation_status not in {
                "verified", "verified_gst_included", "verified_gst_added"
            } or payload.receipt_verified_total is None:
                raise HTTPException(
                    status_code=409,
                    detail="검증된 영수증 금액이 있어야 정산을 저장할 수 있습니다.",
                )
            try:
                allocation_cents, shared_total_cents = allocate_shared_item_cents(
                    [item.model_dump() for item in receipt_items]
                )
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            request_total_cents = int(
                (Decimal(str(payload.total_amount)) * 100).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )
            if shared_total_cents != request_total_cents:
                raise HTTPException(
                    status_code=409,
                    detail="정산 총액이 공용 품목 합계와 일치하지 않습니다.",
                )
            if set(allocation_cents) != set(payload.participant_user_ids):
                raise HTTPException(
                    status_code=400,
                    detail="전체 정산 대상자가 품목별 참여자와 일치하지 않습니다.",
                )
            participant_amount_cents = allocation_cents
        request_total_cents = payload.total_amount_cents or int(
            (Decimal(str(payload.total_amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP)
        )
        receipt = db.get_receipt(house_id, payload.receipt_id) if payload.receipt_id else None
        if payload.receipt_id is not None and receipt is None:
            raise HTTPException(status_code=404, detail="영수증을 찾을 수 없습니다.")
        uploaded_by = int(receipt["uploaded_by"]) if receipt and receipt.get("uploaded_by") is not None else payload.uploaded_by
        if receipt and payload.uploaded_by is not None and uploaded_by != payload.uploaded_by:
            raise HTTPException(status_code=400, detail="영수증 등록자 정보가 일치하지 않습니다.")
        receipt_date = str(receipt["receipt_date"]) if receipt and receipt.get("receipt_date") else (payload.receipt_date.isoformat() if payload.receipt_date else None)
        logger.info(
            "Settlement create: house_id=%s total=%s reconciliation=%s shared_items=%s participants=%s",
            house_id,
            payload.total_amount,
            payload.receipt_reconciliation_status,
            len(receipt_items or []),
            payload.participant_user_ids,
        )
        logger.info(
            "Settlement identities: current_user_id=%s uploaded_by=%s payer_id=%s receipt_id=%s receipt_date=%s",
            user["id"], uploaded_by, payload.payer_id, payload.receipt_id, receipt_date,
        )
        try:
            settlement = db.create_settlement(
                house_id=house_id,
                title=payload.title,
                total_amount=round(payload.total_amount, 2),
                participant_user_ids=payload.participant_user_ids,
                participant_amount_cents=participant_amount_cents,
                payer_user_id=payload.payer_id,
                created_by=int(user["id"]),
                creator_name=str(user["display_name"]),
                receipt_id=payload.receipt_id,
                uploaded_by=uploaded_by,
                receipt_date=receipt_date,
                total_amount_cents=request_total_cents,
            )
        except InvalidSettlementParticipantsError as error:
            raise HTTPException(
                status_code=400,
                detail="정산 대상자는 모두 현재 하우스 멤버여야 합니다.",
            ) from error
        logger.info(
            "Settlement saved: settlement_id=%s payer_id=%s payer_name=%s uploaded_by=%s",
            settlement["id"], settlement.get("payer_id"), settlement.get("payer_name"), settlement.get("uploaded_by"),
        )
        return serialize_settlement(settlement)

    @application.patch(
        "/houses/{house_id}/settlements/{settlement_id}/participants/{participant_user_id}/payment-status",
        response_model=SettlementPaymentStatusResponse,
    )
    def update_settlement_payment_status(
        house_id: int,
        settlement_id: int,
        participant_user_id: int,
        payload: SettlementPaymentStatusRequest,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> SettlementPaymentStatusResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스에 접근할 권한이 없습니다.")
        try:
            result = db.update_settlement_payment_status(
                house_id, settlement_id, participant_user_id,
                payload.payment_status, int(user["id"]),
            )
        except SettlementNotFoundError as error:
            raise HTTPException(status_code=404, detail="정산 내역을 찾을 수 없습니다.") from error
        except SettlementPaymentForbiddenError as error:
            raise HTTPException(status_code=403, detail="결제자만 납부 상태를 변경할 수 있습니다.") from error
        except SettlementParticipantNotFoundError as error:
            raise HTTPException(status_code=404, detail="정산 참여자를 찾을 수 없습니다.") from error
        except PayerPaymentStatusError as error:
            raise HTTPException(status_code=409, detail="결제자의 상태는 변경할 수 없습니다.") from error
        return SettlementPaymentStatusResponse(**result)

    @application.delete(
        "/houses/{house_id}/settlements/{settlement_id}",
        response_model=SettlementDeleteResponse,
    )
    def delete_settlement(
        house_id: int,
        settlement_id: int,
        user: Annotated[dict[str, object], Depends(get_current_user)],
        db: Annotated[Database, Depends(get_database)],
    ) -> SettlementDeleteResponse:
        if not db.is_house_member(house_id, int(user["id"])):
            raise HTTPException(status_code=403, detail="이 하우스의 멤버만 정산을 삭제할 수 있습니다.")
        try:
            result = db.soft_delete_settlement(house_id, settlement_id, int(user["id"]))
        except SettlementNotFoundError as error:
            raise HTTPException(status_code=404, detail="정산 내역을 찾을 수 없습니다.") from error
        except SettlementAlreadyDeletedError as error:
            raise HTTPException(status_code=409, detail="이미 삭제된 정산 내역입니다.") from error
        except SettlementDeleteForbiddenError as error:
            raise HTTPException(
                status_code=403,
                detail="정산 생성자 또는 하우스 소유자만 삭제할 수 있습니다.",
            ) from error
        return SettlementDeleteResponse(**result)

    return application


app = create_app()
