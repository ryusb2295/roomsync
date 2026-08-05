export function formatKoreanDate(value: unknown, fallback = '날짜 확인 필요'): string {
  if (typeof value !== 'string') return fallback;
  const match = value.trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return fallback;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!Number.isInteger(year) || month < 1 || month > 12 || day < 1 || day > 31) {
    return fallback;
  }
  return `${year}년 ${month}월 ${day}일`;
}
