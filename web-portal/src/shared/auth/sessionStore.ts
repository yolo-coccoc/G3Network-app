/**
 * The session (web-portal-conventions.md §8, §9): the access token, in memory
 * only (never `localStorage`), and the caller's `GET /auth/me` answer (user,
 * roles, features, organization, memberships). A reload starts empty and the
 * route guard restores the session from the refresh cookie.
 */
import type { Schema } from "@g3/api-client";
import { create } from "zustand";

/** The caller as `GET /auth/me` describes them. */
export type Me = Schema<"MeResponse">;

/**
 * Session state.
 *
 * Attributes:
 *   accessToken: The short-lived access token, or `null` with no session.
 *   me: The caller's profile and features, or `null` before it is loaded.
 *   setAccessToken: Replace the token (after login or a refresh).
 *   setMe: Replace the profile (after login, `/auth/me` or an organization switch).
 *   clearSession: Forget both (logout, ended session).
 */
interface SessionState {
  accessToken: string | null;
  me: Me | null;
  setAccessToken: (accessToken: string | null) => void;
  setMe: (me: Me) => void;
  clearSession: () => void;
}

export const useSessionStore = create<SessionState>()((set) => ({
  accessToken: null,
  me: null,
  setAccessToken: (accessToken) => {
    set({ accessToken });
  },
  setMe: (me) => {
    set({ me });
  },
  clearSession: () => {
    set({ accessToken: null, me: null });
  },
}));
