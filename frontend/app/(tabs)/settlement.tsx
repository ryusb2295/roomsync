import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionSheetModal } from '@/components/action-sheet-modal';
import { AppButton } from '@/components/app-button';
import { EmptyState } from '@/components/empty-state';
import { InlineMessage } from '@/components/inline-message';
import { ReceiptAnalysisEditor } from '@/components/receipt-analysis-editor';
import { RoomCard } from '@/components/room-card';
import { ScreenContainer } from '@/components/screen-container';
import { SectionHeader } from '@/components/section-header';
import { StatusBadge } from '@/components/status-badge';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { apiRequest } from '@/services/api';
import type { ReceiptAnalysis, Settlement, SettlementDeleteResult } from '@/types/api';

function formatAmount(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('ko-KR');
}

export default function SettlementScreen() {
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
  const [selectedImage, setSelectedImage] = useState<ImagePicker.ImagePickerAsset | null>(null);
  const [analysis, setAnalysis] = useState<ReceiptAnalysis | null>(null);
  const [analysisRevision, setAnalysisRevision] = useState(0);
  const [selectedMemberIds, setSelectedMemberIds] = useState<number[]>([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [loadingSettlements, setLoadingSettlements] = useState(true);
  const [settlementError, setSettlementError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Settlement | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadSettlements = useCallback(async () => {
    if (!token || !currentHouse) {
      setSettlements([]);
      setLoadingSettlements(false);
      return;
    }
    setLoadingSettlements(true);
    setSettlementError(null);
    try {
      setSettlements(await apiRequest<Settlement[]>(
        `/houses/${currentHouse.id}/settlements`, { token }
      ));
    } catch (error) {
      setSettlements([]);
      setSettlementError(error instanceof Error ? error.message : '정산 내역을 불러오지 못했습니다.');
    } finally {
      setLoadingSettlements(false);
    }
  }, [currentHouse, token]);

  useFocusEffect(useCallback(() => {
    void loadSettlements();
    void refreshHouseMembers();
  }, [loadSettlements, refreshHouseMembers]));

  const pendingRecords = useMemo(
    () => settlements.filter((settlement) => !settlement.is_completed),
    [settlements]
  );
  const completedRecords = useMemo(
    () => settlements.filter((settlement) => settlement.is_completed),
    [settlements]
  );
  const settlementTotal = useMemo(
    () => analysis?.items.reduce((sum, item) => sum + item.price, 0) ?? 0,
    [analysis]
  );

  const pickReceipt = async () => {
    setReceiptError(null);
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      setReceiptError('영수증을 선택하려면 설정에서 사진 접근을 허용해주세요.');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsMultipleSelection: false,
      quality: 0.85,
    });
    if (result.canceled) return;
    setSelectedImage(result.assets[0]);
    setAnalysis(null);
    setSelectedMemberIds([]);
  };

  const analyzeReceipt = async () => {
    if (!selectedImage || !token || !currentHouse) {
      setReceiptError('분석할 영수증 이미지와 현재 하우스를 확인해주세요.');
      return;
    }
    const formData = new FormData();
    formData.append('file', {
      uri: selectedImage.uri,
      name: selectedImage.fileName || `receipt-${Date.now()}.jpg`,
      type: selectedImage.mimeType || 'image/jpeg',
    } as unknown as Blob);
    setAnalyzing(true);
    setReceiptError(null);
    try {
      const result = await apiRequest<ReceiptAnalysis>(
        `/houses/${currentHouse.id}/receipts/analyze`,
        { method: 'POST', token, body: formData }
      );
      setAnalysis(result);
      setAnalysisRevision((revision) => revision + 1);
      setSelectedMemberIds(houseMembers.map((member) => member.user_id));
    } catch (error) {
      setAnalysis(null);
      setReceiptError(error instanceof Error ? error.message : '영수증을 분석하지 못했습니다.');
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleMember = (userId: number) => {
    if (saving) return;
    setSelectedMemberIds((current) => current.includes(userId)
      ? current.filter((id) => id !== userId)
      : [...current, userId]);
  };

  const saveSettlement = async () => {
    if (!analysis || !token || !currentHouse || saving || selectedMemberIds.length === 0) return;
    const title = analysis.store_name.trim() || '영수증 정산';
    if (settlementTotal <= 0) {
      setReceiptError('정산 총액은 0보다 커야 합니다.');
      return;
    }
    setSaving(true);
    setReceiptError(null);
    try {
      await apiRequest<Settlement>(`/houses/${currentHouse.id}/settlements`, {
        method: 'POST',
        token,
        body: {
          title,
          total_amount: settlementTotal,
          participant_user_ids: selectedMemberIds,
        },
      });
      setSelectedImage(null);
      setAnalysis(null);
      setSelectedMemberIds([]);
      await loadSettlements();
    } catch (error) {
      setReceiptError(error instanceof Error ? error.message : '정산을 저장하지 못했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const canDelete = (settlement: Settlement) => Boolean(
    user && (currentHouse?.owner_id === user.id || settlement.created_by?.user_id === user.id)
  );

  const confirmDelete = async () => {
    if (!deleteTarget || !token || !currentHouse || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiRequest<SettlementDeleteResult>(
        `/houses/${currentHouse.id}/settlements/${deleteTarget.settlement_id}`,
        { method: 'DELETE', token }
      );
      setDeleteTarget(null);
      await loadSettlements();
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : '정산 내역을 삭제하지 못했습니다.');
    } finally {
      setDeleting(false);
    }
  };

  const renderSettlement = (settlement: Settlement) => (
    <RoomCard key={settlement.settlement_id} style={styles.settlementCard}>
      <View style={styles.itemTopRow}>
        <View style={styles.itemCopy}>
          <Text style={[styles.itemTitle, { color: colors.textPrimary }]}>{settlement.title}</Text>
          <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>
            {settlement.created_by?.name ?? '기존 정산'} · {formatDate(settlement.created_at)}
          </Text>
        </View>
        <StatusBadge label={settlement.is_completed ? '완료' : settlement.status} tone={settlement.is_completed ? 'completed' : 'pending'} />
      </View>
      <Text style={[styles.amount, { color: colors.primary }]}>{formatAmount(settlement.total_amount)}</Text>
      <View style={[styles.participantList, { borderTopColor: colors.divider }]}>
        {settlement.participants.length === 0 ? (
          <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>연결된 현재 하우스 멤버가 없습니다.</Text>
        ) : settlement.participants.map((participant) => (
          <View key={participant.user_id} style={styles.participantRow}>
            <Text style={[styles.participantName, { color: colors.textPrimary }]}>{participant.name}</Text>
            <Text style={[styles.participantAmount, { color: colors.textSecondary }]}>{formatAmount(participant.amount)} · {participant.payment_status}</Text>
          </View>
        ))}
      </View>
      {canDelete(settlement) ? <AppButton icon="delete-outline" label="정산 삭제" onPress={() => { setDeleteError(null); setDeleteTarget(settlement); }} variant="danger" /> : null}
    </RoomCard>
  );

  return (
    <ScreenContainer keyboardAware title="정산" subtitle={`${currentHouse?.name ?? '현재 하우스'}의 실제 정산 내역입니다.`}>
      <View style={styles.registerArea}>
        <Pressable accessibilityRole="button" onPress={pickReceipt} style={({ pressed }) => [styles.registerButton, { backgroundColor: pressed ? colors.primaryPressed : colors.primary }]}>
          <MaterialIcons color={colors.onPrimary} name="add-a-photo" size={25} />
          <Text style={[styles.registerText, { color: colors.onPrimary }]}>{selectedImage ? '다른 영수증 선택' : '영수증 등록'}</Text>
        </Pressable>
        {receiptError ? <InlineMessage message={receiptError} /> : null}
        {selectedImage ? <RoomCard style={styles.previewCard}>
          <Image accessibilityLabel="선택한 영수증 미리보기" contentFit="contain" source={{ uri: selectedImage.uri }} style={[styles.previewImage, { backgroundColor: colors.surfaceMuted }]} />
          <Text numberOfLines={1} style={[styles.previewName, { color: colors.textPrimary }]}>{selectedImage.fileName || '선택한 영수증 이미지'}</Text>
          <AppButton label="AI로 영수증 분석" loading={analyzing} onPress={analyzeReceipt} />
        </RoomCard> : null}
      </View>

      {analysis ? <View style={styles.section}>
        <SectionHeader title="분석 결과" description="품목명과 금액을 확인하고 정산 대상자를 선택하세요." />
        <ReceiptAnalysisEditor key={analysisRevision} analysis={analysis} onChange={setAnalysis} />
        <View style={styles.memberSection}>
          <View style={styles.sectionHeading}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>정산 대상자</Text><AppButton fullWidth={false} label="멤버 새로고침" onPress={refreshHouseMembers} variant="tertiary" /></View>
          {membersError ? <InlineMessage message={membersError} /> : membersLoading ? <Text style={[styles.loadingText, { color: colors.textSecondary }]}>하우스 멤버를 불러오는 중...</Text> : houseMembers.length === 0 ? <InlineMessage message="현재 하우스 멤버를 확인할 수 없습니다." /> : (
            <View style={[styles.memberList, { backgroundColor: colors.surface, borderColor: colors.border }]}>
              {houseMembers.map((member) => {
                const selected = selectedMemberIds.includes(member.user_id);
                return <Pressable accessibilityRole="checkbox" accessibilityState={{ checked: selected }} key={member.user_id} onPress={() => toggleMember(member.user_id)} style={({ pressed }) => [styles.memberRow, pressed && { backgroundColor: colors.background }]}>
                  <MaterialIcons color={selected ? colors.primary : colors.placeholder} name={selected ? 'check-circle' : 'radio-button-unchecked'} size={24} />
                  <View style={styles.itemCopy}><Text style={[styles.memberName, { color: colors.textPrimary }]}>{member.display_name}</Text><Text style={[styles.itemDetail, { color: colors.textSecondary }]}>{member.role === 'owner' ? '소유자' : '멤버'}</Text></View>
                </Pressable>;
              })}
            </View>
          )}
          <Text style={[styles.sharePreview, { color: colors.textSecondary }]}>총 {formatAmount(settlementTotal)} · {selectedMemberIds.length > 0 ? `1인당 약 ${formatAmount(settlementTotal / selectedMemberIds.length)}` : '대상자를 선택해주세요'}</Text>
          <AppButton disabled={selectedMemberIds.length === 0 || settlementTotal <= 0} label="정산 저장" loading={saving} onPress={saveSettlement} />
        </View>
      </View> : null}

      <View style={styles.section}>
        <View style={styles.sectionHeading}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>정산 내역</Text><AppButton fullWidth={false} label="새로고침" onPress={loadSettlements} variant="tertiary" /></View>
        {loadingSettlements ? <Text style={[styles.loadingText, { color: colors.textSecondary }]}>정산 내역을 불러오는 중...</Text> : settlementError ? <View style={styles.errorState}><InlineMessage message={settlementError} /><AppButton label="다시 시도" onPress={loadSettlements} variant="secondary" /></View> : settlements.length === 0 ? (
          <EmptyState actionLabel="영수증 등록" description="영수증을 등록해 첫 정산을 만들어보세요" icon="receipt-long" onAction={pickReceipt} title="아직 정산 내역이 없습니다" />
        ) : <>
          {pendingRecords.length > 0 ? <View style={styles.list}><SectionHeader title="진행 중 정산" />{pendingRecords.map(renderSettlement)}</View> : null}
          {completedRecords.length > 0 ? <View style={styles.list}><SectionHeader title="완료된 정산" />{completedRecords.map(renderSettlement)}</View> : null}
        </>}
      </View>

      <ActionSheetModal confirmLabel="정산 내역 삭제" danger description="삭제하면 하우스 구성원의 정산 목록에서 보이지 않지만 금융 기록은 DB에 보존됩니다." disabled={deleting} error={deleteError} loading={deleting} onClose={() => { if (!deleting) setDeleteTarget(null); }} onConfirm={confirmDelete} title="이 정산 내역을 삭제할까요?" visible={deleteTarget !== null}>
        <Text style={[styles.deleteHint, { color: colors.textSecondary }]}>{deleteTarget?.title}</Text>
      </ActionSheetModal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  registerArea: { gap: Spacing.compact },
  registerButton: { alignItems: 'center', borderRadius: Radius.button, flexDirection: 'row', gap: Spacing.compact, justifyContent: 'center', minHeight: 54, paddingHorizontal: Spacing.item },
  registerText: { fontSize: 17, fontWeight: '700' },
  previewCard: { gap: Spacing.item },
  previewImage: { borderRadius: Radius.input, height: 260, width: '100%' },
  previewName: { fontSize: 15, fontWeight: '700' },
  section: { gap: Spacing.item },
  sectionHeading: { alignItems: 'center', flexDirection: 'row', gap: Spacing.item, justifyContent: 'space-between' },
  memberSection: { gap: Spacing.item },
  memberList: { borderRadius: Radius.card, borderWidth: 1, overflow: 'hidden' },
  memberRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.md, minHeight: 62, paddingHorizontal: Spacing.item, paddingVertical: Spacing.compact },
  memberName: { fontSize: 15, fontWeight: '700' },
  sharePreview: { fontSize: 13, lineHeight: 19, textAlign: 'center' },
  loadingText: { fontSize: 14, paddingVertical: Spacing.section, textAlign: 'center' },
  errorState: { gap: Spacing.item },
  list: { gap: Spacing.md },
  settlementCard: { gap: Spacing.item },
  itemTopRow: { alignItems: 'flex-start', flexDirection: 'row', gap: Spacing.md },
  itemCopy: { flex: 1, gap: Spacing.xs },
  itemTitle: { fontSize: 17, fontWeight: '700' },
  itemDetail: { fontSize: 13, lineHeight: 18 },
  amount: { fontSize: 20, fontWeight: '800' },
  participantList: { borderTopWidth: StyleSheet.hairlineWidth, gap: Spacing.compact, paddingTop: Spacing.item },
  participantRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.item, justifyContent: 'space-between' },
  participantName: { flex: 1, fontSize: 14, fontWeight: '600' },
  participantAmount: { fontSize: 13 },
  deleteHint: { fontSize: 14, fontWeight: '600' },
});
