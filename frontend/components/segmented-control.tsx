import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';

type Option<T extends string> = { label: string; value: T };

export function SegmentedControl<T extends string>({ options, value, onChange }: { options: readonly Option<T>[]; value: T; onChange: (value: T) => void }) {
  const colors = useRoomTheme();
  return <View accessibilityRole="radiogroup" style={styles.row}>{options.map((option) => {
    const selected = option.value === value;
    return <Pressable accessibilityRole="radio" accessibilityState={{ selected }} key={option.value} onPress={() => onChange(option.value)} style={({ pressed }) => [styles.option, { backgroundColor: selected ? colors.primary : colors.surface, borderColor: selected ? colors.primary : colors.border }, pressed && styles.pressed]}><Text style={[styles.label, { color: selected ? colors.onPrimary : colors.textPrimary }]}>{option.label}</Text></Pressable>;
  })}</View>;
}

const styles = StyleSheet.create({ row: { flexDirection: 'row', gap: Spacing.compact }, option: { alignItems: 'center', borderRadius: Radius.button, borderWidth: 1, flex: 1, justifyContent: 'center', minHeight: 44, paddingHorizontal: Spacing.md }, label: { fontSize: 14, fontWeight: '700' }, pressed: { opacity: 0.8 } });
