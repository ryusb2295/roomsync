import { useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { ActionSheetModal } from '@/components/action-sheet-modal';
import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { CheckRow } from '@/components/check-row';
import { EmptyState } from '@/components/empty-state';
import { FeedbackBanner } from '@/components/feedback-banner';
import { HouseContextBanner } from '@/components/house-context-banner';
import { InlineError } from '@/components/inline-error';
import { ScreenContainer } from '@/components/screen-container';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { apiRequest } from '@/services/api';
import type { ShoppingItem, ShoppingItemDeleteResult, ShoppingItemsBulkDeleteResult } from '@/types/api';

type DeleteTarget = { kind: 'item'; item: ShoppingItem } | { kind: 'all' } | null;

export default function ShoppingScreen() {
  const colors = useRoomTheme();
  const { currentHouse, token } = useAuth();
  const [items, setItems] = useState<ShoppingItem[]>([]);
  const [newItem, setNewItem] = useState('');
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>(null);
  const [deleting, setDeleting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadItems = useCallback(async () => {
    if (!token || !currentHouse) {
      setItems([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    try {
      setItems(await apiRequest<ShoppingItem[]>(
        `/houses/${currentHouse.id}/shopping-items`, { token }
      ));
    } catch (error) {
      setItems([]);
      setLoadError(error instanceof Error ? error.message : '장바구니를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, [currentHouse, token]);

  useFocusEffect(useCallback(() => { void loadItems(); }, [loadItems]));

  const pendingItems = useMemo(() => items.filter((item) => !item.is_completed), [items]);
  const completedItems = useMemo(() => items.filter((item) => item.is_completed), [items]);

  const addItem = async () => {
    const itemName = newItem.trim();
    if (!itemName || !token || !currentHouse || adding) return;
    setAdding(true);
    setSuccess(null);
    setMutationError(null);
    try {
      await apiRequest<ShoppingItem>(`/houses/${currentHouse.id}/shopping-items`, {
        method: 'POST', token, body: { item_name: itemName },
      });
      setNewItem('');
      await loadItems();
      setSuccess('장바구니에 항목을 추가했습니다.');
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '품목을 추가하지 못했습니다.');
    } finally {
      setAdding(false);
    }
  };

  const toggleItem = async (item: ShoppingItem) => {
    if (!token || !currentHouse || updatingId !== null) return;
    setUpdatingId(item.id);
    setSuccess(null);
    setMutationError(null);
    try {
      await apiRequest<ShoppingItem>(
        `/houses/${currentHouse.id}/shopping-items/${item.id}/complete`,
        { method: 'PATCH', token, body: { is_completed: !item.is_completed } }
      );
      await loadItems();
      setSuccess(item.is_completed ? '구매 대기 상태로 되돌렸습니다.' : '구매 완료로 표시했습니다.');
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '구매 상태를 변경하지 못했습니다.');
    } finally {
      setUpdatingId(null);
    }
  };

  const openItemDelete = (item: ShoppingItem) => {
    setDeleteError(null);
    setDeleteTarget({ kind: 'item', item });
  };

  const openBulkDelete = () => {
    setDeleteError(null);
    setDeleteTarget({ kind: 'all' });
  };

  const closeDelete = () => {
    if (deleting) return;
    setDeleteTarget(null);
    setDeleteError(null);
  };

  const confirmDelete = async () => {
    if (!token || !currentHouse || !deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      if (deleteTarget.kind === 'item') {
        await apiRequest<ShoppingItemDeleteResult>(
          `/houses/${currentHouse.id}/shopping-items/${deleteTarget.item.id}`,
          { method: 'DELETE', token }
        );
      } else {
        await apiRequest<ShoppingItemsBulkDeleteResult>(
          `/houses/${currentHouse.id}/shopping-items/completed`,
          { method: 'DELETE', token }
        );
      }
      setDeleteTarget(null);
      await loadItems();
      setSuccess(deleteTarget.kind === 'all' ? '완료 항목을 모두 삭제했습니다.' : '완료 항목을 삭제했습니다.');
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : '완료 품목을 삭제하지 못했습니다.');
    } finally {
      setDeleting(false);
    }
  };

  const renderItem = (item: ShoppingItem, allowDelete: boolean) => (
    <View key={item.id} style={styles.itemRow}>
      <CheckRow checked={item.is_completed} detail={item.is_completed ? `${item.added_by} · 구매 완료` : `${item.added_by} · 구매 필요`} disabled={updatingId !== null || deleting} label={item.item_name} onToggle={() => toggleItem(item)} />
      {allowDelete ? (
        <View style={styles.actionRow}>
          <AppButton disabled={deleting || updatingId !== null} fullWidth={false} label="완료 취소" onPress={() => toggleItem(item)} size="compact" variant="tertiary" />
          <AppButton disabled={deleting || updatingId !== null} fullWidth={false} icon="delete-outline" label="삭제" onPress={() => openItemDelete(item)} size="compact" variant="danger" />
        </View>
      ) : null}
    </View>
  );

  const divider = <View style={[styles.divider, { backgroundColor: colors.divider }]} />;
  const isBulkDelete = deleteTarget?.kind === 'all';

  return (
    <ScreenContainer keyboardAware onRefresh={() => void loadItems()} refreshing={loading} subtitle={`${currentHouse?.name ?? '현재 하우스'}에서 함께 필요한 물품을 관리하세요.`} title="장바구니">
      <HouseContextBanner />
      {success ? <FeedbackBanner message={success} /> : null}
      <View style={styles.section}>
        <Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>새 품목 추가</Text>
        <AppTextField
          editable={!adding}
          label="공동 물품"
          maxLength={100}
          onChangeText={setNewItem}
          onSubmitEditing={addItem}
          placeholder="예: 주방 세제"
          returnKeyType="done"
          value={newItem}
        />
        <AppButton disabled={!newItem.trim()} icon="add" label="목록에 추가" loading={adding} onPress={addItem} variant="secondary" />
        {mutationError ? <InlineError message={mutationError} /> : null}
      </View>

      {loading ? <Text style={[styles.loading, { color: colors.textSecondary }]}>장바구니를 불러오는 중...</Text> : loadError ? (
        <View style={styles.feedback}><InlineError message={loadError} /><AppButton label="다시 시도" onPress={loadItems} variant="secondary" /></View>
      ) : items.length === 0 ? (
        <EmptyState description="필요한 공동 물품을 추가해보세요." icon="shopping-cart" title="장바구니가 비어 있어요" />
      ) : <>
        <View style={styles.section}>
          <View style={styles.heading}>
            <Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>구매 필요</Text>
            <Text style={[styles.count, { color: colors.textSecondary }]}>{pendingItems.length}개</Text>
          </View>
          {pendingItems.length === 0 ? <Text style={[styles.emptySection, { color: colors.textSecondary }]}>구매가 필요한 품목이 없습니다.</Text> : (
            <View style={[styles.list, { backgroundColor: colors.surface, borderColor: colors.border }]}>
              {pendingItems.map((item, index) => <View key={item.id}>{renderItem(item, false)}{index < pendingItems.length - 1 ? divider : null}</View>)}
            </View>
          )}
        </View>

        {completedItems.length > 0 ? <View style={styles.section}>
          <View style={styles.heading}>
            <View style={styles.headingCopy}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>구매 완료</Text><Text style={[styles.count, { color: colors.textSecondary }]}>{completedItems.length}개</Text></View>
            <AppButton disabled={deleting} fullWidth={false} icon="delete-sweep" label="완료 항목 정리" onPress={openBulkDelete} variant="danger" />
          </View>
          <View style={[styles.list, { backgroundColor: colors.surface, borderColor: colors.border }]}>
            {completedItems.map((item, index) => <View key={item.id}>{renderItem(item, true)}{index < completedItems.length - 1 ? divider : null}</View>)}
          </View>
        </View> : null}
      </>}

      <ActionSheetModal
        confirmLabel={isBulkDelete ? '완료 항목 모두 삭제' : '삭제'}
        danger
        description={isBulkDelete ? `완료된 품목 ${completedItems.length}개를 모두 삭제합니다. 구매가 필요한 품목은 유지됩니다.` : '구매 완료된 품목을 삭제할까요?'}
        disabled={deleting}
        error={deleteError}
        loading={deleting}
        onClose={closeDelete}
        onConfirm={confirmDelete}
        title={isBulkDelete ? `완료된 품목 ${completedItems.length}개를 모두 삭제할까요?` : '품목 삭제'}
        visible={deleteTarget !== null}>
        <Text style={[styles.confirmHint, { color: colors.textSecondary }]}>삭제한 품목은 앱을 다시 실행해도 복구되지 않습니다.</Text>
      </ActionSheetModal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.item },
  heading: { alignItems: 'center', flexDirection: 'row', gap: Spacing.item, justifyContent: 'space-between' },
  headingCopy: { alignItems: 'baseline', flexDirection: 'row', gap: Spacing.compact },
  count: { fontSize: 12 },
  list: { borderRadius: Radius.card, borderWidth: 1, paddingHorizontal: Spacing.item },
  itemRow: { paddingVertical: Spacing.xs },
  actionRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact, justifyContent: 'flex-end', paddingBottom: Spacing.compact },
  divider: { height: StyleSheet.hairlineWidth, marginLeft: 37 },
  loading: { fontSize: 14, paddingVertical: Spacing.section, textAlign: 'center' },
  feedback: { gap: Spacing.item },
  emptySection: { fontSize: 14, paddingVertical: Spacing.item },
  confirmHint: { fontSize: 13, lineHeight: 19 },
});
