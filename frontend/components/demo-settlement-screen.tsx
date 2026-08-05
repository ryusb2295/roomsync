import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { useEffect, useMemo, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionSheetModal } from '@/components/action-sheet-modal';
import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { EmptyState } from '@/components/empty-state';
import { HouseContextBanner } from '@/components/house-context-banner';
import { InlineMessage } from '@/components/inline-message';
import { RoomCard } from '@/components/room-card';
import { ScreenContainer } from '@/components/screen-container';
import { SegmentedControl } from '@/components/segmented-control';
import { SectionHeader } from '@/components/section-header';
import { StatusBadge } from '@/components/status-badge';
import { Radius, Spacing } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import {
  createDemoSettlements,
  type DemoReceiptItem,
  type DemoSettlement,
  withDerivedStatus,
} from '@/data/demo-settlements';
import { useRoomTheme } from '@/hooks/use-room-theme';

const money = (amount: number) => `AUD ${amount.toFixed(2)}`;

export function DemoSettlementScreen() {
  const colors = useRoomTheme();
  const { currentHouse, houseMembers, membersError, membersLoading, refreshHouseMembers, user } = useAuth();
  const [settlements, setSettlements] = useState<DemoSettlement[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [newItemName, setNewItemName] = useState('');
  const [newItemAmount, setNewItemAmount] = useState('');
  const demoMembers = useMemo(
    () => houseMembers.filter((member) => member.house_id === currentHouse?.id),
    [currentHouse?.id, houseMembers]
  );

  useEffect(() => {
    setSettlements(createDemoSettlements(demoMembers, user?.id));
    setSelectedId(null);
  }, [currentHouse?.id, demoMembers, user?.id]);

  const selected = settlements.find((settlement) => settlement.id === selectedId) ?? null;
  const selectedItemTotal = selected?.items.reduce((sum, item) => sum + item.amount, 0) ?? 0;
  const titleChanged = Boolean(selected && editTitle.trim() && editTitle.trim() !== selected.title);
  const active = useMemo(() => settlements.filter((item) => item.status === 'active'), [settlements]);
  const completed = useMemo(() => settlements.filter((item) => item.status === 'completed'), [settlements]);
  const canDeleteSelected = Boolean(selected && user && (
    selected.createdByUserId === user.id || currentHouse?.owner_id === user.id
  ));

  const updateSelected = (updater: (settlement: DemoSettlement) => DemoSettlement) => {
    if (!selectedId) return;
    setSettlements((current) => current.map((item) => item.id === selectedId ? updater(item) : item));
  };

  const openDetail = (settlement: DemoSettlement) => {
    setSelectedId(settlement.id);
    setEditTitle(settlement.title);
    setNewItemName('');
    setNewItemAmount('');
  };

  const toggleMyPayment = () => updateSelected((settlement) => withDerivedStatus({
    ...settlement,
    participants: settlement.participants.map((participant) => participant.userId === user?.id
      ? { ...participant, paymentStatus: participant.paymentStatus === 'paid' ? 'unpaid' : 'paid' }
      : participant),
  }));

  const updateItem = (itemId: string, patch: Partial<DemoReceiptItem>) => updateSelected((settlement) => ({
    ...settlement,
    items: settlement.items.map((item) => item.id === itemId ? { ...item, ...patch } : item),
  }));

  const toggleItemParticipant = (itemId: string, userId: number) => updateSelected((settlement) => ({
    ...settlement,
    items: settlement.items.map((item) => item.id !== itemId ? item : {
      ...item,
      participantUserIds: item.participantUserIds.includes(userId)
        ? item.participantUserIds.filter((id) => id !== userId)
        : [...item.participantUserIds, userId],
    }),
  }));

  const setItemShared = (itemId: string, isShared: boolean) => updateSelected((settlement) => ({
    ...settlement,
    items: settlement.items.map((item) => item.id === itemId ? {
      ...item,
      isShared,
      participantUserIds: isShared ? (item.participantUserIds.length ? item.participantUserIds : demoMembers.map((member) => member.user_id)) : user ? [user.id] : [],
    } : item),
  }));

  const confirmDeleteItem = (itemId: string) => Alert.alert('품목 삭제', '이 품목을 삭제할까요?', [
    { text: '취소', style: 'cancel' },
    { text: '삭제', style: 'destructive', onPress: () => updateSelected((settlement) => ({ ...settlement, items: settlement.items.filter((item) => item.id !== itemId) })) },
  ]);

  const addItem = () => {
    const amount = Number(newItemAmount.replace(',', '.'));
    if (!newItemName.trim() || !Number.isFinite(amount) || amount < 0) return;
    updateSelected((settlement) => ({
      ...settlement,
      items: [...settlement.items, {
        id: `demo-item-${Date.now()}`,
        name: newItemName.trim(),
        amount,
        isShared: true,
        participantUserIds: demoMembers.map((member) => member.user_id),
      }],
    }));
    setNewItemName('');
    setNewItemAmount('');
  };

  const createSettlement = () => {
    if (demoMembers.length === 0) return;
    const created = createDemoSettlements(demoMembers, user?.id)[0];
    setSettlements((current) => [{
      ...created,
      id: `demo-created-${Date.now()}`,
      title: '새 시연 정산',
      createdAt: new Date().toISOString(),
    }, ...current]);
  };

  const confirmDeleteSettlement = (settlementId: string) => {
    const target = settlements.find((item) => item.id === settlementId);
    Alert.alert(
      '정산 삭제',
      target?.status === 'completed'
        ? '완료된 정산을 삭제할까요?\n삭제 후 목록에서 보이지 않습니다.'
        : '이 정산을 삭제할까요?\n시연 모드에서는 현재 화면에서만 제거됩니다.',
      [
        { text: '취소', style: 'cancel' },
        {
          text: '삭제',
          style: 'destructive',
          onPress: () => {
            setSettlements((current) => current.filter((item) => item.id !== settlementId));
            setSelectedId(null);
          },
        },
      ]
    );
  };

  const confirmResetDemo = () => Alert.alert(
    '시연 데이터 초기화',
    '시연 데이터를 처음 상태로 되돌릴까요?\n현재 수정하거나 삭제한 시연 데이터가 초기화됩니다.',
    [
      { text: '취소', style: 'cancel' },
      { text: '초기화', style: 'destructive', onPress: () => { setSettlements(createDemoSettlements(demoMembers, user?.id)); setSelectedId(null); } },
    ]
  );

  const renderCard = (settlement: DemoSettlement) => {
    const mine = settlement.participants.find((participant) => participant.userId === user?.id);
    return <RoomCard key={settlement.id} style={styles.card}>
      <View style={styles.rowBetween}>
        <View style={styles.flex}><Text style={[styles.title, { color: colors.textPrimary }]}>{settlement.title}</Text><Text style={{ color: colors.textSecondary }}>{new Date(settlement.createdAt).toLocaleDateString('ko-KR')}</Text></View>
        <StatusBadge label={settlement.status === 'completed' ? '완료' : '진행 중'} tone={settlement.status === 'completed' ? 'completed' : 'pending'} />
      </View>
      <Text style={[styles.amount, { color: colors.primary }]}>{money(settlement.totalAmount)}</Text>
      {settlement.participants.map((participant) => <View key={participant.userId} style={styles.rowBetween}><Text style={{ color: colors.textPrimary }}>{participant.name} · {participant.role}</Text><Text style={{ color: colors.textSecondary }}>{money(participant.amount)} · {participant.paymentStatus === 'paid' ? '납부 완료' : '미납'}</Text></View>)}
      {mine ? <Text style={{ color: colors.textSecondary }}>내 부담액 {money(mine.amount)}</Text> : null}
      <AppButton label="정산 상세" onPress={() => openDetail(settlement)} variant="secondary" />
    </RoomCard>;
  };

  if (!currentHouse) return <ScreenContainer title="정산" subtitle="시연 모드"><EmptyState description="정산 시연을 보려면 먼저 하우스를 선택해주세요." icon="home" title="선택된 하우스가 없습니다" /></ScreenContainer>;

  return <ScreenContainer keyboardAware onRefresh={() => undefined} refreshing={false} title="정산" subtitle={`${currentHouse.name} · 실제 구성원 ${demoMembers.length}명`}>
    <HouseContextBanner />
    <View style={[styles.demoBanner, { backgroundColor: colors.warningSoft }]}><MaterialIcons color={colors.warning} name="science" size={20} /><View style={styles.flex}><Text style={[styles.demoTitle, { color: colors.warning }]}>시연 모드 · 변경 내용은 저장되지 않습니다</Text><Text style={[styles.demoText, { color: colors.warning }]}>시연용 데이터이며 앱을 다시 실행하면 초기화됩니다.</Text></View></View>
    {membersLoading ? <Text style={{ color: colors.textSecondary }}>하우스 구성원을 불러오는 중...</Text> : membersError ? <><InlineMessage message={membersError} /><AppButton label="다시 시도" onPress={() => void refreshHouseMembers()} variant="secondary" /></> : demoMembers.length === 0 ? <EmptyState description="실제 멤버 조회 결과가 비어 있어 시연 데이터를 만들지 않았습니다." icon="group-off" title="하우스 구성원이 없습니다" /> : <>
      <View style={styles.demoActions}><AppButton icon="add" label="정산 생성 demo" onPress={createSettlement} /><AppButton label="시연 데이터 초기화" onPress={confirmResetDemo} variant="secondary" /></View>
      <SectionHeader title="진행 중 정산" description={`${active.length}건`} />{active.map(renderCard)}
      <SectionHeader title="완료 정산" description={`${completed.length}건`} />{completed.map(renderCard)}
    </>}

    <ActionSheetModal scrollable visible={Boolean(selected)} title="정산 상세 및 수정" description="모든 변경은 현재 화면의 메모리에만 반영됩니다." confirmLabel={titleChanged ? '제목 저장' : '변경 없음'} onClose={() => setSelectedId(null)} onConfirm={() => { updateSelected((item) => ({ ...item, title: editTitle.trim() || item.title })); setSelectedId(null); }} disabled={!titleChanged}>
      {selected ? <View style={styles.detail}>
        <AppTextField label="정산 제목" value={editTitle} onChangeText={setEditTitle} />
        {selected.participants.find((participant) => participant.userId === user?.id) ? <AppButton label={selected.participants.find((participant) => participant.userId === user?.id)?.paymentStatus === 'paid' ? '내 납부 완료 취소' : '내 납부 완료'} onPress={toggleMyPayment} variant="secondary" /> : null}
        <SectionHeader title="영수증 품목별 참여자" />
        <Text style={{ color: colors.textSecondary }}>품목 합계 {money(selectedItemTotal)}</Text>
        {selected.items.length === 0 ? <EmptyState description="새 품목 입력 폼에서 언제든 추가할 수 있습니다." icon="receipt-long" title="등록된 품목이 없습니다" /> : null}
        {selected.items.map((item) => <View key={item.id} style={[styles.item, { borderColor: colors.border }]}>
          <AppTextField label="품목명" value={item.name} onChangeText={(name) => updateItem(item.id, { name })} />
          <AppTextField keyboardType="decimal-pad" label="금액" value={String(item.amount)} onChangeText={(value) => { const amount = Number(value.replace(',', '.')); if (Number.isFinite(amount)) updateItem(item.id, { amount }); }} />
          <SegmentedControl options={[{ label: '개인 품목', value: 'private' }, { label: '공용 품목', value: 'shared' }]} value={item.isShared ? 'shared' : 'private'} onChange={(value) => setItemShared(item.id, value === 'shared')} />
          {item.isShared ? <View style={styles.participantGroup}><Text style={[styles.itemLabel, { color: colors.textPrimary }]}>참여자</Text>{demoMembers.map((member) => { const checked = item.participantUserIds.includes(member.user_id); return <Pressable accessibilityRole="checkbox" accessibilityState={{ checked }} key={member.user_id} onPress={() => toggleItemParticipant(item.id, member.user_id)} style={styles.member}><MaterialIcons color={checked ? colors.primary : colors.placeholder} name={checked ? 'check-box' : 'check-box-outline-blank'} size={21} /><Text numberOfLines={1} style={[styles.memberText, { color: colors.textPrimary }]}>{member.display_name}</Text></Pressable>; })}</View> : null}
          <View style={styles.itemActionRow}><AppButton fullWidth={false} label="품목 삭제" onPress={() => confirmDeleteItem(item.id)} size="compact" variant="danger" /></View>
        </View>)}
        <View style={[styles.item, { backgroundColor: colors.surfaceMuted, borderColor: colors.border }]}><AppTextField label="새 품목명" value={newItemName} onChangeText={setNewItemName} /><AppTextField keyboardType="decimal-pad" label="새 품목 금액" value={newItemAmount} onChangeText={setNewItemAmount} /><AppButton label="품목 추가" onPress={addItem} variant="secondary" /></View>
        {canDeleteSelected ? <AppButton label="정산 삭제" onPress={() => confirmDeleteSettlement(selected.id)} variant="danger" /> : null}
      </View> : null}
    </ActionSheetModal>
  </ScreenContainer>;
}

const styles = StyleSheet.create({
  amount: { fontSize: 20, fontWeight: '800' }, card: { gap: Spacing.item }, demoActions: { gap: Spacing.compact }, demoBanner: { borderRadius: Radius.input, flexDirection: 'row', gap: Spacing.compact, padding: Spacing.item }, demoText: { fontSize: 12, marginTop: 3 }, demoTitle: { fontSize: 14, fontWeight: '800' }, detail: { gap: Spacing.item }, flex: { flex: 1 }, item: { borderRadius: Radius.card, borderWidth: 1, gap: Spacing.item, padding: Spacing.item }, itemActionRow: { alignItems: 'center', flexDirection: 'row', justifyContent: 'flex-end' }, itemLabel: { fontSize: 14, fontWeight: '800' }, member: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact, minHeight: 44 }, memberText: { flex: 1 }, participantGroup: { gap: Spacing.compact }, rowBetween: { alignItems: 'center', flexDirection: 'row', gap: Spacing.item, justifyContent: 'space-between' }, title: { fontSize: 16, fontWeight: '800' },
});
