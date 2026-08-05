import type { ReceiptAnalysis, ReceiptItem } from '@/types/api';

export type SharedReceiptItemPayload = {
  receipt_item_id: number | null;
  name: string | null;
  line_total: number;
  amount_cents: number;
  participant_user_ids: number[];
  type: NonNullable<ReceiptItem['type']>;
  applies_to_item_ids: number[] | null;
  discount_group_id: string | null;
};

export type SharedSettlementSummary = {
  total: number;
  sharedItemCount: number;
  participantIds: number[];
  receiptItems: SharedReceiptItemPayload[];
  warnings: string[];
  hasParticipantlessItem: boolean;
};

const VERIFIED_RECONCILIATION_STATUSES = new Set([
  'verified', 'verified_gst_included', 'verified_gst_added',
]);

export function normalizeReceiptItemAmount(item: ReceiptItem, index: number): number | null {
  const candidates: unknown[] = [
    item.line_total,
    item.amount,
    item.price,
    (item as ReceiptItem & { total_price?: unknown }).total_price,
  ];
  const rawValue = candidates.find((value) => value !== null && value !== undefined && value !== '');
  const normalized = typeof rawValue === 'string'
    ? rawValue.replace(/AUD/gi, '').replace(/[$,]/g, '').trim()
    : rawValue;
  const amount = typeof normalized === 'number' ? normalized : Number(normalized);
  const type = item.type ?? 'item';
  const validAdjustment = ['discount', 'coupon', 'refund'].includes(type) && amount <= 0;
  const validFee = type === 'fee' && Number.isFinite(amount);
  const validItem = !['discount', 'coupon', 'refund', 'fee', 'tax'].includes(type) && amount > 0;
  if (!Number.isFinite(amount) || (!validAdjustment && !validFee && !validItem)) {
    if (__DEV__) console.warn('[Settlement] invalid item amount:', { index, name: item.name, rawValue });
    return null;
  }
  return Math.round(amount * 100) / 100;
}

export function buildSharedSettlementSummary(analysis: ReceiptAnalysis): SharedSettlementSummary {
  const warnings: string[] = [];
  const receiptItems: SharedReceiptItemPayload[] = [];
  const participantIds = new Set<number>();
  let totalCents = 0;
  let hasParticipantlessItem = false;

  analysis.items.forEach((item, index) => {
    const type = item.type ?? 'item';
    if (type === 'tax') return;
    const isAdjustment = ['discount', 'coupon', 'refund', 'fee'].includes(type);
    if (!isAdjustment && item.is_shared === false) return;
    const lineTotal = normalizeReceiptItemAmount(item, index);
    const participants = isAdjustment ? [] : [...new Set(item.participant_user_ids ?? [])].sort((a, b) => a - b);
    if (lineTotal === null) {
      warnings.push(`품목 ${index + 1}의 금액을 확인할 수 없습니다.`);
      return;
    }
    if (!isAdjustment && participants.length === 0) {
      hasParticipantlessItem = true;
      warnings.push(`품목 ${index + 1} '${item.name || '이름 없음'}'의 참여자를 선택해주세요.`);
      return;
    }
    totalCents += Math.round(lineTotal * 100);
    participants.forEach((userId) => participantIds.add(userId));
    receiptItems.push({
      receipt_item_id: item.receipt_item_id ?? null,
      name: item.name,
      line_total: lineTotal,
      amount_cents: Math.round(lineTotal * 100),
      participant_user_ids: participants,
      type,
      applies_to_item_ids: item.applies_to_item_ids ?? null,
      discount_group_id: item.discount_group_id ?? null,
    });
  });

  return {
    total: totalCents / 100,
    sharedItemCount: analysis.items.filter((item) => !['discount', 'coupon', 'refund', 'fee', 'tax'].includes(item.type ?? 'item') && item.is_shared !== false).length,
    participantIds: [...participantIds].sort((a, b) => a - b),
    receiptItems,
    warnings,
    hasParticipantlessItem,
  };
}

export function getSettlementSaveDisabledReason(
  analysis: ReceiptAnalysis | null,
  summary: SharedSettlementSummary | null,
  hasSessionAndHouse: boolean,
): string | null {
  if (!hasSessionAndHouse) return '하우스 정보를 불러오지 못했습니다.';
  if (!analysis?.verified_total || !VERIFIED_RECONCILIATION_STATUSES.has(analysis.reconciliation_status)) return '금액을 다시 검증해 주세요.';
  if (!summary || summary.sharedItemCount === 0) return '정산할 공용 품목이 없습니다.';
  if (summary.hasParticipantlessItem || summary.warnings.length > 0) return '공용 품목의 금액과 참여자를 확인해 주세요.';
  if (summary.participantIds.length === 0) return '공용 품목의 참여자를 선택해 주세요.';
  return null;
}
