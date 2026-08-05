import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';

export function FeedbackBanner({ message }: { message: string }) {
  const colors = useRoomTheme();
  return <View accessibilityLiveRegion="polite" style={[styles.container, { backgroundColor: colors.successSoft }]}><MaterialIcons color={colors.success} name="check-circle" size={20} /><Text style={[styles.text, { color: colors.success }]}>{message}</Text></View>;
}

const styles = StyleSheet.create({ container: { alignItems: 'center', borderRadius: Radius.input, flexDirection: 'row', gap: Spacing.compact, padding: Spacing.md }, text: { flex: 1, fontSize: 13, fontWeight: '700', lineHeight: 19 } });
