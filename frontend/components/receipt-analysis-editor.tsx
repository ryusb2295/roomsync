import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { EmptyState } from '@/components/empty-state';
import { SegmentedControl } from '@/components/segmented-control';
import { StatusBadge } from '@/components/status-badge';
import { Radius, Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';
import type { HouseMember, ReceiptAnalysis, ReceiptItem } from '@/types/api';
import { formatKoreanDate } from '@/utils/date';
import { formatAud } from '@/utils/money';

type Props = { analysis: ReceiptAnalysis; members: HouseMember[]; currentUserId?: number; onChange: (analysis: ReceiptAnalysis) => void; onReconcile: (draft: ReceiptAnalysis) => void; reconciling: boolean };
function parseOptionalNumber(value: string): number | null { if (!value.trim()) return null; const parsed = Number(value.replace(',', '.')); return Number.isFinite(parsed) ? parsed : null; }
function moneyText(value?: number | null): string { return value == null ? '' : String(value); }

const gstLabels: Record<ReceiptAnalysis['gst_inclusion_type'], string> = {
  included: 'GST 포함', excluded_then_added: 'GST 별도 추가', not_displayed: 'GST 미표시', mixed: '과세·비과세 혼합', unknown: '확인 필요',
};
const reconciliationLabels: Record<ReceiptAnalysis['reconciliation_status'], string> = {
  verified: '검증 완료', verified_gst_included: 'GST 포함 검증 완료', verified_gst_added: 'GST 별도 검증 완료', mismatch: '금액 불일치', needs_review: '사용자 확인 필요',
};
const validationLabels: Record<ReceiptAnalysis['validation_status'], string> = {
  verified: '검증 완료', mostly_verified: '대부분 검증됨', needs_review: '사용자 확인 권장', invalid: '재검토 필요',
};

export function ReceiptAnalysisEditor({ analysis, members, currentUserId, onChange, onReconcile, reconciling }: Props) {
  const colors = useRoomTheme();
  const [draftItems, setDraftItems] = useState(() => analysis.items.map((item) => ({ quantity: moneyText(item.quantity), price: moneyText(item.line_total ?? item.price) })));
  const [draftAmounts, setDraftAmounts] = useState(() => ({ subtotal: moneyText(analysis.subtotal), gst: moneyText(analysis.gst), discount: moneyText(analysis.discount), rounding: moneyText(analysis.rounding), total: moneyText(analysis.total), amount_paid: moneyText(analysis.amount_paid) }));
  const editedTotal = analysis.items.reduce((cents, item) => cents + Math.round((item.line_total ?? item.price ?? 0) * 100), 0) / 100;
  const validationScore = Math.min(Math.max(Math.round(analysis.validation_score ?? 0), 0), 100);
  const validationColor = validationScore >= 90 ? colors.success : validationScore >= 50 ? colors.warning : colors.danger;
  const validationIcon = validationScore >= 90 ? 'check-circle' : validationScore >= 50 ? 'warning' : 'error';
  const itemTotalsVerified = analysis.validation_reasons?.some((reason) => reason.includes('품목 순합계')) ?? false;
  const gstAndDiscountVerified = analysis.validation_reasons?.some((reason) => reason.includes('GST'))
    && analysis.validation_reasons?.some((reason) => reason.includes('할인 라인이 중복'));
  const finalTotalVerified = analysis.validation_reasons?.some((reason) => reason.includes('Total과 Amount Paid')) ?? false;
  const warningMessages = analysis.warnings?.length ? analysis.warnings : analysis.warning ? [analysis.warning] : [];
  const updateItem = (index: number, patch: Partial<ReceiptItem>, invalidatesAmounts = true) => onChange({
    ...analysis,
    items: analysis.items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item),
    ...(invalidatesAmounts ? { requires_review: true, reconciliation_status: 'needs_review' as const, verified_total: null } : {}),
  });
  const updateNumber = (index: number, field: 'quantity' | 'price', value: string) => {
    setDraftItems((current) => current.map((draft, itemIndex) => itemIndex === index ? { ...draft, [field]: value } : draft));
    const parsed = parseOptionalNumber(value);
    updateItem(index, field === 'price' ? { price: parsed, line_total: parsed, amount: parsed } : { quantity: parsed });
  };
  const updateAmount = (field: keyof typeof draftAmounts, value: string) => {
    setDraftAmounts((current) => ({ ...current, [field]: value }));
    onChange({ ...analysis, [field]: parseOptionalNumber(value), requires_review: true, reconciliation_status: 'needs_review', verified_total: null });
  };
  const reconcileLatestDraft = () => onReconcile({
    ...analysis,
    subtotal: parseOptionalNumber(draftAmounts.subtotal),
    gst: parseOptionalNumber(draftAmounts.gst),
    discount: parseOptionalNumber(draftAmounts.discount),
    rounding: parseOptionalNumber(draftAmounts.rounding),
    total: parseOptionalNumber(draftAmounts.total),
    amount_paid: parseOptionalNumber(draftAmounts.amount_paid),
    items: analysis.items.map((item, index) => {
      const lineTotal = parseOptionalNumber(draftItems[index]?.price ?? '');
      return {
        ...item,
        quantity: parseOptionalNumber(draftItems[index]?.quantity ?? ''),
        price: lineTotal,
        line_total: lineTotal,
        amount: lineTotal,
      };
    }),
  });
  const setShared = (index: number, shared: boolean) => updateItem(index, {
    is_shared: shared,
    participant_user_ids: shared
      ? (analysis.items[index].participant_user_ids?.length ? analysis.items[index].participant_user_ids : members.map((member) => member.user_id))
      : currentUserId ? [currentUserId] : [],
  }, false);
  const toggleParticipant = (index: number, userId: number) => {
    const current = analysis.items[index].participant_user_ids ?? members.map((member) => member.user_id);
    updateItem(index, { participant_user_ids: current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId] }, false);
  };
  const deleteItem = (index: number) => Alert.alert('품목 삭제', '이 품목을 삭제할까요?', [
    { text: '취소', style: 'cancel' },
    { text: '삭제', style: 'destructive', onPress: () => { onChange({ ...analysis, items: analysis.items.filter((_, itemIndex) => itemIndex !== index), requires_review: true, reconciliation_status: 'needs_review', verified_total: null }); setDraftItems((current) => current.filter((_, itemIndex) => itemIndex !== index)); } },
  ]);
  const addItem = () => {
    onChange({ ...analysis, items: [...analysis.items, { name: '', quantity: 1, price: 0, line_total: 0, amount: 0, type: 'item', gst_status: 'unknown', is_shared: true, participant_user_ids: members.map((member) => member.user_id) }], requires_review: true, reconciliation_status: 'needs_review', verified_total: null });
    setDraftItems((current) => [...current, { quantity: '1', price: '0' }]);
  };

  return (
    <View style={styles.container}>
      {warningMessages.length ? <View style={[styles.warning, { backgroundColor: colors.warningSoft }]}><MaterialIcons color={colors.warning} name="info-outline" size={19} /><View style={styles.warningCopy}>{warningMessages.map((message, index) => <Text key={`${index}-${message}`} style={[styles.warningText, { color: colors.warning }]}>• {message}</Text>)}</View></View> : null}
      <View style={[styles.summary, { backgroundColor: colors.surface, borderColor: colors.border }]}>
        <View style={styles.modeRow}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>분석 정보</Text><StatusBadge label={analysis.analysis_mode === 'gemini' ? 'Gemini 분석' : analysis.analysis_mode === 'openai' ? 'OpenAI 분석' : analysis.analysis_mode === 'edited' ? '수정 후 재검증' : 'Mock 분석'} tone={analysis.requires_review ? 'pending' : 'completed'} /></View>
        <AppTextField label="매장명" onChangeText={(storeName) => onChange({ ...analysis, store_name: storeName })} value={analysis.store_name} />
        <View style={styles.metadataRow}><View style={styles.metadataItem}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>영수증 날짜</Text><Text style={[styles.metadataValue, { color: colors.textPrimary }]}>{formatKoreanDate(analysis.receipt_date, '확인 불가')}</Text></View><View style={styles.metadataItem}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>통화</Text><Text style={[styles.metadataValue, { color: colors.textPrimary }]}>{analysis.currency ?? '확인 불가'}</Text></View></View>
        <View style={[styles.divider, { backgroundColor: colors.divider }]} />
        <View style={styles.totalRow}><View><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>최종 결제금액</Text><Text style={[styles.total, { color: colors.textPrimary }]}>{analysis.verified_total == null ? '확인 필요' : formatAud(analysis.verified_total)}</Text></View><View style={styles.alignRight}><Text style={[styles.summaryLabel, { color: colors.textSecondary }]}>수정 품목 합계</Text><Text style={[styles.editedTotal, { color: colors.primary }]}>{formatAud(editedTotal)}</Text></View></View>
        <View style={styles.metadataRow}><StatusBadge label={gstLabels[analysis.gst_inclusion_type]} tone={analysis.gst_inclusion_type === 'unknown' ? 'pending' : 'completed'} /><StatusBadge label={reconciliationLabels[analysis.reconciliation_status]} tone={analysis.requires_review ? 'pending' : 'completed'} /></View>
        <View style={styles.numberRow}><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="소계" onChangeText={(value) => updateAmount('subtotal', value)} value={draftAmounts.subtotal} /></View><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="GST" onChangeText={(value) => updateAmount('gst', value)} value={draftAmounts.gst} /></View></View>
        <View style={styles.numberRow}><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="할인" onChangeText={(value) => updateAmount('discount', value)} value={draftAmounts.discount} /></View><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="반올림" onChangeText={(value) => updateAmount('rounding', value)} value={draftAmounts.rounding} /></View></View>
        <View style={styles.numberRow}><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="영수증 Total" onChangeText={(value) => updateAmount('total', value)} value={draftAmounts.total} /></View><View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="Amount Paid" onChangeText={(value) => updateAmount('amount_paid', value)} value={draftAmounts.amount_paid} /></View></View>
        <View style={styles.metadataRow}><Text style={[styles.metadataValue, { color: colors.textSecondary }]}>GST {analysis.gst == null ? '미표시' : formatAud(analysis.gst)}</Text><Text style={[styles.metadataValue, { color: colors.textSecondary }]}>할인 {analysis.discount == null ? '—' : formatAud(analysis.discount)}</Text><Text style={[styles.metadataValue, { color: colors.textSecondary }]}>반올림 {analysis.rounding == null ? '—' : formatAud(analysis.rounding)}</Text><Text style={[styles.metadataValue, { color: colors.textSecondary }]}>금액 검증 {analysis.requires_review ? '확인 필요' : '완료'}</Text></View>
        <View style={[styles.validationBox, { borderColor: validationColor }]}>
          <View style={styles.validationHeading}><MaterialIcons color={validationColor} name={validationIcon} size={22} /><Text style={[styles.validationScore, { color: validationColor }]}>분석 신뢰도 {validationScore}%</Text><Text style={[styles.validationStatus, { color: validationColor }]}>{validationLabels[analysis.validation_status]}</Text></View>
          {[
            ['AI 추출', analysis.items.length > 0 && Boolean(analysis.merchant_name ?? analysis.store_name)],
            ['품목 합계 검증', itemTotalsVerified],
            ['GST 및 할인 검증', Boolean(gstAndDiscountVerified)],
            ['최종 결제금액 검증', finalTotalVerified],
          ].map(([label, verified]) => <View key={String(label)} style={styles.validationRow}><MaterialIcons color={verified ? colors.success : colors.warning} name={verified ? 'check-circle' : 'info-outline'} size={17} /><Text style={[styles.validationText, { color: colors.textSecondary }]}>{String(label)}: {verified ? '완료' : '확인 필요'}</Text></View>)}
        </View>
        <AppButton label="수정 금액 다시 검증" loading={reconciling} onPress={reconcileLatestDraft} variant="secondary" />
      </View>

      {analysis.items.length === 0 ? <EmptyState description="새 품목을 추가해 검수를 계속할 수 있습니다." icon="receipt-long" title="등록된 품목이 없습니다" /> : null}
      <View style={styles.items}>{analysis.items.map((item, index) => {
        const type = item.type ?? 'item';
        const isAdjustment = ['discount', 'coupon', 'refund', 'fee'].includes(type);
        if (isAdjustment) {
          const linkedCount = item.discount_group_id
            ? analysis.items.filter((candidate) => (candidate.type ?? 'item') === 'item' && candidate.discount_group_id === item.discount_group_id).length
            : 0;
          const amount = item.line_total ?? item.price ?? 0;
          return <View key={`${index}-${item.name}`} style={[styles.item, { backgroundColor: colors.surfaceMuted, borderColor: colors.border }]}>
            <View style={styles.modeRow}><Text style={[styles.itemTitle, { color: colors.textPrimary }]}>{item.name || '프로모션 할인'}</Text><StatusBadge label={type === 'fee' ? '추가 비용' : '할인'} tone="completed" /></View>
            <Text style={[styles.discountAmount, { color: type === 'fee' ? colors.warning : colors.primary }]}>{formatAud(amount)}</Text>
            <Text style={[styles.metadataValue, { color: colors.textSecondary }]}>{linkedCount > 0 ? `연결된 ${linkedCount}개 상품에 자동 적용` : '전체 공용 품목에 자동 적용'}</Text>
          </View>;
        }
        const shared = item.is_shared !== false;
        const participants = item.participant_user_ids ?? members.map((member) => member.user_id);
        return (
        <View key={`${index}-${item.name}`} style={[styles.item, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          <Text style={[styles.itemTitle, { color: colors.textPrimary }]}>품목 {index + 1}</Text>
          <AppTextField label="품목명" onChangeText={(name) => updateItem(index, { name })} value={item.name ?? ''} />
          <View style={styles.numberRow}>
            <View style={styles.quantity}><AppTextField keyboardType="decimal-pad" label="수량" onChangeText={(value) => updateNumber(index, 'quantity', value)} value={draftItems[index]?.quantity ?? String(item.quantity)} /></View>
            <View style={styles.price}><AppTextField keyboardType="numbers-and-punctuation" label="금액" onChangeText={(value) => updateNumber(index, 'price', value)} value={draftItems[index]?.price ?? String(item.price)} /></View>
          </View>
          <SegmentedControl options={[{ label: '개인 품목', value: 'private' }, { label: '공용 품목', value: 'shared' }]} value={shared ? 'shared' : 'private'} onChange={(value) => setShared(index, value === 'shared')} />
          {shared ? <View style={styles.participants}><Text style={[styles.itemTitle, { color: colors.textPrimary }]}>참여자</Text>{members.map((member) => { const checked = participants.includes(member.user_id); return <Pressable accessibilityRole="checkbox" accessibilityState={{ checked }} key={member.user_id} onPress={() => toggleParticipant(index, member.user_id)} style={styles.memberRow}><MaterialIcons color={checked ? colors.primary : colors.placeholder} name={checked ? 'check-box' : 'check-box-outline-blank'} size={22} /><Text numberOfLines={1} style={[styles.memberName, { color: colors.textPrimary }]}>{member.display_name}</Text></Pressable>; })}</View> : null}
          <View style={styles.actionRow}><AppButton fullWidth={false} label="품목 삭제" onPress={() => deleteItem(index)} size="compact" variant="danger" /></View>
        </View>
      );})}</View>
      <AppButton icon="add" label="품목 추가" onPress={addItem} variant="secondary" />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: Spacing.item }, warning: { alignItems: 'flex-start', borderRadius: Radius.input, flexDirection: 'row', gap: Spacing.compact, padding: Spacing.md }, warningCopy: { flex: 1, gap: Spacing.xs }, warningText: { fontSize: 12, lineHeight: 18 },
  summary: { borderRadius: Radius.card, borderWidth: 1, gap: Spacing.item, padding: Spacing.item }, modeRow: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' }, summaryLabel: { fontSize: 11, fontWeight: '700' }, divider: { height: StyleSheet.hairlineWidth },
  validationBox: { borderRadius: Radius.input, borderWidth: 1, gap: Spacing.compact, padding: Spacing.md }, validationHeading: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact }, validationScore: { flex: 1, fontSize: 16, fontWeight: '800' }, validationStatus: { fontSize: 12, fontWeight: '800' }, validationRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact }, validationText: { fontSize: 13, fontWeight: '600' },
  totalRow: { alignItems: 'flex-end', flexDirection: 'row', justifyContent: 'space-between' }, total: { fontSize: 21, fontWeight: '800', marginTop: 4 }, alignRight: { alignItems: 'flex-end' }, editedTotal: { fontSize: 17, fontWeight: '800', marginTop: 4 }, metadataRow: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.item }, metadataItem: { flex: 1, minWidth: 110 }, metadataValue: { fontSize: 13, fontWeight: '600', marginTop: 3 },
  items: { gap: Spacing.item }, item: { borderRadius: Radius.card, borderWidth: 1, gap: Spacing.item, padding: Spacing.item }, itemTitle: { flexShrink: 1, fontSize: 14, fontWeight: '800' }, discountAmount: { fontSize: 20, fontWeight: '800' }, numberRow: { flexDirection: 'row', gap: Spacing.md }, quantity: { width: 92 }, price: { flex: 1 }, participants: { gap: Spacing.compact }, memberRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.compact, minHeight: 44 }, memberName: { flex: 1, fontSize: 14 }, actionRow: { alignItems: 'center', flexDirection: 'row', justifyContent: 'flex-end' },
});
