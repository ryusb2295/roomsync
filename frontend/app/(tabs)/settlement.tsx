import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { ActionSheetModal } from '@/components/action-sheet-modal';
import { AppButton } from '@/components/app-button';
import { EmptyState } from '@/components/empty-state';
import { FeedbackBanner } from '@/components/feedback-banner';
import { InlineMessage } from '@/components/inline-message';
import { HouseContextBanner } from '@/components/house-context-banner';
import { ReceiptAnalysisEditor } from '@/components/receipt-analysis-editor';
import { RoomCard } from '@/components/room-card';
import { ScreenContainer } from '@/components/screen-container';
import { SectionHeader } from '@/components/section-header';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { API_BASE_URL } from '@/constants/api';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { ApiError, apiRequest } from '@/services/api';
import type { ReceiptAnalysis, Settlement, SettlementDeleteResult, SettlementPaymentStatusResult } from '@/types/api';
import { buildSharedSettlementSummary, getSettlementSaveDisabledReason } from '@/utils/receipt-settlement';
import { formatKoreanDate } from '@/utils/date';
import { formatAud } from '@/utils/money';

function formatAmount(amount: number): string {
  return formatAud(amount);
}

type SettlementBadgeKind = 'payer' | 'unpaid' | 'paid' | 'in_progress' | 'completed';

const settlementBadgePalette = {
  payer: { background: '#EAF2FF', foreground: '#2563EB', border: '#BFDBFE', icon: 'account-balance-wallet' as const, label: '결제자' },
  unpaid: { background: '#FFF4E5', foreground: '#D97706', border: '#FED7AA', icon: 'schedule' as const, label: '미납' },
  paid: { background: '#EAF8EE', foreground: '#16A34A', border: '#BBF7D0', icon: 'check-circle' as const, label: '완료' },
  in_progress: { background: '#FFF4E5', foreground: '#D97706', border: '#FED7AA', icon: 'schedule' as const, label: '진행 중' },
  completed: { background: '#EAF8EE', foreground: '#16A34A', border: '#BBF7D0', icon: 'check-circle' as const, label: '정산 완료' },
};

function SettlementStatusPill({ kind, disabled = false, onPress }: { kind: SettlementBadgeKind; disabled?: boolean; onPress?: () => void }) {
  const palette = settlementBadgePalette[kind];
  const content = <><MaterialIcons color={palette.foreground} name={palette.icon} size={16} /><Text style={[styles.statusPillText, { color: palette.foreground }]}>{palette.label}</Text></>;
  const pillStyle = [styles.statusPill, { backgroundColor: palette.background, borderColor: palette.border }, disabled && styles.statusPillDisabled];
  if (!onPress) return <View accessibilityLabel={palette.label} style={pillStyle}>{content}</View>;
  return <Pressable accessibilityLabel={`${palette.label} 상태 변경`} accessibilityRole="button" accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={({ pressed }) => [pillStyle, styles.statusPillTouchable, pressed && styles.statusPillPressed]}>{content}</Pressable>;
}

function RealSettlementScreen() {
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
  const [selectedPayerId, setSelectedPayerId] = useState<number | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisElapsedSeconds, setAnalysisElapsedSeconds] = useState(0);
  const [reconciling, setReconciling] = useState(false);
  const [saving, setSaving] = useState(false);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [loadingSettlements, setLoadingSettlements] = useState(true);
  const [settlementError, setSettlementError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Settlement | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [paymentUpdatingKey, setPaymentUpdatingKey] = useState<string | null>(null);
  const [paymentError, setPaymentError] = useState<string | null>(null);

  useEffect(() => {
    if (!analyzing) return;
    setAnalysisElapsedSeconds(0);
    const timer = setInterval(() => setAnalysisElapsedSeconds((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [analyzing]);

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
  const settlementSummary = useMemo(
    () => analysis ? buildSharedSettlementSummary(analysis) : null,
    [analysis]
  );
  const settlementTotal = settlementSummary?.total ?? 0;
  const selectedMemberIds = settlementSummary?.participantIds ?? [];
  const baseSaveDisabledReason = getSettlementSaveDisabledReason(
    analysis,
    settlementSummary,
    Boolean(token && user && currentHouse),
  );
  const saveDisabledReason = selectedPayerId === null
    ? '결제자를 선택해 주세요.'
    : baseSaveDisabledReason;

  const pickReceipt = async () => {
    if (analyzing) return;
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
    setSelectedPayerId(null);
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
        { method: 'POST', token, body: formData, timeoutMs: 90000 }
      );
      const allMemberIds = houseMembers.map((member) => member.user_id);
      setAnalysis({
        ...result,
        items: result.items.map((item) => ({
          ...item,
          is_shared: ['discount', 'coupon', 'refund', 'fee', 'tax'].includes(item.type ?? 'item') ? undefined : true,
          participant_user_ids: ['discount', 'coupon', 'refund', 'fee', 'tax'].includes(item.type ?? 'item') ? [] : allMemberIds,
        })),
      });
      setSelectedPayerId(user?.id ?? null);
      setAnalysisRevision((revision) => revision + 1);
    } catch (error) {
      setReceiptError(error instanceof ApiError && error.kind === 'timeout'
        ? '서버 응답 시간이 초과되었습니다.'
        : error instanceof Error ? error.message : '영수증을 분석하지 못했습니다.');
    } finally {
      setAnalyzing(false);
    }
  };

  const pickCroppedReceipt = async () => {
    if (analyzing) return;
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], allowsEditing: true, quality: 0.9 });
    if (!result.canceled) {
      setSelectedImage(result.assets[0]);
      setReceiptError(null);
    }
  };

  const startManualEntry = () => {
    const memberIds = houseMembers.map((member) => member.user_id);
    setAnalysis({
      store_name: '직접 입력 영수증', merchant_name: null, receipt_date: null, currency: 'AUD',
      items: [{ name: null, quantity: 1, price: null, line_total: null, gst_status: 'unknown', is_shared: true, participant_user_ids: memberIds }],
      total: null, amount_paid: null, gst_inclusion_type: 'unknown', gst_displayed: false,
      verified_total: null, reconciliation_status: 'needs_review', requires_review: true,
      model_reported_confidence: null, validation_score: 0, validation_status: 'invalid', validation_reasons: ['직접 입력한 금액을 다시 검증해 주세요.'], warnings: ['품목과 최종 결제금액을 입력한 뒤 다시 검증해 주세요.'], analysis_mode: 'edited', warning: null,
      uploaded_by: user?.id ?? null,
    });
    setSelectedPayerId(user?.id ?? null);
    setAnalysisRevision((value) => value + 1);
  };

  const toggleMember = (userId: number) => {
    if (saving || !analysis) return;
    const removeFromAll = selectedMemberIds.includes(userId);
    setAnalysis({
      ...analysis,
      items: analysis.items.map((item) => {
        if (item.is_shared === false) return item;
        const participants = item.participant_user_ids ?? [];
        return {
          ...item,
          participant_user_ids: removeFromAll
            ? participants.filter((id) => id !== userId)
            : [...new Set([...participants, userId])],
        };
      }),
    });
  };

  const reconcileReceipt = async (draftAnalysis: ReceiptAnalysis) => {
    if (!token || !currentHouse || reconciling) return;
    setReconciling(true);
    setReceiptError(null);
    try {
      const result = await apiRequest<ReceiptAnalysis>(
        `/houses/${currentHouse.id}/receipts/reconcile`,
        { method: 'POST', token, body: draftAnalysis }
      );
      setAnalysis({
        ...result,
        items: result.items.map((item, index) => ({
          ...item,
          is_shared: ['discount', 'coupon', 'refund', 'fee', 'tax'].includes(item.type ?? 'item') ? undefined : draftAnalysis.items[index]?.is_shared ?? true,
          participant_user_ids: ['discount', 'coupon', 'refund', 'fee', 'tax'].includes(item.type ?? 'item') ? [] : draftAnalysis.items[index]?.participant_user_ids ?? [],
        })),
      });
      setAnalysisRevision((revision) => revision + 1);
    } catch (error) {
      setReceiptError(error instanceof Error ? error.message : '수정한 금액을 검증하지 못했습니다.');
    } finally {
      setReconciling(false);
    }
  };

  const saveSettlement = async () => {
    if (!analysis || !token || !currentHouse || !user || selectedPayerId === null || saving || !settlementSummary || saveDisabledReason) {
      if (saveDisabledReason) setReceiptError(saveDisabledReason);
      return;
    }
    const title = analysis.store_name.trim() || '영수증 정산';
    if (analysis.requires_review || !analysis.verified_total) {
      setReceiptError('영수증 금액을 수정한 뒤 다시 검증해야 정산을 저장할 수 있습니다.');
      return;
    }
    if (settlementTotal <= 0) {
      setReceiptError('정산 총액은 0보다 커야 합니다.');
      return;
    }
    setSaving(true);
    setReceiptError(null);
    try {
      const path = `/houses/${currentHouse.id}/settlements`;
      const payload = {
        title,
        total_amount: settlementTotal,
        total_amount_cents: Math.round(settlementTotal * 100),
        participant_user_ids: selectedMemberIds,
        receipt_id: analysis.receipt_id,
        payer_id: selectedPayerId,
        uploaded_by: analysis.uploaded_by ?? user.id,
        receipt_date: analysis.receipt_date,
        receipt_verified_total: analysis.verified_total,
        receipt_reconciliation_status: analysis.reconciliation_status,
        receipt_items: settlementSummary.receiptItems,
        items: settlementSummary.receiptItems,
      };
      const payer = houseMembers.find((member) => member.user_id === selectedPayerId);
      if (__DEV__) {
        console.log('[Settlement] create request:', {
          current_user_id: user.id,
          uploaded_by: analysis.uploaded_by ?? user.id,
          selected_payer_id: selectedPayerId,
          payer_display_name: payer?.display_name,
          verified_total: analysis.verified_total,
          reconciliation_status: analysis.reconciliation_status,
          shared_item_count: settlementSummary.sharedItemCount,
          items: settlementSummary.receiptItems,
          participant_ids: selectedMemberIds,
          house_id: currentHouse.id,
          receipt_id: analysis.receipt_id,
          receipt_date: analysis.receipt_date,
          url: `${API_BASE_URL}${path}`,
          payload,
        });
      }
      await apiRequest<Settlement>(path, {
        method: 'POST',
        token,
        body: payload,
      });
      setSelectedImage(null);
      setAnalysis(null);
      setSelectedPayerId(null);
      await loadSettlements();
      setSuccess('정산을 저장했습니다.');
    } catch (error) {
      setReceiptError(error instanceof Error ? error.message : '정산을 저장하지 못했습니다.');
    } finally {
      setSaving(false);
    }
  };

  const canDelete = (settlement: Settlement) => Boolean(
    user && (currentHouse?.owner_id === user.id || settlement.created_by?.user_id === user.id)
  );

  const togglePaymentStatus = async (settlement: Settlement, participantUserId: number, currentStatus: 'unpaid' | 'paid') => {
    if (!token || !currentHouse || user?.id !== settlement.payer_id) return;
    const key = `${settlement.settlement_id}-${participantUserId}`;
    if (paymentUpdatingKey) return;
    setPaymentUpdatingKey(key);
    setPaymentError(null);
    try {
      const result = await apiRequest<SettlementPaymentStatusResult>(
        `/houses/${currentHouse.id}/settlements/${settlement.settlement_id}/participants/${participantUserId}/payment-status`,
        { method: 'PATCH', token, body: { payment_status: currentStatus === 'paid' ? 'unpaid' : 'paid' } },
      );
      setSettlements((current) => current.map((item) => item.settlement_id !== result.settlement_id ? item : {
        ...item,
        status: result.settlement_status,
        is_completed: result.settlement_status === 'completed',
        completed_at: result.completed_at,
        participants: item.participants.map((participant) => participant.user_id === result.participant_user_id
          ? { ...participant, payment_status: result.payment_status, paid_at: result.paid_at, confirmed_by: result.payment_status === 'paid' ? user.id : null }
          : participant),
      }));
    } catch (error) {
      setPaymentError(error instanceof Error ? error.message : '납부 상태를 변경하지 못했습니다.');
    } finally {
      setPaymentUpdatingKey(null);
    }
  };

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
      setSuccess('정산을 삭제했습니다.');
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
            {(settlement.uploaded_by_name ?? settlement.created_by?.name) ?? '기존 정산'} · {formatKoreanDate(settlement.receipt_date)}
          </Text>
        </View>
        <SettlementStatusPill kind={settlement.status === 'completed' ? 'completed' : 'in_progress'} />
      </View>
      <Text style={[styles.amount, { color: colors.primary }]}>{formatAmount(settlement.total_amount)}</Text>
      <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>결제자: {settlement.payer_name ?? '결제자 확인 필요'} · 등록일: {formatKoreanDate(settlement.created_at)}</Text>
      <View style={[styles.participantList, { borderTopColor: colors.divider }]}>
        {settlement.participants.length === 0 ? (
          <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>연결된 현재 하우스 멤버가 없습니다.</Text>
        ) : settlement.participants.map((participant) => (
          <View key={participant.user_id} style={styles.participantRow}>
            <Text style={[styles.participantName, { color: colors.textPrimary }]}>{participant.display_name ?? participant.name}</Text>
            <Text style={[styles.participantAmount, { color: colors.textSecondary }]}>{formatAmount(participant.share_amount_cents / 100)}</Text>
            {participant.role === 'payer' ? <SettlementStatusPill kind="payer" /> : <SettlementStatusPill disabled={paymentUpdatingKey !== null} kind={participant.payment_status === 'paid' ? 'paid' : 'unpaid'} onPress={user?.id === settlement.payer_id ? () => togglePaymentStatus(settlement, participant.user_id, participant.payment_status === 'paid' ? 'paid' : 'unpaid') : undefined} />}
          </View>
        ))}
      </View>
      {canDelete(settlement) ? <AppButton icon="delete-outline" label="정산 삭제" onPress={() => { setDeleteError(null); setDeleteTarget(settlement); }} variant="danger" /> : null}
    </RoomCard>
  );

  return (
    <ScreenContainer keyboardAware onRefresh={() => void loadSettlements()} refreshing={loadingSettlements} title="정산" subtitle={`${currentHouse?.name ?? '현재 하우스'}의 실제 정산 내역입니다.`}>
      <HouseContextBanner />
      {success ? <FeedbackBanner message={success} /> : null}
      {paymentError ? <InlineMessage message={paymentError} /> : null}
      <View style={styles.registerArea}>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: analyzing }} disabled={analyzing} onPress={pickReceipt} style={({ pressed }) => [styles.registerButton, { backgroundColor: pressed ? colors.primaryPressed : colors.primary }, analyzing && { opacity: 0.65 }]}>
          <MaterialIcons color={colors.onPrimary} name="add-a-photo" size={25} />
          <Text style={[styles.registerText, { color: colors.onPrimary }]}>{selectedImage ? '다른 영수증 선택' : '영수증 등록'}</Text>
        </Pressable>
        {receiptError ? <InlineMessage message={receiptError} /> : null}
        {selectedImage ? <RoomCard style={styles.previewCard}>
          <Image accessibilityLabel="선택한 영수증 미리보기" contentFit="contain" source={{ uri: selectedImage.uri }} style={[styles.previewImage, { backgroundColor: colors.surfaceMuted }]} />
          <Text numberOfLines={1} style={[styles.previewName, { color: colors.textPrimary }]}>{selectedImage.fileName || '선택한 영수증 이미지'}</Text>
          <AppButton disabled={analyzing} label={analyzing ? '영수증 분석 중' : 'AI로 영수증 분석'} loading={analyzing} onPress={analyzeReceipt} />
          {analyzing ? <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>이미지 품질에 따라 최대 1분 정도 걸릴 수 있습니다. · {analysisElapsedSeconds}초 경과</Text> : null}
          {receiptError && !analyzing ? <View style={styles.recoveryActions}><AppButton fullWidth={false} label="같은 이미지 다시 분석" onPress={analyzeReceipt} variant="secondary" /><AppButton fullWidth={false} label="다른 이미지 선택" onPress={pickReceipt} variant="tertiary" /><AppButton fullWidth={false} label="영수증 영역 자르기" onPress={pickCroppedReceipt} variant="tertiary" /><AppButton fullWidth={false} label="직접 입력" onPress={startManualEntry} variant="tertiary" /></View> : null}
        </RoomCard> : null}
      </View>

      {analysis ? <View style={styles.section}>
        <SectionHeader title="분석 결과" description="품목명과 금액을 확인하고 정산 대상자를 선택하세요." />
        <ReceiptAnalysisEditor key={analysisRevision} analysis={analysis} currentUserId={user?.id} members={houseMembers} onChange={setAnalysis} onReconcile={reconcileReceipt} reconciling={reconciling} />
        <View style={styles.memberSection}>
          <Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>실제 결제자</Text>
          <Text style={[styles.itemDetail, { color: colors.textSecondary }]}>저장할 결제자: {houseMembers.find((member) => member.user_id === selectedPayerId)?.display_name ?? '선택 필요'}</Text>
          <View style={[styles.memberList, { backgroundColor: colors.surface, borderColor: colors.border }]}>
            {houseMembers.map((member) => {
              const selected = member.user_id === selectedPayerId;
              return <Pressable accessibilityRole="radio" accessibilityState={{ checked: selected }} key={`payer-${member.user_id}`} onPress={() => setSelectedPayerId(member.user_id)} style={({ pressed }) => [styles.memberRow, pressed && { backgroundColor: colors.background }]}>
                <MaterialIcons color={selected ? colors.primary : colors.placeholder} name={selected ? 'radio-button-checked' : 'radio-button-unchecked'} size={24} />
                <Text style={[styles.memberName, { color: colors.textPrimary }]}>{member.display_name}</Text>
              </Pressable>;
            })}
          </View>
        </View>
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
          <Text style={[styles.sharePreview, { color: colors.textSecondary }]}>공용 품목 총액 {formatAmount(settlementTotal)} · 대상자 {selectedMemberIds.length}명</Text>
          {settlementSummary?.warnings.map((message) => <InlineMessage key={message} message={message} />)}
          {saveDisabledReason ? <InlineMessage message={saveDisabledReason} /> : null}
          <AppButton disabled={saveDisabledReason !== null} label="정산 저장" loading={saving} onPress={saveSettlement} />
        </View>
      </View> : null}

      <View style={styles.section}>
        <View style={styles.sectionHeading}><Text style={[Typography.sectionTitle, { color: colors.textPrimary }]}>정산 내역</Text><AppButton fullWidth={false} label="새로고침" onPress={loadSettlements} variant="tertiary" /></View>
        <Text style={[styles.paymentHint, { color: colors.textSecondary }]}>결제자는 미납 상태를 눌러 납부 완료로 변경할 수 있습니다.</Text>
        {loadingSettlements ? <Text style={[styles.loadingText, { color: colors.textSecondary }]}>정산 내역을 불러오는 중...</Text> : settlementError ? <View style={styles.errorState}><InlineMessage message={settlementError} /><AppButton label="다시 시도" onPress={loadSettlements} variant="secondary" /></View> : settlements.length === 0 ? (
          <EmptyState actionLabel="영수증 등록" description="영수증을 등록해 첫 정산을 만들어보세요" icon="receipt-long" onAction={pickReceipt} title="아직 정산 내역이 없습니다" />
        ) : <>
          {pendingRecords.length > 0 ? <View style={styles.list}><SectionHeader title="진행 중 정산" />{pendingRecords.map(renderSettlement)}</View> : null}
          {completedRecords.length > 0 ? <View style={styles.list}><SectionHeader title="완료된 정산" />{completedRecords.map(renderSettlement)}</View> : null}
        </>}
      </View>

      <ActionSheetModal confirmLabel="삭제" danger description="삭제된 정산과 납부 기록은 복구할 수 없습니다." disabled={deleting} error={deleteError} loading={deleting} onClose={() => { if (!deleting) setDeleteTarget(null); }} onConfirm={confirmDelete} title="정산을 삭제하시겠습니까?" visible={deleteTarget !== null}>
        <Text style={[styles.deleteHint, { color: colors.textSecondary }]}>{deleteTarget?.title}</Text>
      </ActionSheetModal>
    </ScreenContainer>
  );
}

export default function SettlementScreen() {
  return <RealSettlementScreen />;
}

const styles = StyleSheet.create({
  registerArea: { gap: Spacing.compact },
  registerButton: { alignItems: 'center', borderRadius: Radius.button, flexDirection: 'row', gap: Spacing.compact, justifyContent: 'center', minHeight: 54, paddingHorizontal: Spacing.item },
  registerText: { fontSize: 17, fontWeight: '700' },
  previewCard: { gap: Spacing.item },
  previewImage: { borderRadius: Radius.input, height: 260, width: '100%' },
  recoveryActions: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.item },
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
  statusPill: { alignItems: 'center', borderRadius: 999, borderWidth: 1, flexDirection: 'row', gap: 5, justifyContent: 'center', minWidth: 82, paddingHorizontal: 12, paddingVertical: 6 },
  statusPillText: { fontSize: 12, fontWeight: '700' },
  statusPillTouchable: { shadowColor: '#0F172A', shadowOffset: { width: 0, height: 1 }, shadowOpacity: 0.08, shadowRadius: 2 },
  statusPillPressed: { opacity: 0.72, transform: [{ scale: 0.97 }] },
  statusPillDisabled: { opacity: 0.62 },
  paymentHint: { fontSize: 12, lineHeight: 18 },
  deleteHint: { fontSize: 14, fontWeight: '600' },
});
