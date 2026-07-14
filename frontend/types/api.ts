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
  name: string;
  quantity: number;
  price: number;
};

export type ReceiptAnalysis = {
  store_name: string;
  items: ReceiptItem[];
  total: number;
  analysis_mode: 'openai' | 'mock';
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
  user_id: number;
  name: string;
  amount: number;
  payment_status: string;
};

export type Settlement = {
  settlement_id: number;
  house_id: number;
  title: string;
  total_amount: number;
  created_by: { user_id: number; name: string } | null;
  created_at: string;
  status: string;
  is_completed: boolean;
  participants: SettlementParticipant[];
};

export type SettlementDeleteResult = {
  deleted_settlement_id: number;
  deleted_at: string;
  deleted_by: number;
};
