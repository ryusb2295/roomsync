import type { HouseMember } from '@/types/api';

export type DemoPaymentStatus = 'paid' | 'unpaid';

export type DemoParticipant = {
  userId: number;
  name: string;
  role: string;
  amount: number;
  paymentStatus: DemoPaymentStatus;
};

export type DemoReceiptItem = {
  id: string;
  name: string;
  amount: number;
  isShared: boolean;
  participantUserIds: number[];
};

export type DemoSettlement = {
  id: string;
  title: string;
  totalAmount: number;
  status: 'active' | 'completed';
  createdAt: string;
  payerUserId: number;
  createdByUserId: number;
  participants: DemoParticipant[];
  items: DemoReceiptItem[];
};

type DemoDefinition = {
  id: string;
  title: string;
  totalCents: number;
  status: 'active' | 'completed';
  daysAgo: number;
  items: Array<[string, number, 'all' | 'first-two' | 'current-user']>;
};

const DEFINITIONS: DemoDefinition[] = [
  {
    id: 'demo-settlement-coles',
    title: 'Coles 공용품 정산',
    totalCents: 2710,
    status: 'active',
    daysAgo: 0,
    items: [
      ['Milk', 340, 'all'],
      ['Eggs', 620, 'first-two'],
      ['Toilet Paper', 1200, 'all'],
      ['Dishwashing Liquid', 550, 'current-user'],
    ],
  },
  {
    id: 'demo-settlement-woolworths',
    title: 'Woolworths 생활용품 정산',
    totalCents: 3650,
    status: 'completed',
    daysAgo: 2,
    items: [
      ['Rice', 800, 'all'],
      ['Laundry Detergent', 1450, 'first-two'],
      ['Garbage Bags', 600, 'current-user'],
      ['Paper Towel', 800, 'all'],
    ],
  },
  {
    id: 'demo-settlement-aldi',
    title: 'ALDI 청소용품 정산',
    totalCents: 1270,
    status: 'active',
    daysAgo: 5,
    items: [
      ['Cleaning Spray', 550, 'all'],
      ['Sponges', 320, 'first-two'],
      ['Hand Soap', 400, 'current-user'],
    ],
  },
];

export function splitAmount(totalCents: number, memberCount: number): number[] {
  if (memberCount <= 0) return [];
  const base = Math.floor(totalCents / memberCount);
  const amounts = Array.from({ length: memberCount }, () => base);
  amounts[memberCount - 1] += totalCents - base * memberCount;
  return amounts;
}

function itemParticipants(
  members: HouseMember[],
  currentUserId: number | undefined,
  strategy: 'all' | 'first-two' | 'current-user'
): number[] {
  if (strategy === 'all') return members.map((member) => member.user_id);
  if (strategy === 'first-two') return members.slice(0, 2).map((member) => member.user_id);
  const currentMember = members.find((member) => member.user_id === currentUserId);
  return currentMember ? [currentMember.user_id] : members.slice(0, 1).map((member) => member.user_id);
}

export function createDemoSettlements(
  members: HouseMember[],
  currentUserId?: number,
  now = new Date()
): DemoSettlement[] {
  if (members.length === 0) return [];

  return DEFINITIONS.map((definition) => {
    const amounts = splitAmount(definition.totalCents, members.length);
    const createdAt = new Date(now);
    createdAt.setDate(createdAt.getDate() - definition.daysAgo);
    return {
      id: definition.id,
      title: definition.title,
      totalAmount: definition.totalCents / 100,
      status: definition.status,
      createdAt: createdAt.toISOString(),
      payerUserId: members[0].user_id,
      createdByUserId: members.some((member) => member.user_id === currentUserId)
        ? currentUserId as number
        : members[0].user_id,
      participants: members.map((member, index) => ({
        userId: member.user_id,
        name: member.display_name,
        role: member.role,
        amount: amounts[index] / 100,
        paymentStatus:
          definition.status === 'completed' || index % 2 === 0 ? 'paid' : 'unpaid',
      })),
      items: definition.items.map(([name, cents, strategy], index) => ({
        id: `${definition.id}-item-${index + 1}`,
        name,
        amount: cents / 100,
        isShared: strategy !== 'current-user',
        participantUserIds: itemParticipants(members, currentUserId, strategy),
      })),
    };
  });
}

export function withDerivedStatus(settlement: DemoSettlement): DemoSettlement {
  const completed = settlement.participants.length > 0
    && settlement.participants.every((participant) => participant.paymentStatus === 'paid');
  return { ...settlement, status: completed ? 'completed' : 'active' };
}
