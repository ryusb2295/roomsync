# 백엔드 테스트 결과

## 테스트 날짜

2026년 7월 20일

## 테스트 환경

- 브랜치: `feature/backend-integration`
- 운영체제: Windows
- Python: 3.13.9, `backend/.venv` 사용
- 데이터베이스: 테스트마다 만든 임시 SQLite 파일
- 실행 명령: `python -m pytest backend/tests -q`
- 외부 AI 호출: 자동 테스트에서는 실행하지 않음

## 테스트한 기능과 결과

| 기능 | 결과 |
|---|---|
| `/health` 응답 | 성공 |
| 회원가입, 로그인, 로그아웃, 인증 실패 | 성공 |
| 하우스 생성, 목록, 초대코드 참여 | 성공 |
| 하우스 멤버 조회, 나가기, 소유권 이전, 삭제 | 성공 |
| 계정 삭제 | 성공 |
| 청소 일정 CRUD | 성공 |
| 공동 장바구니 CRUD | 성공 |
| 정산 생성, 조회, 삭제 | 성공 |
| SQLite 테이블 생성과 데이터 유지 | 성공 |
| 영수증 이미지 형식, 크기, 권한 검사 | 성공 |
| Gemini 필드 별칭 정규화 | 성공 |
| 마크다운 JSON 파싱 | 성공 |
| 통화 기호와 쉼표가 있는 숫자 변환 | 성공 |
| 누락된 amount, subtotal, tax, confidence 처리 | 성공 |
| 잘못된 Gemini JSON의 422 처리 | 성공 |
| 외부 호출 없는 Gemini 주입 테스트 | 성공 |

최종 결과: 48개 성공, 0개 실패

## 이번 422 오류의 원인

기존 코드는 Gemini 결과를 바로 `ParsedReceipt` 모델로 검사했다. 이 모델은 `merchant_name`, `receipt_date`, 숫자형 금액, 각 품목의 `amount`, `total`, `confidence`를 정해진 이름과 형식으로 요구한다.

Gemini가 `merchant`, `purchase_date`, `price` 같은 다른 필드명을 사용하거나 `$4.50` 같은 문자열 금액을 반환하면 Pydantic 검사가 실패했다. 품목의 `amount`, 세금 또는 confidence가 빠진 경우도 같은 422 응답이 발생했다.

## 수정한 내용

- Gemini의 원시 응답을 받는 유연한 모델과 프론트 응답 정규화 단계를 분리했다.
- `merchant`, `merchant_name`, `store_name`을 같은 상점 이름으로 처리한다.
- `purchase_date`, `date`, `receipt_date`를 `receipt_date`로 처리한다.
- `price`와 `unit_price` 중 있는 값을 단가로 사용한다.
- `amount`가 없으면 수량과 단가를 곱한다.
- `subtotal`이 없으면 품목 금액 합계를 사용한다.
- `tax`가 없으면 0으로 처리하고 warnings에 이유를 남긴다.
- `confidence`가 없으면 과장되지 않은 기본값 0.5를 사용한다.
- AUD, 달러 기호, 쉼표가 포함된 금액을 숫자로 바꾼다.
- 가능한 날짜 형식을 `YYYY-MM-DD`로 바꾼다.
- JSON 코드 블록도 파싱한다.
- Gemini 호출에 `application/json` 응답 형식을 요청한다. 필드 별칭과 문자열 금액은 백엔드에서 정규화한다.
- Gemini 호출에 단순한 정식 응답 스키마를 다시 지정해 `items` 배열을 우선 반환하도록 했다.
- `receipt`, `receipt_data`, `data`, `result` 아래에 중첩된 응답과 `purchased_items`, `line_items`, `products`, `entries` 품목 이름도 처리한다.
- 품목이 없을 때 영수증 값은 남기지 않고 JSON 키 구조만 로그에 기록한다.
- 잘못된 응답의 원인은 로그에 남기지만 기본 로그에는 전체 영수증을 남기지 않는다.
- `ROOMSYNC_RECEIPT_DEBUG=true`일 때만 개발 환경에서 원문 응답을 DEBUG 로그로 확인할 수 있다.

## 발생한 오류

- 첫 백엔드 점검 때 시스템 Python에 `fastapi`가 없어 테스트 수집이 중단됐다. `backend/.venv`에 requirements를 설치해 해결했다.
- 실제 Gemini 재호출은 영수증 이미지와 로컬 API 키를 외부 서비스로 전송하는 작업이라 현재 실행 환경의 보안 정책에서 차단됐다. 과거 실패 요청의 원문은 서버 로그에 저장되지 않아 다시 가져올 수 없었다.

## 아직 남아 있는 문제

- 실제 Coles 영수증은 개발 서버에서 `ROOMSYNC_RECEIPT_DEBUG=true`로 한 번 확인한 뒤 옵션을 다시 꺼야 한다.
- 기존 코드 일부의 한국어 오류 메시지에 문자 인코딩 문제가 남아 있다.
- 정산 수정과 결제 상태 변경 API는 현재 없다.
