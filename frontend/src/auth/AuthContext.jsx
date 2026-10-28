import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import * as api from "../api/client.js";

const AuthContext = createContext(null);

// status: "loading" (checking an existing session) | "authed" | "unauthed"
export function AuthProvider({ children }) {
  const [status, setStatus] = useState("loading");
  const [user, setUser] = useState(null);
  const [message, setMessage] = useState(null);
  const loggingOutRef = useRef(false);

  const logout = useCallback((reason) => {
    if (loggingOutRef.current) return;
    loggingOutRef.current = true;
    api.clearToken();
    setUser(null);
    setMessage(reason || null);
    setStatus("unauthed");
    loggingOutRef.current = false;
  }, []);

  useEffect(() => {
    // Every API call funnels through client.js - ANY 401 anywhere in the app
    // means the session is no longer valid (there is no refresh endpoint).
    api.setUnauthorizedHandler(() => logout("Session expired"));
  }, [logout]);

  useEffect(() => {
    const token = api.getToken();
    if (!token) {
      setStatus("unauthed");
      return;
    }
    // Render nothing admin-related until /me actually succeeds.
    api
      .getMe()
      .then((me) => {
        if (me.role !== "admin") {
          api.clearToken();
          setMessage("Admin access only");
          setStatus("unauthed");
          return;
        }
        setUser(me);
        setStatus("authed");
      })
      .catch(() => {
        api.clearToken();
        setStatus("unauthed");
      });
  }, []);

  const login = useCallback(async (email, password) => {
    const result = await api.login(email, password);
    api.setToken(result.access_token);
    const me = await api.getMe();
    if (me.role !== "admin") {
      api.clearToken();
      setMessage("Admin access only");
      setStatus("unauthed");
      throw new Error("Admin access only");
    }
    setUser(me);
    setMessage(null);
    setStatus("authed");
    return me;
  }, []);

  const value = { status, user, message, login, logout: () => logout(null) };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
