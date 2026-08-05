export function formatAud(value: number): string {
  return `AUD ${value.toFixed(2)}`;
}

export function formatAudFromCents(value: number): string {
  return formatAud(value / 100);
}
