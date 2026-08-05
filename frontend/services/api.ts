import { API_BASE_URL } from '@/constants/api';

type ApiRequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown;
  token?: string | null;
  timeoutMs?: number;
};

type ApiErrorKind = 'http' | 'network' | 'timeout';

type ApiErrorContext = {
  code?: string;
  config: { baseURL: string; url: string; timeout: number };
  response?: { status: number; data: unknown };
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly payload?: unknown,
    public readonly kind: ApiErrorKind = 'http',
    public readonly context?: ApiErrorContext
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function logApiError(error: ApiError): void {
  if (!__DEV__) return;

  const details = {
    message: error.message,
    code: error.context?.code,
    config: error.context?.config,
    response: error.context?.response,
  };
  const isExpectedApiError =
    error.kind === 'network' ||
    error.kind === 'timeout' ||
    error.status !== undefined;

  if (isExpectedApiError) {
    console.warn('[API] request failed:', details);
  } else {
    console.error('[API] unexpected error:', details);
  }
}

function payloadWithoutPassword(body: unknown): unknown {
  if (
    !body ||
    typeof body !== 'object' ||
    (typeof FormData !== 'undefined' && body instanceof FormData)
  ) {
    return body;
  }
  const { password: _password, ...safePayload } = body as Record<string, unknown>;
  return safePayload;
}

function getErrorMessage(payload: unknown): string {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) {
    return '요청을 처리하지 못했습니다.';
  }

  const detail = payload.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String(detail.message);
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const firstError = detail[0];
    if (firstError && typeof firstError === 'object' && 'msg' in firstError) {
      return String(firstError.msg).replace(/^Value error, /, '');
    }
  }
  return '입력 내용을 확인해주세요.';
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const { body, timeoutMs = 15000, token, ...requestOptions } = options;
  if (!API_BASE_URL) {
    const error = new ApiError('API 서버 주소가 설정되지 않았습니다.', undefined, undefined, 'network');
    logApiError(error);
    throw error;
  }

  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  const requestUrl = `${API_BASE_URL}${normalizedPath}`;
  const method = requestOptions.method?.toUpperCase() ?? 'GET';
  const headers = new Headers(options.headers);
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  headers.set('Accept', 'application/json');
  if (body !== undefined && !isFormData) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  if (normalizedPath === '/auth/login' || normalizedPath === '/auth/signup') {
    console.log('[API] auth request:', {
      method,
      url: requestUrl,
      timeout: timeoutMs,
      payload: payloadWithoutPassword(body),
    });
  }

  let response: Response;
  try {
    response = await fetch(requestUrl, {
      ...requestOptions,
      body:
        body === undefined
          ? undefined
          : isFormData
            ? (body as FormData)
            : JSON.stringify(body),
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      const timeoutError = new ApiError(
        '요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.',
        undefined,
        undefined,
        'timeout',
        { code: 'ECONNABORTED', config: { baseURL: API_BASE_URL, url: normalizedPath, timeout: timeoutMs } }
      );
      logApiError(timeoutError);
      throw timeoutError;
    }
    const networkError = new ApiError(
      '네트워크 오류가 발생했습니다. iPhone과 컴퓨터의 Wi-Fi 및 EXPO_PUBLIC_API_URL을 확인해주세요.',
      undefined,
      error,
      'network',
      { code: 'ERR_NETWORK', config: { baseURL: API_BASE_URL, url: normalizedPath, timeout: timeoutMs } }
    );
    logApiError(networkError);
    throw networkError;
  } finally {
    clearTimeout(timeout);
  }

  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const httpError = new ApiError(
      getErrorMessage(payload),
      response.status,
      payload,
      'http',
      {
        config: { baseURL: API_BASE_URL, url: normalizedPath, timeout: timeoutMs },
        response: { status: response.status, data: payload },
      }
    );
    logApiError(httpError);
    throw httpError;
  }
  return payload as T;
}
