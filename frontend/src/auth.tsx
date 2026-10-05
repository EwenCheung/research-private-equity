import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

interface Auth {
  authed: boolean | null; // null while the first session check is in flight
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<Auth>(null as never);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ authenticated: boolean }>("/api/session").then(
      (s) => setAuthed(s.authenticated),
      () => setAuthed(false),
    );
    const expired = () => setAuthed(false);
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, []);

  const login = useCallback(async (password: string) => {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password }) });
    setAuthed(true);
  }, []);
  const logout = useCallback(async () => {
    await api("/api/logout", { method: "POST" });
    setAuthed(false);
  }, []);

  return <AuthContext.Provider value={{ authed, login, logout }}>{children}</AuthContext.Provider>;
}
