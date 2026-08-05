import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { AppButton } from '@/components/app-button';
import { Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';

export function LoadingState({ label = '불러오는 중...' }: { label?: string }) {
  const colors = useRoomTheme();
  return <View accessibilityLiveRegion="polite" style={styles.state}><ActivityIndicator color={colors.primary} /><Text style={[styles.text, { color: colors.textSecondary }]}>{label}</Text></View>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const colors = useRoomTheme();
  return <View accessibilityLiveRegion="polite" style={styles.state}><MaterialIcons color={colors.danger} name="error-outline" size={25} /><Text style={[styles.text, { color: colors.danger }]}>{message}</Text>{onRetry ? <AppButton fullWidth={false} label="다시 시도" onPress={onRetry} size="compact" variant="secondary" /> : null}</View>;
}

const styles = StyleSheet.create({ state: { alignItems: 'center', gap: Spacing.compact, paddingVertical: Spacing.section }, text: { fontSize: 13, lineHeight: 19, textAlign: 'center' } });
