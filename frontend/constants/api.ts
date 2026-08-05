const configuredUrl = process.env.EXPO_PUBLIC_API_URL?.trim();

export const API_BASE_URL = configuredUrl?.replace(/\/+$/, '') ?? '';

console.log('[API] EXPO_PUBLIC_API_URL:', configuredUrl || '(not set)');
console.log('[API] baseURL:', API_BASE_URL || '(not set)');
