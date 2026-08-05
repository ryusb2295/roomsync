import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';

export function BrandHeader() {
  const colors = useRoomTheme();
  return <View accessibilityLabel="RoomSync" style={styles.container}>
    <View style={[styles.mark, { backgroundColor: colors.primary }]}><Text style={styles.initial}>R</Text></View>
    <View style={styles.copy}><Text style={[styles.name, { color: colors.textPrimary }]}>RoomSync</Text><Text style={[styles.tagline, { color: colors.textSecondary }]}>쉐어하우스 생활을 한곳에서 관리하세요</Text></View>
  </View>;
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', flexDirection: 'row', gap: Spacing.md }, mark: { alignItems: 'center', borderRadius: Radius.input, height: 44, justifyContent: 'center', width: 44 }, initial: { color: '#FFFFFF', fontSize: 23, fontWeight: '900' }, copy: { flex: 1, gap: 2 }, name: { fontSize: 21, fontWeight: '900' }, tagline: { fontSize: 12, lineHeight: 17 },
});
