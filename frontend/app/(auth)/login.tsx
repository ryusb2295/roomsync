import { Link } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
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

export default function LoginScreen() {
  const colors = useRoomTheme();
  const { clearLogoutNotice, logoutNotice, saveAuth } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [emailTouched, setEmailTouched] = useState(false);
  const [passwordTouched, setPasswordTouched] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const normalizedEmail = email.trim().toLowerCase();
  const emailError = emailTouched && !normalizedEmail ? '이메일을 입력해주세요.' : emailTouched && !EMAIL_PATTERN.test(normalizedEmail) ? '올바른 이메일 형식으로 입력해주세요.' : null;
  const passwordError = passwordTouched && !password ? '비밀번호를 입력해주세요.' : requestError;
  const canSubmit = EMAIL_PATTERN.test(normalizedEmail) && password.length > 0 && !loading;

  const login = async () => {
    if (loading) return;
    clearLogoutNotice();
    setEmailTouched(true);
    setPasswordTouched(true);
    if (!canSubmit) return;
    setLoading(true);
    setRequestError(null);
    setNotice(null);
    try {
      const auth = await apiRequest<AuthResponse>('/auth/login', { method: 'POST', body: { email: normalizedEmail, password } });
      await saveAuth(auth);
    } catch (error) {
      if (!(error instanceof ApiError)) {
        setRequestError('로그인하지 못했습니다. 입력 정보를 확인해주세요.');
      } else if (error.status === 401) {
        setRequestError(error.message);
      } else if (error.status === 422) {
        setRequestError('입력값 형식이 올바르지 않습니다. 입력 내용을 확인해주세요.');
      } else if (error.status === 500) {
        setRequestError('서버 오류가 발생했습니다. 잠시 후 다시 시도해주세요.');
      } else if (error.kind === 'timeout') {
        setRequestError('요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.');
      } else if (error.kind === 'network') {
        setRequestError(error.message);
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
            <Text style={[Typography.screenTitle, { color: colors.textPrimary }]}>만나서 반가워요</Text>
            <Text style={[styles.description, { color: colors.textSecondary }]}>하우스 생활을 한곳에서 관리하세요</Text>
          </View>

          <View style={styles.form}>
            <AppTextField
              autoCapitalize="none"
              autoComplete="email"
              error={emailError}
              keyboardType="email-address"
              label="이메일"
              onBlur={() => setEmailTouched(true)}
              onChangeText={(value) => { setEmail(value); setRequestError(null); }}
              placeholder="name@example.com"
              returnKeyType="next"
              textContentType="emailAddress"
              value={email}
            />
            <PasswordField
              autoComplete="current-password"
              error={passwordError}
              label="비밀번호"
              onBlur={() => setPasswordTouched(true)}
              onChangeText={(value) => { setPassword(value); setRequestError(null); }}
              onSubmitEditing={login}
              placeholder="비밀번호 입력"
              returnKeyType="done"
              textContentType="password"
              value={password}
            />
            <Pressable accessibilityRole="button" onPress={() => setNotice('비밀번호 찾기는 준비 중입니다.')} style={styles.forgot}>
              <Text style={[styles.link, { color: colors.primary }]}>비밀번호를 잊으셨나요?</Text>
            </Pressable>
            {notice ? <InlineError message={notice} /> : null}
            {logoutNotice ? <InlineError message={logoutNotice} /> : null}
            <AppButton disabled={!canSubmit} label="로그인" loading={loading} onPress={login} />
          </View>

          <Text style={[styles.footer, { color: colors.textSecondary }]}>계정이 없나요? <Link href="/signup" style={[styles.link, { color: colors.primary }]}>회원가입</Link></Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 }, flex: { flex: 1 },
  content: { flexGrow: 1, paddingBottom: 32, paddingHorizontal: Layout.screenPadding, paddingTop: 30 },
  heading: { gap: Spacing.compact, marginBottom: 36, marginTop: 56 },
  description: { fontSize: 16, lineHeight: 23 },
  form: { gap: Spacing.item },
  forgot: { alignSelf: 'flex-end', minHeight: 36, justifyContent: 'center', marginTop: -8 },
  link: { fontSize: 14, fontWeight: '700' },
  footer: { fontSize: 14, marginTop: 30, textAlign: 'center' },
});
