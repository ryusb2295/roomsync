from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLEANING = ROOT / "frontend/app/(tabs)/cleaning.tsx"
MODAL = ROOT / "frontend/components/chore-form-modal.tsx"


def test_create_action_does_not_pass_press_event_as_chore() -> None:
    source = CLEANING.read_text(encoding="utf-8")
    assert "const openCreateForm = () =>" in source
    assert "onAction={() => openCreateForm()}" in source
    assert "onAction={openForm}" not in source


def test_undefined_dates_and_members_are_normalized_before_render() -> None:
    cleaning = CLEANING.read_text(encoding="utf-8")
    modal = MODAL.read_text(encoding="utf-8")
    assert "typeof value === 'string' ? value.slice(0, 10) : ''" in cleaning
    assert "Array.isArray(members) ? members : []" in modal
    assert "members[0]?.user_id" not in modal


def test_form_initial_values_and_loading_guard_are_present() -> None:
    source = MODAL.read_text(encoding="utf-8")
    assert "useState('')" in source
    assert "useState<number | null>(null)" in source
    assert "하우스 멤버를 불러오는 중" in source


def test_chore_delete_requires_explicit_confirmation() -> None:
    source = CLEANING.read_text(encoding="utf-8")
    assert "setDeleteTarget(chore)" in source
    assert 'title="청소 일정을 삭제할까요?"' in source
    assert "onConfirm={deleteChore}" in source


def test_user_facing_dates_use_shared_korean_formatter() -> None:
    date_utility = (ROOT / "frontend/utils/date.ts").read_text(encoding="utf-8")
    home = (ROOT / "frontend/app/(tabs)/index.tsx").read_text(encoding="utf-8")
    settlement = (ROOT / "frontend/app/(tabs)/settlement.tsx").read_text(encoding="utf-8")
    editor = (ROOT / "frontend/components/receipt-analysis-editor.tsx").read_text(encoding="utf-8")
    assert "`${year}년 ${month}월 ${day}일`" in date_utility
    assert "formatKoreanDate(nextChore.scheduled_date)" in home
    assert "formatKoreanDate(settlement.receipt_date)" in settlement
    assert "formatKoreanDate(settlement.created_at)" in settlement
    assert "formatKoreanDate(analysis.receipt_date, '확인 불가')" in editor
