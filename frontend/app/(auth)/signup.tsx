import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { Link } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { BrandHeader } from '@/components/brand-header';
import { InlineError } from '@/components/inline-error';
import { PasswordField } from '@/components/password-field';
import { Layout, Spacing, Typography } from '@/constants/theme';
import { useAuth } from '@/contexts/auth-context';
import { useRoomTheme } from '@/hooks/use-room-theme';
import { ApiError, apiRequest } from '@/services/api';
import type { AuthResponse } from '@/types/api';

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SignupScreen() {
  const colors = useRoomTheme();
  const { saveAuth } = useAuth();
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [touched, setTouched] = useState({ name: false, email: false, password: false, confirmation: false });
  const [requestError, setRequestError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const normalizedEmail = email.trim().toLowerCase();
  const validName = displayName.trim().length > 0;
  const validEmail = EMAIL_PATTERN.test(normalizedEmail);
  const validPassword = password.length >= 8;
  const passwordsMatch = confirmation.length > 0 && password === confirmation;
  const canSubmit = validName && validEmail && validPassword && passwordsMatch && !loading;
  const touch = (key: keyof typeof touched) => setTouched((current) => ({ ...current, [key]: true }));

  const signup = async () => {
    if (loading) return;
    setTouched({ name: true, email: true, password: true, confirmation: true });
    if (!canSubmit) return;
    setLoading(true);
    setRequestError(null);
    try {
      const auth = await apiRequest<AuthResponse>('/auth/signup', { method: 'POST', body: { display_name: displayName.trim(), email: normalizedEmail, password } });
      await saveAuth(auth);
    } catch (error) {
      if (!(error instanceof ApiError)) {
        setRequestError('회원가입하지 못했습니다.');
      } else if (error.status === 409) {
        const detail =
          error.payload && typeof error.payload === 'object' && 'detail' in error.payload
            ? error.payload.detail
            : null;
        setRequestError(typeof detail === 'string' ? detail : error.message);
      } else if (error.status === 422) {
        setRequestError('입력값 형식이 올바르지 않습니다. 입력 내용을 확인해주세요.');
      } else if (error.status === 500) {
        setRequestError('서버 오류가 발생했습니다. 잠시 후 다시 시도해주세요.');
      } else if (error.kind === 'timeout') {
        setRequestError('요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.');
      } else if (error.kind === 'network') {
        setRequestError('네트워크 오류가 발생했습니다. 인터넷 연결과 서버 주소를 확인해주세요.');
      } else {
        setRequestError(error.message);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.surface }]}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView contentContainerStyle={styles.content} keyboardDismissMode="interactive" keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
          <BrandHeader />
          <View style={styles.heading}>
            <Text style={[Typography.screenTitle, { color: colors.textPrimary }]}>함께할 준비를 해볼까요?</Text>
            <Text style={[styles.description, { color: colors.textSecondary }]}>기본 정보를 입력하면 바로 하우스를 선택할 수 있어요.</Text>
          </View>

          <View style={styles.section}>
            <Text style={[styles.step, { color: colors.primary }]}>기본 정보</Text>
            <AppTextField autoCapitalize="words" autoComplete="name" error={touched.name && !validName ? '이름을 입력해주세요.' : null} label="이름" onBlur={() => touch('name')} onChangeText={setDisplayName} placeholder="룸메이트에게 보일 이름" textContentType="name" value={displayName} />
            <AppTextField autoCapitalize="none" autoComplete="email" error={touched.email && !validEmail ? '올바른 이메일 형식으로 입력해주세요.' : null} keyboardType="email-address" label="이메일" onBlur={() => touch('email')} onChangeText={setEmail} placeholder="name@example.com" textContentType="emailAddress" value={email} />
          </View>

          <View style={styles.section}>
            <Text style={[styles.step, { color: colors.primary }]}>비밀번호 설정</Text>
            <PasswordField autoComplete="new-password" error={touched.password && !validPassword ? '비밀번호는 8자 이상이어야 합니다.' : null} label="비밀번호" onBlur={() => touch('password')} onChangeText={setPassword} placeholder="8자 이상 입력" textContentType="newPassword" value={password} />
            <View style={styles.rule}><MaterialIcons color={validPassword ? colors.success : colors.placeholder} name={validPassword ? 'check-circle' : 'radio-button-unchecked'} size={16} /><Text style={[styles.ruleText, { color: validPassword ? colors.success : colors.textSecondary }]}>8자 이상</Text></View>
            <PasswordField autoComplete="new-password" error={touched.confirmation && !passwordsMatch ? '비밀번호가 일치하지 않습니다.' : null} label="비밀번호 확인" onBlur={() => touch('confirmation')} onChangeText={setConfirmation} onSubmitEditing={signup} placeholder="비밀번호 다시 입력" returnKeyType="done" textContentType="newPassword" value={confirmation} />
            {confirmation ? <View style={styles.rule}><MaterialIcons color={passwordsMatch ? colors.success : colors.danger} name={passwordsMatch ? 'check-circle' : 'error-outline'} size={16} /><Text style={[styles.ruleText, { color: passwordsMatch ? colors.success : colors.danger }]}>{passwordsMatch ? '비밀번호가 일치합니다.' : '비밀번호가 일치하지 않습니다.'}</Text></View> : null}
          </View>

          {requestError ? <InlineError message={requestError} /> : null}
          <AppButton disabled={!canSubmit} label="회원가입" loading={loading} onPress={signup} />
          <Text style={[styles.footer, { color: colors.textSecondary }]}>이미 계정이 있나요? <Link href="/login" style={[styles.link, { color: colors.primary }]}>로그인</Link></Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 }, flex: { flex: 1 },
  content: { paddingBottom: 36, paddingHorizontal: Layout.screenPadding, paddingTop: 24 },
  heading: { gap: Spacing.compact, marginBottom: 32, marginTop: 34 },
  description: { fontSize: 15, lineHeight: 22 },
  section: { gap: Spacing.item, marginBottom: Spacing.section },
  step: { fontSize: 13, fontWeight: '800', marginBottom: -2 },
  rule: { alignItems: 'center', flexDirection: 'row', gap: 6, marginTop: -8 },
  ruleText: { fontSize: 12 },
  footer: { fontSize: 14, marginTop: Spacing.xl, textAlign: 'center' },
  link: { fontSize: 14, fontWeight: '700' },
});
