import { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { ActionSheetModal } from '@/components/action-sheet-modal';
import { CheckRow } from '@/components/check-row';
import { ChoreFormModal } from '@/components/chore-form-modal';
import { EmptyState } from '@/components/empty-state';
import { FeedbackBanner } from '@/components/feedback-banner';
import { HouseContextBanner } from '@/components/house-context-banner';
import { InlineError } from '@/components/inline-error';
import { LoadingOverlay } from '@/components/loading-overlay';
import { ScreenContainer } from '@/components/screen-container';
import { StatusBadge } from '@/components/status-badge';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { apiRequest } from '@/services/api';
import type { Chore } from '@/types/api';
import { formatKoreanDate } from '@/utils/date';

function localDateKey(date = new Date()): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function normalizeDate(value: unknown): string {
  return typeof value === 'string' ? value.slice(0, 10) : '';
}

function sortChores(items: Chore[]): Chore[] {
  return [...items].sort((left, right) =>
    normalizeDate(left.scheduled_date).localeCompare(normalizeDate(right.scheduled_date)) || left.id - right.id
  );
}

export default function CleaningScreen() {
  const colors = useRoomTheme();
  const {
    currentHouse,
    token,
    user,
    houseMembers,
    membersLoading,
    membersError,
    refreshHouseMembers,
  } = useAuth();
  const [chores, setChores] = useState<Chore[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Chore | null>(null);
  const [formVisible, setFormVisible] = useState(false);
  const [editingChore, setEditingChore] = useState<Chore | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadChores = useCallback(async () => {
    if (!token || !currentHouse) return;
    setLoading(true);
    setLoadError(null);
    try {
      const response = await apiRequest<Chore[] | { chores?: Chore[] }>(`/houses/${currentHouse.id}/chores`, { token });
      const choreList = Array.isArray(response) ? response : Array.isArray(response?.chores) ? response.chores : [];
      if (__DEV__) console.log('[cleaning] chores response:', { response, normalizedCount: choreList.length });
      if (!Array.isArray(response) && !Array.isArray(response?.chores) && __DEV__) console.warn('[cleaning] invalid chores response:', response);
      setChores(sortChores(choreList));
    } catch (error) {
      setChores([]);
      setLoadError(error instanceof Error ? error.message : '청소 일정을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, [currentHouse, token]);

  useEffect(() => { loadChores(); }, [loadChores]);

  const openCreateForm = () => {
    setFormError(null);
    setEditingChore(null);
    if (__DEV__) console.log('[cleaning] open create form:', { houseId: currentHouse?.id, members: houseMembers, membersLoading, currentUserId: user?.id });
    setFormVisible(true);
    if (houseMembers.length === 0 && !membersLoading) void refreshHouseMembers();
  };

  const openEditForm = (chore: Chore) => {
    setFormError(null);
    setEditingChore(chore);
    if (__DEV__) console.log('[cleaning] open edit form:', { chore, members: houseMembers });
    setFormVisible(true);
  };

  const saveChore = async (draft: { title: string; description: string; assigneeUserId: number; scheduledDate: string }) => {
    if (!token || !user || !currentHouse) { setFormError('하우스 정보를 불러오지 못했습니다.'); return; }
    if (!draft.title.trim()) { setFormError('청소 제목을 입력해 주세요.'); return; }
    if (!draft.assigneeUserId || !houseMembers.some((member) => member.user_id === draft.assigneeUserId)) { setFormError('담당자를 선택해 주세요.'); return; }
    if (!normalizeDate(draft.scheduledDate)) { setFormError('청소 예정일을 선택해 주세요.'); return; }
    if (editingChore) {
      if (saving) return;
      setSaving(true);
      setFormError(null);
      try {
        const updated = await apiRequest<Chore>(`/houses/${currentHouse.id}/chores/${editingChore.id}`, {
          method: 'PATCH', token, body: { title: draft.title, description: draft.description, assignee_user_id: draft.assigneeUserId, scheduled_date: draft.scheduledDate },
        });
        setChores((current) => sortChores(current.map((item) => item.id === updated.id ? updated : item)));
        setFormVisible(false);
        setEditingChore(null);
        setSuccess('청소 일정을 수정했습니다.');
      } catch (error) {
        setFormError(error instanceof Error ? error.message : '청소 일정을 수정하지 못했습니다.');
      } finally {
        setSaving(false);
      }
      return;
    }
    await createChore(draft);
  };

  const createChore = async (draft: { title: string; description: string; assigneeUserId: number; scheduledDate: string }) => {
    if (saving) return;
    if (!token || !user || !currentHouse) { setFormError('하우스 정보를 불러오지 못했습니다.'); return; }
    if (!draft.title.trim()) { setFormError('청소 제목을 입력해 주세요.'); return; }
    if (!draft.assigneeUserId || !houseMembers.some((member) => member.user_id === draft.assigneeUserId)) { setFormError('담당자를 선택해 주세요.'); return; }
    if (!normalizeDate(draft.scheduledDate)) { setFormError('청소 예정일을 선택해 주세요.'); return; }
    setSaving(true);
    setFormError(null);
    try {
      const created = await apiRequest<Chore>(`/houses/${currentHouse.id}/chores`, {
        method: 'POST',
        token,
        body: {
          title: draft.title,
          description: draft.description,
          assignee_user_id: draft.assigneeUserId,
          scheduled_date: draft.scheduledDate,
        },
      });
      setChores((current) => sortChores([...current, created]));
      setFormVisible(false);
      setSuccess('청소 일정을 등록했습니다.');
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '청소 일정을 등록하지 못했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const toggleChore = async (chore: Chore) => {
    if (!token || !currentHouse || updatingId !== null) return;
    setUpdatingId(chore.id);
    setActionError(null);
    try {
      const updated = await apiRequest<Chore>(
        `/houses/${currentHouse.id}/chores/${chore.id}/complete`,
        { method: 'PATCH', token, body: { is_completed: !chore.is_completed } }
      );
      setChores((current) => current.map((item) => item.id === updated.id ? updated : item));
      setSuccess(updated.is_completed ? '청소 완료로 표시했습니다.' : '청소 완료를 취소했습니다.');
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '청소 완료 상태를 변경하지 못했습니다.');
    } finally {
      setUpdatingId(null);
    }
  };

  const deleteChore = async () => {
    const chore = deleteTarget;
    if (!chore) return;
    if (!token || !currentHouse || deletingId !== null) return;
    setDeletingId(chore.id);
    setActionError(null);
    try {
      await apiRequest<null>(`/houses/${currentHouse.id}/chores/${chore.id}`, { method: 'DELETE', token });
      setChores((current) => current.filter((item) => item.id !== chore.id));
      setDeleteTarget(null);
      setSuccess('청소 일정을 삭제했습니다.');
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '청소 일정을 삭제하지 못했습니다.');
    } finally {
      setDeletingId(null);
    }
  };

  const upcomingChores = useMemo(() => chores.filter((chore) => !chore.is_completed), [chores]);
  const completedChores = useMemo(() => chores.filter((chore) => chore.is_completed), [chores]);
  const todayAssignees = [...new Set(
    chores
      .filter((chore) => normalizeDate(chore.scheduled_date) === localDateKey())
      .map((chore) => chore.assignee?.display_name)
      .filter((name): name is string => typeof name === 'string' && name.length > 0)
  )];

  const renderChore = (chore: Chore) => (
    <View key={chore.id} style={[styles.chore, chore.is_completed && { backgroundColor: colors.background }]}>
      <CheckRow
        checked={chore.is_completed}
        detail={`${chore.assignee.display_name} · ${formatKoreanDate(chore.scheduled_date)}`}
        disabled={updatingId !== null || deletingId !== null}
        label={chore.title}
        onToggle={() => toggleChore(chore)}
      />
      {chore.description ? <Text style={[styles.description, { color: colors.textSecondary }]}>{chore.description}</Text> : null}
      <View style={styles.choreFooter}>
        <StatusBadge label={chore.is_completed ? '완료' : '예정'} tone={chore.is_completed ? 'completed' : 'pending'} />
        <View style={styles.choreActions}><AppButton disabled={deletingId !== null || updatingId !== null} fullWidth={false} label="수정" onPress={() => openEditForm(chore)} variant="tertiary" /><AppButton disabled={deletingId !== null || updatingId !== null} fullWidth={false} label={deletingId === chore.id ? '삭제 중' : '삭제'} onPress={() => setDeleteTarget(chore)} variant="danger" /></View>
      </View>
    </View>
  );

  return (
    <ScreenContainer
      headerAction={<AppButton disabled={!currentHouse || membersLoading} fullWidth={false} icon="add" label="일정 추가" onPress={openCreateForm} />}
      onRefresh={() => void loadChores()}
      refreshing={loading}
      subtitle={`${currentHouse?.name ?? '현재 하우스'}의 실제 청소 일정입니다.`}
      title="청소">
      <HouseContextBanner />
      {success ? <FeedbackBanner message={success} /> : null}
      {loadError ? <View style={styles.feedback}><InlineError message={loadError} /><AppButton label="다시 시도" onPress={loadChores} variant="secondary" /></View> : null}
      {actionError ? <InlineError message={actionError} /> : null}

      {!loading && !loadError && todayAssignees.length > 0 ? (
        <View style={[styles.assignee, { backgroundColor: colors.primarySoft }]}>
          <View style={[styles.avatar, { backgroundColor: colors.primary }]}><Text style={styles.avatarText}>{todayAssignees[0]?.slice(0, 1)}</Text></View>
          <View style={styles.copy}><Text style={[styles.label, { color: colors.primary }]}>오늘의 담당자</Text><Text style={[styles.name, { color: colors.textPrimary }]}>{todayAssignees.join(', ')}</Text><Text style={[styles.meta, { color: colors.textSecondary }]}>{currentHouse?.name} 구성원</Text></View>
          <StatusBadge label="오늘" tone="info" />
        </View>
      ) : null}

      {!loading && !loadError && chores.length === 0 ? (
        <EmptyState actionLabel="첫 일정 등록하기" description="현재 하우스에 저장된 청소 일정이 없습니다." icon="cleaning-services" onAction={() => openCreateForm()} title="등록된 청소 일정이 없어요" />
      ) : null}

      {!loading && !loadError && upcomingChores.length > 0 ? (
        <View style={styles.section}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>예정 일정</Text><View style={[styles.list, { backgroundColor: colors.surface, borderColor: colors.border }]}>{upcomingChores.map(renderChore)}</View></View>
      ) : null}
      {!loading && !loadError && completedChores.length > 0 ? (
        <View style={styles.section}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>완료 일정</Text><View style={[styles.list, { backgroundColor: colors.surface, borderColor: colors.border }]}>{completedChores.map(renderChore)}</View></View>
      ) : null}

      <ChoreFormModal
        error={formError ?? membersError}
        initialAssigneeUserId={user?.id}
        initialDraft={editingChore ? { title: editingChore.title ?? '', description: editingChore.description ?? '', assigneeUserId: editingChore.assignee_user_id, scheduledDate: normalizeDate(editingChore.scheduled_date) } : undefined}
        loading={saving || membersLoading}
        members={houseMembers}
        onClose={() => { if (!saving) { setFormVisible(false); setEditingChore(null); } }}
        onSubmit={saveChore}
        visible={formVisible}
      />
      <ActionSheetModal confirmLabel="삭제" danger description="삭제한 청소 일정은 복구할 수 없습니다." disabled={deletingId !== null} error={actionError} loading={deletingId !== null} onClose={() => { if (deletingId === null) setDeleteTarget(null); }} onConfirm={deleteChore} title="청소 일정을 삭제할까요?" visible={deleteTarget !== null}>
        <Text style={[styles.description, { color: colors.textSecondary }]}>{deleteTarget?.title}</Text>
      </ActionSheetModal>
      <LoadingOverlay label="청소 일정을 불러오는 중" visible={loading} />
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  feedback: { gap: Spacing.item }, assignee: { alignItems: 'center', borderRadius: Radius.card, flexDirection: 'row', gap: Spacing.md, padding: Spacing.item },
  avatar: { alignItems: 'center', borderRadius: Radius.pill, height: 48, justifyContent: 'center', width: 48 }, avatarText: { color: '#FFFFFF', fontSize: 17, fontWeight: '800' },
  copy: { flex: 1, gap: 2 }, label: { fontSize: 11, fontWeight: '800' }, name: { fontSize: 18, fontWeight: '800' }, meta: { fontSize: 12 },
  section: { gap: Spacing.item }, list: { borderRadius: Radius.card, borderWidth: 1, overflow: 'hidden' },
  chore: { borderBottomWidth: StyleSheet.hairlineWidth, borderColor: '#EEF0F3', paddingHorizontal: Spacing.item, paddingBottom: Spacing.md },
  description: { fontSize: 13, lineHeight: 19, marginBottom: Spacing.compact, marginLeft: 37 }, choreFooter: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginLeft: 37 }, choreActions: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact },
});
