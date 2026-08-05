from pathlib import Path


def test_general_and_receipt_timeouts_are_separate() -> None:
    root = Path(__file__).resolve().parents[2]
    api_source = (root / "frontend/services/api.ts").read_text(encoding="utf-8")
    settlement_source = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    assert "timeoutMs = 15000" in api_source
    assert "timeoutMs: 90000" in settlement_source


def test_receipt_analysis_prevents_duplicate_submission() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    assert "if (analyzing) return" in source
    assert "disabled={analyzing}" in source


def test_discount_lines_are_not_rejected_or_given_manual_participants() -> None:
    root = Path(__file__).resolve().parents[2]
    utility = (root / "frontend/utils/receipt-settlement.ts").read_text(encoding="utf-8")
    editor = (root / "frontend/components/receipt-analysis-editor.tsx").read_text(encoding="utf-8")
    assert "validAdjustment" in utility
    assert "participant_user_ids: participants" in utility
    assert "전체 공용 품목에 자동 적용" in editor
    assert "const isAdjustment" in editor


def test_settlement_payment_status_uses_backend_patch_and_payer_permission() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    assert "/payment-status`" in source
    assert "user?.id === settlement.payer_id" in source
    assert "result.settlement_status === 'completed'" in source


def test_settlement_status_pills_have_distinct_accessible_palettes() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    for color in ("#EAF2FF", "#2563EB", "#FFF4E5", "#D97706", "#EAF8EE", "#16A34A"):
        assert color in source
    for icon in ("account-balance-wallet", "schedule", "check-circle"):
        assert icon in source
    assert 'accessibilityRole="button"' in source
    assert "borderRadius: 999" in source


def test_production_screens_do_not_switch_to_demo_settlements() -> None:
    root = Path(__file__).resolve().parents[2]
    home = (root / "frontend/app/(tabs)/index.tsx").read_text(encoding="utf-8")
    settlement = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    assert "createDemoSettlements" not in home
    assert "DemoSettlementScreen" not in settlement
    assert "EXPO_PUBLIC_ROOMSYNC_DEMO_MODE" not in home + settlement


def test_dashboard_uses_persisted_cents_and_future_incomplete_chore() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/app/(tabs)/index.tsx").read_text(encoding="utf-8")
    assert "share_amount_cents" in source
    assert "participant.payment_status === 'unpaid'" in source
    assert "item.payer_id !== user?.id" in source
    assert "participant.user_id !== user?.id" in source
    assert "!chore.is_completed && chore.scheduled_date.slice(0, 10) >= today" in source


def test_receipt_ui_uses_validation_score_not_model_self_rating() -> None:
    root = Path(__file__).resolve().parents[2]
    editor = (root / "frontend/components/receipt-analysis-editor.tsx").read_text(encoding="utf-8")
    types = (root / "frontend/types/api.ts").read_text(encoding="utf-8")
    assert "분석 신뢰도 {validationScore}%" in editor
    assert "AI 인식 신뢰도" not in editor
    assert "품목 합계 검증" in editor
    assert "GST 및 할인 검증" in editor
    assert "최종 결제금액 검증" in editor
    assert "model_reported_confidence?: number | null" in types
    assert "validation_score: number" in types


def test_user_facing_money_uses_shared_aud_formatter() -> None:
    root = Path(__file__).resolve().parents[2]
    money = (root / "frontend/utils/money.ts").read_text(encoding="utf-8")
    home = (root / "frontend/app/(tabs)/index.tsx").read_text(encoding="utf-8")
    settlement = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    editor = (root / "frontend/components/receipt-analysis-editor.tsx").read_text(encoding="utf-8")
    assert "`AUD ${value.toFixed(2)}`" in money
    assert "formatAudFromCents(amountToPayCents)" in home
    assert "formatAudFromCents(amountToReceiveCents)" in home
    assert "return formatAud(amount)" in settlement
    assert "formatAud(analysis.verified_total)" in editor
    assert "AI 인식 신뢰도" not in editor


def test_final_ui_has_payment_hint_and_safe_danger_confirmations() -> None:
    root = Path(__file__).resolve().parents[2]
    settlement = (root / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    cleaning = (root / "frontend/app/(tabs)/cleaning.tsx").read_text(encoding="utf-8")
    shopping = (root / "frontend/app/(tabs)/shopping.tsx").read_text(encoding="utf-8")
    settings = (root / "frontend/app/(tabs)/settings.tsx").read_text(encoding="utf-8")
    check_row = (root / "frontend/components/check-row.tsx").read_text(encoding="utf-8")
    assert "결제자는 미납 상태를 눌러 납부 완료로 변경할 수 있습니다." in settlement
    assert 'title="정산을 삭제하시겠습니까?"' in settlement
    assert "삭제된 정산과 납부 기록은 복구할 수 없습니다." in settlement
    assert "onConfirm={deleteChore}" in cleaning
    assert "onConfirm={confirmDelete}" in shopping
    assert "visible={action === 'transfer'}" in settings
    assert "visible={action === 'delete-house'}" in settings
    assert "visible={action === 'delete-account'}" in settings
    assert "한국어 · 향후 지원 예정" in settings
    assert "stateBadge" in check_row
