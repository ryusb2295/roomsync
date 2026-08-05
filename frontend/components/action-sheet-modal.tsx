import type { PropsWithChildren } from 'react';
import { KeyboardAvoidingView, Modal, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppButton } from '@/components/app-button';
import { InlineError } from '@/components/inline-error';
import { Radius, Spacing } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';

type Props = PropsWithChildren<{
  visible: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  onClose: () => void;
  onConfirm: () => void;
  loading?: boolean;
  disabled?: boolean;
  error?: string | null;
  danger?: boolean;
  scrollable?: boolean;
}>;

export function ActionSheetModal({
  visible,
  title,
  description,
  confirmLabel,
  onClose,
  onConfirm,
  loading = false,
  disabled = false,
  error,
  danger = false,
  scrollable = false,
  children,
}: Props) {
  const colors = useRoomTheme();
  return (
    <Modal animationType="slide" onRequestClose={onClose} transparent visible={visible}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <Pressable accessibilityLabel="닫기" onPress={loading ? undefined : onClose} style={styles.backdrop} />
        <SafeAreaView edges={['bottom']} style={[styles.sheet, scrollable && styles.scrollSheet, { backgroundColor: colors.surface }]}>
          <View style={styles.header}>
            <View style={styles.copy}>
              <Text style={[styles.title, { color: colors.textPrimary }]}>{title}</Text>
              <Text style={[styles.description, { color: colors.textSecondary }]}>{description}</Text>
            </View>
            <Pressable accessibilityRole="button" disabled={loading} hitSlop={12} onPress={onClose}>
              <Text style={[styles.close, { color: colors.primary }]}>닫기</Text>
            </Pressable>
          </View>
          {scrollable ? (
            <ScrollView contentContainerStyle={styles.scrollContent} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator>
              {children}
            </ScrollView>
          ) : children}
          {error ? <InlineError message={error} /> : null}
          <View style={styles.actions}>
            <AppButton disabled={loading} label="취소" onPress={onClose} variant="secondary" />
            <AppButton
              disabled={disabled}
              label={confirmLabel}
              loading={loading}
              onPress={onConfirm}
              variant={danger ? 'danger' : 'primary'}
            />
          </View>
        </SafeAreaView>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, justifyContent: 'flex-end' },
  backdrop: { backgroundColor: 'rgba(17, 24, 39, 0.38)', ...StyleSheet.absoluteFillObject },
  sheet: { borderTopLeftRadius: Radius.sheet, borderTopRightRadius: Radius.sheet, gap: Spacing.item, padding: Spacing.screenHorizontal },
  scrollSheet: { flex: 1, marginTop: 48, maxHeight: '92%' },
  scrollContent: { flexGrow: 1, paddingBottom: Spacing.section },
  header: { alignItems: 'flex-start', flexDirection: 'row', gap: Spacing.item },
  copy: { flex: 1, gap: Spacing.compact },
  title: { fontSize: 22, fontWeight: '800' },
  description: { fontSize: 14, lineHeight: 21 },
  close: { fontSize: 15, fontWeight: '700' },
  actions: { gap: Spacing.compact, marginTop: Spacing.compact },
});
