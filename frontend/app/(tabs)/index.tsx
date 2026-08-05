import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { HouseContextBanner } from '@/components/house-context-banner';
import { ListRow } from '@/components/list-row';
import { ScreenContainer } from '@/components/screen-container';
import { ErrorState } from '@/components/state-view';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { apiRequest } from '@/services/api';
import type { Chore, Settlement, ShoppingItem } from '@/types/api';
import { formatKoreanDate } from '@/utils/date';
import { formatAudFromCents } from '@/utils/money';

const localDateKey = (date = new Date()) => {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
};

export default function HomeScreen() {
  const router = useRouter();
  const colors = useRoomTheme();
  const { currentHouse, user, token, refreshHouseMembers } = useAuth();
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [chores, setChores] = useState<Chore[]>([]);
  const [shopping, setShopping] = useState<ShoppingItem[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    if (!currentHouse || !token) return;
    setRefreshing(true);
    setError(null);
    try {
      const [choreRows, shoppingRows, settlementRows] = await Promise.all([
        apiRequest<Chore[]>(`/houses/${currentHouse.id}/chores`, { token }),
        apiRequest<ShoppingItem[]>(`/houses/${currentHouse.id}/shopping-items`, { token }),
        apiRequest<Settlement[]>(`/houses/${currentHouse.id}/settlements`, { token }),
        refreshHouseMembers(),
      ]);
      setChores(choreRows);
      setShopping(shoppingRows);
      setSettlements(settlementRows);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '홈 요약을 불러오지 못했습니다.');
    } finally {
      setRefreshing(false);
    }
  }, [currentHouse, refreshHouseMembers, token]);

  useFocusEffect(useCallback(() => { void loadSummary(); }, [loadSummary]));

  const activeRealSettlements = settlements.filter((item) => !item.is_completed);
  const activeCount = activeRealSettlements.length;
  const amountToPayCents = activeRealSettlements
    .filter((item) => item.payer_id !== user?.id)
    .reduce((sum, item) => sum + (item.participants.find((participant) => participant.user_id === user?.id && participant.payment_status === 'unpaid')?.share_amount_cents ?? 0), 0);
  const amountToReceiveCents = activeRealSettlements
    .filter((item) => item.payer_id === user?.id)
    .reduce((sum, item) => sum + item.participants
      .filter((participant) => participant.user_id !== user?.id && participant.payment_status === 'unpaid')
      .reduce((subtotal, participant) => subtotal + participant.share_amount_cents, 0), 0);
  const today = localDateKey();
  const nextChore = [...chores].filter((chore) => !chore.is_completed && chore.scheduled_date.slice(0, 10) >= today).sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date))[0];
  const pendingShopping = shopping.filter((item) => !item.is_completed).length;

  return <ScreenContainer onRefresh={() => void loadSummary()} refreshing={refreshing} subtitle={`${user?.display_name ?? '사용자'}님의 오늘 할 일을 확인하세요.`} title="홈">
    <HouseContextBanner />
    {error ? <ErrorState message={error} onRetry={() => void loadSummary()} /> : null}
    <View style={styles.summaryGrid}>
      <Pressable accessibilityRole="button" onPress={() => router.push('/settlement')} style={[styles.summaryCard, { backgroundColor: colors.surface, borderColor: colors.border }]}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>내가 낼 금액</Text><Text style={[styles.summaryValue, { color: colors.danger }]}>{formatAudFromCents(amountToPayCents)}</Text></Pressable>
      <Pressable accessibilityRole="button" onPress={() => router.push('/settlement')} style={[styles.summaryCard, { backgroundColor: colors.surface, borderColor: colors.border }]}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>내가 받을 금액</Text><Text style={[styles.summaryValue, { color: colors.success }]}>{formatAudFromCents(amountToReceiveCents)}</Text></Pressable>
      <Pressable accessibilityRole="button" onPress={() => router.push('/settlement')} style={[styles.summaryCard, { backgroundColor: colors.surface, borderColor: colors.border }]}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>진행 중 정산</Text><Text style={[styles.summaryValue, { color: colors.textPrimary }]}>{activeCount}건</Text></Pressable>
      <Pressable accessibilityRole="button" onPress={() => router.push('/shopping')} style={[styles.summaryCard, { backgroundColor: colors.surface, borderColor: colors.border }]}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>구매 대기</Text><Text style={[styles.summaryValue, { color: colors.textPrimary }]}>{pendingShopping}개</Text></Pressable>
    </View>
    <Pressable accessibilityRole="button" onPress={() => router.push('/cleaning')} style={[styles.nextChore, { backgroundColor: colors.primarySoft }]}><Text style={[styles.summaryLabel, { color: colors.primary }]}>가장 가까운 청소 일정</Text><Text numberOfLines={2} style={[styles.choreTitle, { color: colors.textPrimary }]}>{nextChore ? `${nextChore.title} · ${nextChore.assignee.display_name} · ${formatKoreanDate(nextChore.scheduled_date)}` : '예정된 청소 일정이 없습니다'}</Text></Pressable>
    <View style={styles.primaryAction}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>공동 지출이 생겼나요?</Text><Text style={[styles.description, { color: colors.textSecondary }]}>영수증을 등록하고 정산을 시작하세요.</Text><AppButton icon="add-a-photo" label="영수증 등록" onPress={() => router.push('/settlement')} /></View>
    <View style={styles.section}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>바로가기</Text><View style={[styles.list, { backgroundColor: colors.surface, borderColor: colors.border }]}><ListRow icon="receipt-long" onPress={() => router.push('/settlement')} subtitle="정산 내역과 납부 상태 확인" title="정산 보기" /><View style={[styles.divider, { backgroundColor: colors.divider }]} /><ListRow icon="cleaning-services" onPress={() => router.push('/cleaning')} subtitle="담당자와 예정일 확인" title="청소 일정" /><View style={[styles.divider, { backgroundColor: colors.divider }]} /><ListRow icon="shopping-cart" onPress={() => router.push('/shopping')} subtitle="공동 물품 목록 확인" title="장바구니" /></View></View>
  </ScreenContainer>;
}

const styles = StyleSheet.create({ summaryGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.md }, summaryCard: { borderRadius: Radius.card, borderWidth: 1, gap: 5, minWidth: '46%', padding: Spacing.item }, summaryLabel: { fontSize: 12, fontWeight: '700' }, summaryValue: { fontSize: 19, fontWeight: '900' }, nextChore: { borderRadius: Radius.card, gap: 6, padding: Spacing.item }, choreTitle: { fontSize: 15, fontWeight: '700', lineHeight: 21 }, primaryAction: { gap: Spacing.compact }, description: { fontSize: 14, lineHeight: 20 }, section: { gap: Spacing.item }, list: { borderRadius: Radius.card, borderWidth: 1, paddingHorizontal: Spacing.item }, divider: { height: StyleSheet.hairlineWidth, marginLeft: 52 } });
