import * as Clipboard from 'expo-clipboard';
import type { ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { StyleSheet, Switch, Text, View } from 'react-native';

import { ActionSheetModal } from '@/components/action-sheet-modal';
import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { EmptyState } from '@/components/empty-state';
import { InlineError } from '@/components/inline-error';
import { ListRow } from '@/components/list-row';
import { PasswordField } from '@/components/password-field';
import { ScreenContainer } from '@/components/screen-container';
import { StatusBadge } from '@/components/status-badge';
import { Radius, Spacing } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { ApiError, apiRequest } from '@/services/api';
import type { AccountDeletionResult, BlockingHouse, House, HouseDeletionResult } from '@/types/api';

type Action = 'leave' | 'transfer' | 'delete-house' | 'delete-account' | null;

const impactLabels: Record<string, string> = {
  house_members: '멤버십',
  receipts: '영수증',
  receipt_items: '영수증 품목',
  settlements: '정산 내역',
  settlement_details: '정산 상세',
  settlement_participants: '정산 참여자',
  chores: '청소 일정',
  shopping_items: '장바구니 품목',
};

function blockingHousesFrom(error: ApiError): BlockingHouse[] {
  const payload = error.payload;
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return [];
  const detail = payload.detail;
  if (!detail || typeof detail !== 'object' || !('blocking_houses' in detail)) return [];
  return Array.isArray(detail.blocking_houses) ? (detail.blocking_houses as BlockingHouse[]) : [];
}

export default function SettingsScreen() {
  const colors = useRoomTheme();
  const {
    currentHouse,
    user,
    token,
    houseMembers,
    membersLoading,
    membersError,
    refreshHouseMembers,
    selectHouse,
    clearHouse,
    clearLocalSession,
    logout,
  } = useAuth();
  const [houseDetails, setHouseDetails] = useState(currentHouse);
  const [houseError, setHouseError] = useState<string | null>(null);
  const [notificationsEnabled, setNotificationsEnabled] = useState(true);
  const [copySuccess, setCopySuccess] = useState(false);
  const [action, setAction] = useState<Action>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [newOwnerId, setNewOwnerId] = useState<number | null>(null);
  const [houseConfirmation, setHouseConfirmation] = useState('');
  const [accountPassword, setAccountPassword] = useState('');
  const [accountConfirmation, setAccountConfirmation] = useState('');
  const [blockingHouses, setBlockingHouses] = useState<BlockingHouse[]>([]);
  const [deletionImpact, setDeletionImpact] = useState<Record<string, number> | null>(null);

  const currentMembership = houseMembers.find((member) => member.user_id === user?.id);
  const isOwner = Boolean(
    user && houseDetails?.owner_id === user.id && currentMembership?.role === 'owner'
  );
  const transferCandidates = useMemo(
    () => houseMembers.filter((member) => member.user_id !== user?.id),
    [houseMembers, user?.id]
  );

  const loadHouseDetails = useCallback(async () => {
    if (!token || !currentHouse) return;
    setHouseError(null);
    try {
      setHouseDetails(await apiRequest<House>(`/houses/${currentHouse.id}`, { token }));
    } catch (error) {
      setHouseError(error instanceof Error ? error.message : '하우스 정보를 불러오지 못했습니다.');
    }
  }, [currentHouse, token]);

  useEffect(() => { loadHouseDetails(); }, [loadHouseDetails]);

  const closeAction = () => {
    if (working) return;
    setAction(null);
    setActionError(null);
    setNewOwnerId(null);
    setHouseConfirmation('');
    setAccountPassword('');
    setAccountConfirmation('');
    setBlockingHouses([]);
    setDeletionImpact(null);
  };

  const copyInviteCode = async () => {
    if (!houseDetails?.invite_code) return;
    await Clipboard.setStringAsync(houseDetails.invite_code);
    setCopySuccess(true);
  };

  const openHouseDeletion = async () => {
    if (!token || !currentHouse) return;
    setAction('delete-house');
    setActionError(null);
    setDeletionImpact(null);
    try {
      const result = await apiRequest<HouseDeletionResult>(
        `/houses/${currentHouse.id}/deletion-impact`, { token }
      );
      setDeletionImpact(result.deleted_counts);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '삭제 영향을 확인하지 못했습니다.');
    }
  };

  const openAccountDeletion = async () => {
    if (!token || !user) return;
    setAction('delete-account');
    setActionError(null);
    setBlockingHouses([]);
    try {
      const houses = await apiRequest<House[]>('/houses', { token });
      setBlockingHouses(
        houses
          .filter((house) => house.owner_id === user.id && house.member_count > 1)
          .map((house) => ({ id: house.id, name: house.name }))
      );
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '소유한 하우스를 확인하지 못했습니다.');
    }
  };

  const leaveHouse = async () => {
    if (!token || !currentHouse || working) return;
    setWorking(true);
    setActionError(null);
    try {
      await apiRequest<null>(`/houses/${currentHouse.id}/leave`, { method: 'POST', token });
      setAction(null);
      await clearHouse();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '하우스에서 나가지 못했습니다.');
    } finally {
      setWorking(false);
    }
  };

  const transferOwner = async () => {
    if (!token || !currentHouse || !newOwnerId || working) return;
    setWorking(true);
    setActionError(null);
    try {
      const updated = await apiRequest<House>(`/houses/${currentHouse.id}/owner`, {
        method: 'PATCH', token, body: { new_owner_user_id: newOwnerId },
      });
      await selectHouse(updated);
      setHouseDetails(updated);
      await refreshHouseMembers();
      setAction(null);
      setActionError(null);
      setNewOwnerId(null);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '소유권을 이전하지 못했습니다.');
    } finally {
      setWorking(false);
    }
  };

  const deleteHouse = async () => {
    if (!token || !currentHouse || working) return;
    setWorking(true);
    setActionError(null);
    try {
      await apiRequest<HouseDeletionResult>(`/houses/${currentHouse.id}`, {
        method: 'DELETE', token, body: { confirmation: houseConfirmation },
      });
      setAction(null);
      await clearHouse();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '하우스를 삭제하지 못했습니다.');
    } finally {
      setWorking(false);
    }
  };

  const deleteAccount = async () => {
    if (!token || working || blockingHouses.length > 0) return;
    setWorking(true);
    setActionError(null);
    try {
      await apiRequest<AccountDeletionResult>('/auth/me', {
        method: 'DELETE',
        token,
        body: { password: accountPassword, confirmation: accountConfirmation },
      });
      setAction(null);
      await clearLocalSession();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setBlockingHouses(blockingHousesFrom(error));
      }
      setActionError(error instanceof Error ? error.message : '계정을 삭제하지 못했습니다.');
    } finally {
      setWorking(false);
    }
  };

  const section = (title: string, children: ReactNode) => (
    <View style={styles.section}>
      <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>{title}</Text>
      <View style={[styles.group, { backgroundColor: colors.surface, borderColor: colors.border }]}>{children}</View>
    </View>
  );
  const divider = <View style={[styles.divider, { backgroundColor: colors.divider }]} />;

  return (
    <ScreenContainer keyboardAware subtitle="하우스와 계정 환경을 안전하게 관리하세요." title="설정">
      {section('하우스 정보', <>
        <ListRow icon="apartment" subtitle={`${houseDetails?.location ?? ''} · 구성원 ${houseDetails?.member_count ?? houseMembers.length}명`} title={houseDetails?.name ?? '현재 하우스'} />
        {divider}
        <View style={styles.inviteRow}>
          <View style={styles.inviteCopy}>
            <Text style={[styles.inviteLabel, { color: colors.textSecondary }]}>초대코드</Text>
            <Text selectable style={[styles.inviteCode, { color: colors.textPrimary }]}>{houseDetails?.invite_code}</Text>
            {copySuccess ? <Text style={[styles.successText, { color: colors.success }]}>복사했습니다</Text> : null}
          </View>
          <AppButton fullWidth={false} icon="content-copy" label="복사" onPress={copyInviteCode} variant="secondary" />
        </View>
        {divider}
        <ListRow icon="swap-horiz" onPress={clearHouse} subtitle="다른 하우스를 선택합니다" title="하우스 변경" />
      </>)}
      {houseError ? <View style={styles.feedback}><InlineError message={houseError} /><AppButton label="하우스 정보 다시 불러오기" onPress={loadHouseDetails} variant="secondary" /></View> : null}

      <View style={styles.section}>
        <View style={styles.memberHeader}>
          <Text style={[styles.sectionTitle, { color: colors.textSecondary }]}>구성원</Text>
          <AppButton fullWidth={false} label="새로고침" onPress={refreshHouseMembers} variant="tertiary" />
        </View>
        {membersError ? <View style={styles.feedback}><InlineError message={membersError} /><AppButton label="다시 시도" onPress={refreshHouseMembers} variant="secondary" /></View> : membersLoading ? <Text style={[styles.loadingText, { color: colors.textSecondary }]}>멤버를 불러오는 중...</Text> : houseMembers.length === 0 ? <EmptyState description="현재 확인할 수 있는 멤버가 없습니다." icon="group" title="구성원이 없어요" /> : (
          <View style={[styles.group, { backgroundColor: colors.surface, borderColor: colors.border }]}>{houseMembers.map((member, index) => <View key={member.id}><ListRow icon="person-outline" subtitle={member.email} title={member.display_name} trailing={<StatusBadge label={member.role === 'owner' ? '소유자' : '멤버'} tone={member.role === 'owner' ? 'info' : 'completed'} />} />{index < houseMembers.length - 1 ? divider : null}</View>)}</View>
        )}
      </View>

      {section('하우스 관리', isOwner ? <>
        <ListRow icon="manage-accounts" onPress={() => setAction('transfer')} subtitle="현재 멤버에게 소유자 권한을 넘깁니다" title="소유권 이전" />
        {divider}
        <ListRow destructive icon="delete-outline" onPress={openHouseDeletion} subtitle="모든 하우스 데이터를 영구 삭제합니다" title="하우스 삭제" />
      </> : <ListRow destructive icon="exit-to-app" onPress={() => setAction('leave')} subtitle="하우스 데이터는 삭제되지 않습니다" title="하우스 나가기" />)}

      {section('앱 설정', <><ListRow icon="language" subtitle="한국어" title="언어" />{divider}<ListRow icon="notifications-none" subtitle="현재 기기에만 적용됩니다" title="알림" trailing={<Switch accessibilityLabel="알림 사용" ios_backgroundColor={colors.disabled} onValueChange={setNotificationsEnabled} trackColor={{ false: colors.disabled, true: colors.primary }} value={notificationsEnabled} />} /></>)}
      {section('계정 관리', <><ListRow icon="person-outline" subtitle={user?.email} title={user?.display_name ?? '내 계정'} />{divider}<ListRow icon="logout" onPress={logout} title="로그아웃" />{divider}<ListRow destructive icon="person-remove" onPress={openAccountDeletion} subtitle="공유 기록은 탈퇴한 사용자로 보존됩니다" title="계정 삭제" /></>)}

      <ActionSheetModal confirmLabel="하우스 나가기" danger description="멤버십만 제거되며 하우스의 일정과 내역은 유지됩니다." disabled={working} error={actionError} loading={working} onClose={closeAction} onConfirm={leaveHouse} title="하우스에서 나갈까요?" visible={action === 'leave'}>
        <Text style={[styles.guidance, { color: colors.textSecondary }]}>나간 뒤에는 초대코드로 다시 참여해야 접근할 수 있습니다.</Text>
      </ActionSheetModal>

      <ActionSheetModal confirmLabel="소유권 이전" description="새 소유자가 하우스 삭제와 다음 소유권 이전 권한을 갖습니다." disabled={!newOwnerId || working} error={actionError} loading={working} onClose={closeAction} onConfirm={transferOwner} title="새 소유자를 선택하세요" visible={action === 'transfer'}>
        {transferCandidates.length === 0 ? <InlineError message="소유권을 이전할 다른 멤버가 없습니다." /> : transferCandidates.map((member) => (
          <AppButton key={member.user_id} label={`${newOwnerId === member.user_id ? '선택됨 · ' : ''}${member.display_name}`} onPress={() => setNewOwnerId(member.user_id)} variant={newOwnerId === member.user_id ? 'primary' : 'secondary'} />
        ))}
      </ActionSheetModal>

      <ActionSheetModal confirmLabel="하우스 영구 삭제" danger description="하우스를 삭제하면 모든 멤버가 즉시 접근할 수 없습니다." disabled={!deletionImpact || houseConfirmation !== houseDetails?.name || working} error={actionError} loading={working} onClose={closeAction} onConfirm={deleteHouse} title="하우스를 삭제할까요?" visible={action === 'delete-house'}>
        <View style={[styles.impactBox, { backgroundColor: colors.background, borderColor: colors.border }]}>
          <Text style={[styles.impactTitle, { color: colors.textPrimary }]}>삭제되는 데이터</Text>
          {deletionImpact ? Object.entries(deletionImpact).map(([key, count]) => <Text key={key} style={[styles.impactText, { color: colors.textSecondary }]}>• {impactLabels[key] ?? key} {count}개</Text>) : <Text style={[styles.impactText, { color: colors.textSecondary }]}>영향을 확인하는 중...</Text>}
        </View>
        <AppTextField autoCapitalize="none" autoCorrect={false} error={houseConfirmation && houseConfirmation !== houseDetails?.name ? '하우스 이름이 정확히 일치해야 합니다.' : null} label={`확인을 위해 “${houseDetails?.name ?? ''}” 입력`} onChangeText={setHouseConfirmation} value={houseConfirmation} />
      </ActionSheetModal>

      <ActionSheetModal confirmLabel="계정 영구 삭제" danger description="로그인 정보와 멤버십은 제거되지만, 다른 멤버에게 필요한 정산 기록은 ‘탈퇴한 사용자’로 보존됩니다." disabled={!accountPassword || accountConfirmation !== 'DELETE' || blockingHouses.length > 0 || working} error={actionError} loading={working} onClose={closeAction} onConfirm={deleteAccount} title="계정을 삭제할까요?" visible={action === 'delete-account'}>
        {blockingHouses.length > 0 ? <View style={[styles.blockingBox, { backgroundColor: colors.dangerSoft }]}><Text style={[styles.blockingTitle, { color: colors.danger }]}>먼저 처리할 하우스</Text>{blockingHouses.map((house) => <Text key={house.id} style={[styles.impactText, { color: colors.textPrimary }]}>• {house.name}: 소유권 이전 또는 하우스 삭제 필요</Text>)}</View> : null}
        <PasswordField autoCapitalize="none" autoCorrect={false} label="비밀번호 재입력" onChangeText={setAccountPassword} value={accountPassword} />
        <AppTextField autoCapitalize="characters" autoCorrect={false} error={accountConfirmation && accountConfirmation !== 'DELETE' ? 'DELETE를 정확히 입력해주세요.' : null} label="확인 문구 DELETE 입력" onChangeText={(value) => setAccountConfirmation(value.trim().toUpperCase())} value={accountConfirmation} />
      </ActionSheetModal>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  section: { gap: Spacing.compact },
  sectionTitle: { fontSize: 12, fontWeight: '700', paddingHorizontal: 4, textTransform: 'uppercase' },
  group: { borderRadius: Radius.card, borderWidth: 1, paddingHorizontal: Spacing.item },
  divider: { height: StyleSheet.hairlineWidth, marginLeft: 52 },
  inviteRow: { alignItems: 'center', flexDirection: 'row', gap: Spacing.item, minHeight: 84, paddingVertical: Spacing.compact },
  inviteCopy: { flex: 1, gap: 3 },
  inviteLabel: { fontSize: 12, fontWeight: '600' },
  inviteCode: { fontSize: 20, fontWeight: '800', letterSpacing: 1.5 },
  successText: { fontSize: 11, fontWeight: '700' },
  memberHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' },
  feedback: { gap: Spacing.item },
  loadingText: { fontSize: 13, paddingVertical: Spacing.item, textAlign: 'center' },
  guidance: { fontSize: 14, lineHeight: 21 },
  impactBox: { borderRadius: Radius.input, borderWidth: 1, gap: 5, padding: Spacing.item },
  impactTitle: { fontSize: 14, fontWeight: '700', marginBottom: 2 },
  impactText: { fontSize: 13, lineHeight: 19 },
  blockingBox: { borderRadius: Radius.input, gap: 5, padding: Spacing.item },
  blockingTitle: { fontSize: 14, fontWeight: '800', marginBottom: 2 },
});
