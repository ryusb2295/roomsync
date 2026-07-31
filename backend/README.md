# RoomSync 백엔드

## 담당 범위

FastAPI 서버, SQLite 데이터베이스, 로그인 세션, 영수증 분석, 백엔드 테스트

## 폴더 구조

```text
backend/
├─ app/
│  ├─ main.py              FastAPI 앱, API 경로
│  ├─ database.py          SQLite 테이블 생성, CRUD
│  ├─ schemas.py           요청 및 응답 모델
│  ├─ security.py          비밀번호 및 토큰 처리
│  ├─ config.py            환경변수 설정
│  └─ receipt_analyzer.py  Gemini, OpenAI, mock 영수증 분석
├─ scripts/
│  └─ prepare_database.py  기존 DB 복사와 초기화
├─ tests/                  pytest API 및 DB 테스트
├─ .env.example            환경변수 예시
├─ requirements.txt        Python 패키지 목록
└─ TEST_RESULT.md          테스트 결과
```

`backend/data`, `backend/backups`의 DB 파일은 실행 중 로컬에 만들어질 수 있으며 Git에 올리지 않는다.

## 개발 환경 준비

프로젝트 최상위 폴더에서 다음 명령을 실행한다.

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/Activate.ps1
python -m pip install -r backend/requirements.txt
```

macOS 또는 Linux에서는 활성화 명령이 다음과 같다.

```bash
source backend/.venv/bin/activate
```

## 환경변수 설정

`backend/.env.example`을 참고해 `backend/.env`를 만든다. `.env`는 Git에 올리지 않는다.

Gemini를 사용할 때 필요한 기본 설정은 다음과 같다.

```env
ROOMSYNC_RECEIPT_PROVIDER=gemini
GEMINI_API_KEY=발급받은_API_키
ROOMSYNC_GEMINI_MODEL=gemini-3.1-flash-lite
```

주요 환경변수는 다음과 같다.

- `ROOMSYNC_DB_PATH`: SQLite 파일 위치. 기본값은 `backend/data/roomsync.db`
- `ROOMSYNC_CORS_ORIGINS`: 허용할 프론트엔드 주소를 쉼표로 구분
- `ROOMSYNC_RECEIPT_PROVIDER`: `gemini`, `openai`, `mock` 중 하나
- `GEMINI_API_KEY`: Gemini 사용 시 필요
- `ROOMSYNC_GEMINI_MODEL`: Gemini 모델 이름
- `OPENAI_API_KEY`: OpenAI 사용 시 필요
- `ROOMSYNC_RECEIPT_MODEL`: OpenAI 모델 이름
- `ROOMSYNC_RECEIPT_MOCK_ENABLED`: mock 사용 시 `true`로 설정
- `ROOMSYNC_RECEIPT_DEBUG`: 개발 중 Gemini 원문 응답 로그가 필요할 때만 `true`로 설정
- `ROOMSYNC_BACKUP_DIR`: 마이그레이션 전 DB 백업 위치

mock은 개발용 고정 결과이다. 실제 영수증 분석을 확인하려면 Gemini 또는 OpenAI 제공자와 해당 API 키를 설정해야 한다.

`ROOMSYNC_RECEIPT_DEBUG`의 기본값은 `false`이다. `true`로 설정하면 영수증 구매 정보가 로그에 남을 수 있으므로 개발 환경에서 확인 후 바로 끈다. API 키는 로그에 출력하지 않는다.

## 서버 실행

프로젝트 최상위 폴더에서 실행한다.

```powershell
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
```

서버 상태는 `http://localhost:8001/health`에서 확인한다. Swagger는 `http://localhost:8001/docs`, OpenAPI 문서는 `http://localhost:8001/openapi.json`에서 확인할 수 있다.

인증이 필요한 API는 회원가입 또는 로그인 응답의 `access_token`을 사용한다.

```http
Authorization: Bearer access_token
```

## 테스트 실행

```powershell
python -m pytest backend/tests -q
```

테스트는 임시 SQLite 파일과 mock 또는 주입한 Gemini 응답을 사용한다. 실제 API 키와 외부 API 호출은 필요하지 않다.

## 주요 API

| 기능 | 메서드와 경로 |
|---|---|
| 서버 상태 | `GET /health` |
| 인증 | `POST /auth/signup`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`, `DELETE /auth/me` |
| 하우스 | `GET /houses`, `POST /houses`, `POST /houses/join`, `GET /houses/{house_id}`, `DELETE /houses/{house_id}` |
| 멤버 관리 | `GET /houses/{house_id}/members`, `POST /houses/{house_id}/leave`, `PATCH /houses/{house_id}/owner` |
| 삭제 영향 확인 | `GET /houses/{house_id}/deletion-impact` |
| 영수증 분석 | `POST /houses/{house_id}/receipts/analyze` |
| 청소 일정 | `GET/POST /houses/{house_id}/chores`, `PATCH/DELETE /houses/{house_id}/chores/{chore_id}`, `PATCH /houses/{house_id}/chores/{chore_id}/complete` |
| 공동 장바구니 | `GET/POST /houses/{house_id}/shopping-items`, `PATCH /houses/{house_id}/shopping-items/{item_id}/complete`, `DELETE /houses/{house_id}/shopping-items/{item_id}`, `DELETE /houses/{house_id}/shopping-items/completed` |
| 정산 | `GET/POST /houses/{house_id}/settlements`, `DELETE /houses/{house_id}/settlements/{settlement_id}` |
