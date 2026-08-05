import * as SecureStore from 'expo-secure-store';
import type { PropsWithChildren } from 'react';
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Platform } from 'react-native';

import { apiRequest } from '@/services/api';
import type { AuthResponse, House, HouseMember, User } from '@/types/api';

const TOKEN_KEY = 'roomsync.accessToken';
const HOUSE_KEY = 'roomsync.currentHouse';

async function readStorage(key: string): Promise<string | null> {
  if (Platform.OS === 'web') return globalThis.localStorage?.getItem(key) ?? null;
  return SecureStore.getItemAsync(key);
}

async function writeStorage(key: string, value: string): Promise<void> {
  if (Platform.OS === 'web') {
    globalThis.localStorage?.setItem(key, value);
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

async function removeStorage(key: string): Promise<void> {
  if (Platform.OS === 'web') {
    globalThis.localStorage?.removeItem(key);
    return;
  }
  await SecureStore.deleteItemAsync(key);
}

type AuthContextValue = {
  isLoading: boolean;
  token: string | null;
  user: User | null;
  currentHouse: House | null;
  houseMembers: HouseMember[];
  membersLoading: boolean;
  membersError: string | null;
  logoutNotice: string | null;
  saveAuth: (auth: AuthResponse) => Promise<void>;
  selectHouse: (house: House) => Promise<void>;
  clearHouse: () => Promise<void>;
  clearLocalSession: () => Promise<void>;
  clearLogoutNotice: () => void;
  logout: () => Promise<void>;
  refreshHouseMembers: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [isLoading, setIsLoading] = useState(true);
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [currentHouse, setCurrentHouse] = useState<House | null>(null);
  const [houseMembers, setHouseMembers] = useState<HouseMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [logoutNotice, setLogoutNotice] = useState<string | null>(null);

  useEffect(() => {
    async function restoreSession() {
      try {
        const [storedToken, storedHouse] = await Promise.all([
          readStorage(TOKEN_KEY),
          readStorage(HOUSE_KEY),
        ]);
        if (!storedToken) return;

        const restoredUser = await apiRequest<User>('/auth/me', { token: storedToken });
        setToken(storedToken);
        setUser(restoredUser);
        if (storedHouse) {
          const parsedHouse = JSON.parse(storedHouse) as House;
          const houses = await apiRequest<House[]>('/houses', { token: storedToken });
          const validHouse = houses.find((house) => house.id === parsedHouse.id);
          if (validHouse) {
            setCurrentHouse(validHouse);
          } else {
            await removeStorage(HOUSE_KEY);
          }
        }
      } catch {
        await Promise.all([removeStorage(TOKEN_KEY), removeStorage(HOUSE_KEY)]);
      } finally {
        setIsLoading(false);
      }
    }

    restoreSession();
  }, []);

  const saveAuth = useCallback(async (auth: AuthResponse) => {
    await writeStorage(TOKEN_KEY, auth.access_token);
    setToken(auth.access_token);
    setUser(auth.user);
    setCurrentHouse(null);
    setHouseMembers([]);
    setLogoutNotice(null);
    await removeStorage(HOUSE_KEY);
  }, []);

  const selectHouse = useCallback(async (house: House) => {
    await writeStorage(HOUSE_KEY, JSON.stringify(house));
    setCurrentHouse(house);
  }, []);

  const clearHouse = useCallback(async () => {
    await removeStorage(HOUSE_KEY);
    setCurrentHouse(null);
    setHouseMembers([]);
    setMembersError(null);
  }, []);

  const logout = useCallback(async () => {
    const activeToken = token;
    setToken(null);
    setUser(null);
    setCurrentHouse(null);
    setHouseMembers([]);
    setMembersError(null);
    await Promise.all([removeStorage(TOKEN_KEY), removeStorage(HOUSE_KEY)]);
    if (activeToken) {
      try {
        await apiRequest<null>('/auth/logout', { method: 'POST', token: activeToken });
        setLogoutNotice(null);
      } catch {
        setLogoutNotice('서버 연결에 실패했지만 로그아웃되었습니다.');
      }
    }
  }, [token]);

  const clearLogoutNotice = useCallback(() => setLogoutNotice(null), []);

  const clearLocalSession = useCallback(async () => {
    await Promise.all([removeStorage(TOKEN_KEY), removeStorage(HOUSE_KEY)]);
    setToken(null);
    setUser(null);
    setCurrentHouse(null);
    setHouseMembers([]);
    setMembersError(null);
  }, []);

  const refreshHouseMembers = useCallback(async () => {
    if (!token || !currentHouse) {
      setHouseMembers([]);
      return;
    }
    setMembersLoading(true);
    setMembersError(null);
    try {
      const response = await apiRequest<HouseMember[] | { members?: HouseMember[] }>(
        `/houses/${currentHouse.id}/members`,
        { token }
      );
      const members = Array.isArray(response)
        ? response
        : Array.isArray(response?.members) ? response.members : [];
      if (__DEV__) console.log('[cleaning] members:', { response, normalizedCount: members.length });
      if (!Array.isArray(response) && !Array.isArray(response?.members) && __DEV__) console.warn('[cleaning] invalid members response:', response);
      setHouseMembers(members);
    } catch (error) {
      setHouseMembers([]);
      setMembersError(error instanceof Error ? error.message : '하우스 멤버를 불러오지 못했습니다.');
    } finally {
      setMembersLoading(false);
    }
  }, [currentHouse, token]);

  useEffect(() => {
    refreshHouseMembers();
  }, [refreshHouseMembers]);

  const value = useMemo(
    () => ({
      isLoading,
      token,
      user,
      currentHouse,
      houseMembers,
      membersLoading,
      membersError,
      logoutNotice,
      saveAuth,
      selectHouse,
      clearHouse,
      clearLocalSession,
      clearLogoutNotice,
      logout,
      refreshHouseMembers,
    }),
    [
      clearHouse,
      clearLocalSession,
      clearLogoutNotice,
      currentHouse,
      houseMembers,
      isLoading,
      logout,
      membersError,
      membersLoading,
      logoutNotice,
      refreshHouseMembers,
      saveAuth,
      selectHouse,
      token,
      user,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider.');
  return context;
}
