"use client";

import {
  fetchMe,
  getStoredUser,
  login as loginRequest,
  logout as logoutRequest,
  register as registerRequest,
  storeAuthToken,
  storeUser
} from "@/lib/api";
import type { User } from "@/lib/types";
import { createContext, useContext, useEffect, useState } from "react";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const storedUser = getStoredUser();
    if (storedUser) {
      setUser(storedUser);
    }

    fetchMe()
      .then((profile) => {
        setUser(profile);
        storeUser(profile);
        setStatus("authenticated");
      })
      .catch(() => {
        storeAuthToken(null);
        storeUser(null);
        setUser(null);
        setStatus("unauthenticated");
      });
  }, []);

  async function hydrateFromSession() {
    const profile = await fetchMe();
    storeUser(profile);
    setUser(profile);
    setStatus("authenticated");
  }

  async function handleLogin(username: string, password: string) {
    const token = await loginRequest(username, password);
    storeAuthToken(token.access_token);
    await hydrateFromSession();
  }

  async function handleRegister(username: string, password: string) {
    await registerRequest(username, password);
    await handleLogin(username, password);
  }

  async function logout() {
    try {
      await logoutRequest();
    } finally {
      storeAuthToken(null);
      storeUser(null);
      setUser(null);
      setStatus("unauthenticated");
    }
  }

  async function refreshUser() {
    const profile = await fetchMe();
    setUser(profile);
    storeUser(profile);
  }

  const value: AuthContextValue = {
    status,
    user,
    login: handleLogin,
    register: handleRegister,
    logout,
    refreshUser
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
