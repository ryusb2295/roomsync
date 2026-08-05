import MaterialIcons from '@expo/vector-icons/MaterialIcons';
import { useEffect, useMemo, useState } from 'react';
import {
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { AppButton } from '@/components/app-button';
import { AppTextField } from '@/components/app-text-field';
import { InlineError } from '@/components/inline-error';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useRoomTheme } from '@/hooks/use-room-theme';
import type { HouseMember } from '@/types/api';
import { formatKoreanDate } from '@/utils/date';

type ChoreDraft = {
  title: string;
  description: string;
  assigneeUserId: number;
  scheduledDate: string;
};

type Props = {
  visible: boolean;
  members: HouseMember[];
  initialAssigneeUserId?: number;
  loading: boolean;
  error?: string | null;
  initialDraft?: ChoreDraft;
  onClose: () => void;
  onSubmit: (draft: ChoreDraft) => void;
};

function dateKey(date = new Date()): string {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

function isValidDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00`);
  return !Number.isNaN(parsed.getTime()) && dateKey(parsed) === value;
}

export function ChoreFormModal({
  visible,
  members,
  initialAssigneeUserId,
  loading,
  error,
  initialDraft,
  onClose,
  onSubmit,
}: Props) {
  const colors = useRoomTheme();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [assigneeUserId, setAssigneeUserId] = useState<number | null>(null);
  const [scheduledDate, setScheduledDate] = useState(dateKey());
  const [dateTouched, setDateTouched] = useState(false);
  const [calendarVisible, setCalendarVisible] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const safeMembers = useMemo(() => Array.isArray(members) ? members : [], [members]);

  useEffect(() => {
    if (!visible) return;
    if (__DEV__) console.log('[cleaning] formState before initialize:', { members, initialDraft, initialAssigneeUserId });
    setTitle(initialDraft?.title ?? '');
    setDescription(initialDraft?.description ?? '');
    setAssigneeUserId(
      initialDraft?.assigneeUserId && safeMembers.some((member) => member.user_id === initialDraft.assigneeUserId)
        ? initialDraft.assigneeUserId
        : initialAssigneeUserId && safeMembers.some((member) => member.user_id === initialAssigneeUserId)
          ? initialAssigneeUserId
        : safeMembers[0]?.user_id ?? null
    );
    const nextDate = initialDraft?.scheduledDate && isValidDate(initialDraft.scheduledDate)
      ? initialDraft.scheduledDate
      : dateKey();
    setScheduledDate(nextDate);
    const [year, month] = nextDate.split('-').map(Number);
    setCalendarMonth(new Date(year, month - 1, 1));
    setCalendarVisible(false);
    setDateTouched(false);
  }, [initialAssigneeUserId, initialDraft, members, safeMembers, visible]);

  const validDate = isValidDate(scheduledDate);
  const canSubmit = title.trim().length > 0 && assigneeUserId !== null && validDate && !loading;

  const calendarDays = Array.from({ length: 42 }, (_, index) => {
    const firstDayOffset = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), 1).getDay();
    return new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), index - firstDayOffset + 1);
  });

  const selectDate = (date: Date) => {
    setScheduledDate(dateKey(date));
    setCalendarMonth(new Date(date.getFullYear(), date.getMonth(), 1));
    setDateTouched(true);
    setCalendarVisible(false);
  };

  return (
    <Modal animationType="slide" onRequestClose={onClose} presentationStyle="pageSheet" visible={visible}>
      <SafeAreaView style={[styles.safeArea, { backgroundColor: colors.background }]}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>
            <View style={styles.header}>
              <View style={styles.headerCopy}>
                <Text style={[Typography.screenTitle, { color: colors.textPrimary }]}>{initialDraft ? '청소 일정 수정' : '청소 일정 추가'}</Text>
                <Text style={[styles.subtitle, { color: colors.textSecondary }]}>현재 하우스에 새 담당 일정을 등록합니다.</Text>
              </View>
              <Pressable accessibilityLabel="닫기" accessibilityRole="button" disabled={loading} onPress={onClose} style={styles.close}>
                <MaterialIcons color={colors.textSecondary} name="close" size={25} />
              </Pressable>
            </View>

            <View style={styles.form}>
              <AppTextField label="청소 항목명" maxLength={100} onChangeText={setTitle} placeholder="예: 욕실 청소" value={title} />
              <AppTextField label="설명 또는 메모" maxLength={500} multiline onChangeText={setDescription} placeholder="청소 범위나 주의사항을 입력하세요" style={styles.memoInput} textAlignVertical="top" value={description} />

              <View style={styles.fieldGroup}>
                <Text style={[styles.label, { color: colors.textPrimary }]}>담당자</Text>
                {safeMembers.map((member) => {
                  const selected = member.user_id === assigneeUserId;
                  return (
                    <Pressable
                      key={member.id}
                      accessibilityRole="radio"
                      accessibilityState={{ selected }}
                      onPress={() => setAssigneeUserId(member.user_id)}
                      style={({ pressed }) => [
                        styles.memberRow,
                        { backgroundColor: selected ? colors.primarySoft : colors.surface, borderColor: selected ? colors.primary : colors.border },
                        pressed && styles.pressed,
                      ]}>
                      <MaterialIcons color={selected ? colors.primary : colors.placeholder} name={selected ? 'radio-button-checked' : 'radio-button-unchecked'} size={22} />
                      <View style={styles.memberCopy}>
                        <Text style={[styles.memberName, { color: colors.textPrimary }]}>{member.display_name}</Text>
                        <Text style={[styles.memberRole, { color: colors.textSecondary }]}>{member.role === 'owner' ? '관리자' : '멤버'}</Text>
                      </View>
                    </Pressable>
                  );
                })}
                {loading ? <Text style={[styles.memberRole, { color: colors.textSecondary }]}>하우스 멤버를 불러오는 중...</Text> : safeMembers.length === 0 ? <InlineError message="선택할 수 있는 하우스 멤버가 없습니다." /> : null}
              </View>

              <View style={styles.fieldGroup}>
                <Text style={[styles.label, { color: colors.textPrimary }]}>예정일</Text>
                <Pressable
                  accessibilityLabel="청소 예정일 선택"
                  accessibilityRole="button"
                  onPress={() => setCalendarVisible((current) => !current)}
                  style={({ pressed }) => [styles.dateField, { backgroundColor: colors.surface, borderColor: colors.border }, pressed && styles.pressed]}>
                  <MaterialIcons color={colors.primary} name="calendar-month" size={23} />
                  <View style={styles.memberCopy}><Text style={[styles.dateValue, { color: colors.textPrimary }]}>{validDate ? formatKoreanDate(scheduledDate, '날짜를 선택해주세요') : '날짜를 선택해주세요'}</Text></View>
                  <MaterialIcons color={colors.textSecondary} name={calendarVisible ? 'expand-less' : 'expand-more'} size={24} />
                </Pressable>
                {dateTouched && !validDate ? <InlineError message="올바른 날짜를 선택해주세요." /> : null}
                {calendarVisible ? <View style={[styles.calendar, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                  <View style={styles.calendarHeader}><Pressable hitSlop={10} onPress={() => setCalendarMonth((current) => new Date(current.getFullYear(), current.getMonth() - 1, 1))} style={styles.calendarNav}><MaterialIcons color={colors.primary} name="chevron-left" size={26} /></Pressable><Text style={[styles.calendarTitle, { color: colors.textPrimary }]}>{calendarMonth.getFullYear()}년 {calendarMonth.getMonth() + 1}월</Text><Pressable hitSlop={10} onPress={() => setCalendarMonth((current) => new Date(current.getFullYear(), current.getMonth() + 1, 1))} style={styles.calendarNav}><MaterialIcons color={colors.primary} name="chevron-right" size={26} /></Pressable></View>
                  <View style={styles.weekRow}>{['일', '월', '화', '수', '목', '금', '토'].map((day, index) => <Text key={day} style={[styles.weekday, { color: index === 0 ? colors.danger : index === 6 ? colors.primary : colors.textSecondary }]}>{day}</Text>)}</View>
                  <View style={styles.daysGrid}>{calendarDays.map((date) => { const key = dateKey(date); const selected = key === scheduledDate; const outside = date.getMonth() !== calendarMonth.getMonth(); const weekendColor = date.getDay() === 0 ? colors.danger : date.getDay() === 6 ? colors.primary : colors.textPrimary; return <Pressable accessibilityRole="button" accessibilityState={{ selected }} key={key} onPress={() => selectDate(date)} style={[styles.dayCell, selected && { backgroundColor: colors.primary }]}><Text style={[styles.dayText, { color: selected ? colors.onPrimary : outside ? colors.placeholder : weekendColor }]}>{date.getDate()}</Text></Pressable>; })}</View>
                  <View style={styles.calendarActions}><AppButton fullWidth={false} label="취소" onPress={() => setCalendarVisible(false)} variant="tertiary" /><AppButton fullWidth={false} label="오늘" onPress={() => selectDate(new Date())} variant="secondary" /></View>
                </View> : null}
              </View>
              {error ? <InlineError message={error} /> : null}
              <AppButton
                disabled={!canSubmit}
                label={initialDraft ? '수정 저장' : '일정 등록'}
                loading={loading}
                onPress={() => assigneeUserId !== null && onSubmit({ title: title.trim(), description: description.trim(), assigneeUserId, scheduledDate })}
              />
            </View>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1 }, flex: { flex: 1 }, content: { padding: Spacing.screenHorizontal, paddingBottom: 40 },
  header: { alignItems: 'flex-start', flexDirection: 'row', gap: Spacing.item, justifyContent: 'space-between' }, headerCopy: { flex: 1, gap: Spacing.compact },
  subtitle: { fontSize: 14, lineHeight: 20 }, close: { alignItems: 'center', height: 44, justifyContent: 'center', width: 44 },
  form: { gap: Spacing.item, marginTop: Spacing.section }, memoInput: { minHeight: 92, paddingTop: 14 }, fieldGroup: { gap: Spacing.compact }, label: { fontSize: 14, fontWeight: '600' },
  memberRow: { alignItems: 'center', borderRadius: Radius.input, borderWidth: 1, flexDirection: 'row', gap: Spacing.md, minHeight: 58, paddingHorizontal: Spacing.item },
  memberCopy: { flex: 1, gap: 2 }, memberName: { fontSize: 15, fontWeight: '700' }, memberRole: { fontSize: 12 }, pressed: { opacity: 0.75 },
  dateField: { alignItems: 'center', borderRadius: Radius.input, borderWidth: 1, flexDirection: 'row', gap: Spacing.md, minHeight: 56, paddingHorizontal: Spacing.item },
  dateValue: { fontSize: 15, fontWeight: '700' }, calendar: { borderRadius: Radius.card, borderWidth: 1, gap: Spacing.compact, padding: Spacing.md },
  calendarHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' }, calendarNav: { alignItems: 'center', height: 44, justifyContent: 'center', width: 44 }, calendarTitle: { fontSize: 16, fontWeight: '800' },
  weekRow: { flexDirection: 'row' }, weekday: { fontSize: 12, fontWeight: '700', textAlign: 'center', width: '14.2857%' }, daysGrid: { flexDirection: 'row', flexWrap: 'wrap' }, dayCell: { alignItems: 'center', borderRadius: Radius.pill, height: 40, justifyContent: 'center', width: '14.2857%' }, dayText: { fontSize: 14, fontWeight: '600' }, calendarActions: { flexDirection: 'row', gap: Spacing.compact, justifyContent: 'flex-end' },
});
