import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { StyleSheet, Text, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { Radius, Spacing } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';

export function HouseContextBanner({ allowChange = true }: { allowChange?: boolean }) {
  const colors = useRoomTheme();
  const { currentHouse, houseMembers, clearHouse } = useAuth();
  if (!currentHouse) return null;
  const memberCount = houseMembers.filter((member) => member.house_id === currentHouse.id).length || currentHouse.member_count;
  return <View style={[styles.container, { backgroundColor: colors.primarySoft }]}>
    <MaterialIcons color={colors.primary} name="apartment" size={22} />
    <View style={styles.copy}><Text numberOfLines={1} style={[styles.name, { color: colors.textPrimary }]}>{currentHouse.name}</Text><Text style={[styles.meta, { color: colors.textSecondary }]}>구성원 {memberCount}명</Text></View>
    {allowChange ? <AppButton fullWidth={false} label="변경" onPress={() => void clearHouse()} size="compact" variant="tertiary" /> : null}
  </View>;
}

const styles = StyleSheet.create({ container: { alignItems: 'center', borderRadius: Radius.card, flexDirection: 'row', gap: Spacing.md, padding: Spacing.md }, copy: { flex: 1, minWidth: 0 }, name: { fontSize: 15, fontWeight: '800' }, meta: { fontSize: 12, marginTop: 2 } });
