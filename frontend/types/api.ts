export type User = {
  id: number;
  email: string;
  display_name: string;
};

export type AuthResponse = {
  access_token: string;
  token_type: 'bearer';
  user: User;
};

export type House = {
  id: number;
  name: string;
  location: string;
  invite_code: string;
  created_by: string;
  owner_id: number | null;
  created_at: string;
  member_count: number;
};

export type HouseMember = {
  id: number;
  house_id: number;
  user_id: number;
  display_name: string;
  email: string;
  role: 'owner' | 'member' | string;
  joined_at: string;
};

export type HouseDeletionResult = {
  deleted_counts: Record<string, number>;
};

export type AccountDeletionResult = {
  deleted_solo_houses: number;
  deleted_at: string;
};

export type BlockingHouse = {
  id: number;
  name: string;
};

export type ReceiptItem = {
  receipt_item_id?: number | null;
  name: string | null;
  quantity: number | null;
  price: number | null;
  unit_price?: number | null;
  line_total?: number | null;
  amount?: number | null;
  tax_marker?: string | null;
  gst_status: 'taxable' | 'gst_free' | 'unknown';
  model_reported_confidence?: number | null;
  is_shared?: boolean;
  participant_user_ids?: number[];
  type?: 'item' | 'discount' | 'coupon' | 'refund' | 'fee' | 'tax' | 'unknown';
  applies_to_item_ids?: number[] | null;
  discount_group_id?: string | null;
};

export type ReceiptAnalysis = {
  receipt_id?: number | null;
  uploaded_by?: number | null;
  store_name: string;
  merchant_name?: string | null;
  receipt_date?: string | null;
  currency?: string | null;
  items: ReceiptItem[];
  items_total?: number | null;
  calculated_items_total?: number | null;
  subtotal?: number | null;
  tax?: number | null;
  gst?: number | null;
  discount?: number | null;
  fees?: number | null;
  rounding?: number | null;
  total: number | null;
  amount_paid?: number | null;
  gst_inclusion_type: 'included' | 'excluded_then_added' | 'not_displayed' | 'mixed' | 'unknown';
  gst_displayed: boolean;
  verified_total: number | null;
  reconciliation_status: 'verified' | 'verified_gst_included' | 'verified_gst_added' | 'mismatch' | 'needs_review';
  requires_review: boolean;
  model_reported_confidence?: number | null;
  validation_score: number;
  validation_status: 'verified' | 'mostly_verified' | 'needs_review' | 'invalid';
  validation_reasons: string[];
  warnings?: string[];
  analysis_mode: 'gemini' | 'openai' | 'mock' | 'edited';
  warning: string | null;
};

export type Chore = {
  id: number;
  house_id: number;
  title: string;
  description: string;
  assignee_user_id: number;
  scheduled_date: string;
  is_completed: boolean;
  status: string;
  completed_at: string | null;
  created_by: number | null;
  created_at: string;
  assignee: {
    user_id: number;
    display_name: string;
  };
};

export type ShoppingItem = {
  id: number;
  house_id: number;
  item_name: string;
  added_by: string;
  status: string;
  is_completed: boolean;
};

export type ShoppingItemDeleteResult = {
  deleted_item_id: number;
};

export type ShoppingItemsBulkDeleteResult = {
  deleted_count: number;
};

export type SettlementParticipant = {
  id?: number | null;
  user_id: number;
  name: string;
  amount: number;
  display_name: string;
  share_amount_cents: number;
  role: 'payer' | 'participant';
  payment_status: 'payer' | 'unpaid' | 'paid';
  paid_at?: string | null;
  confirmed_by?: number | null;
};

export type Settlement = {
  settlement_id: number;
  house_id: number;
  title: string;
  total_amount: number;
  total_amount_cents: number;
  receipt_id: number | null;
  payer_id: number | null;
  payer_name: string | null;
  uploaded_by: number | null;
  uploaded_by_name: string | null;
  receipt_date: string | null;
  created_by: { user_id: number; name: string } | null;
  created_at: string;
  completed_at?: string | null;
  status: 'in_progress' | 'completed';
  is_completed: boolean;
  participants: SettlementParticipant[];
};

export type SettlementPaymentStatusResult = {
  settlement_id: number;
  participant_user_id: number;
  payment_status: 'paid' | 'unpaid';
  settlement_status: 'in_progress' | 'completed';
  paid_at: string | null;
  completed_at: string | null;
};

export type SettlementDeleteResult = {
  deleted_settlement_id: number;
  deleted_at: string;
  deleted_by: number;
};
