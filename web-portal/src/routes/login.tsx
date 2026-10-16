/**
 * `/login` (ACC-04): phone + password. A visitor who still has a session
 * (the refresh cookie) goes straight on; after login, a person who has not
 * picked an organization goes to the picker, everyone else back to where
 * they were going (`?redirect=`, a portal path only).
 */
import {
  createFileRoute,
  redirect,
  useNavigate,
  useRouter,
} from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { LoginForm } from "../features/identity";
import { toSafeRedirect } from "../shared/auth/redirect";
import { restoreSession } from "../shared/auth/session";
import { CenteredCardLayout } from "../shared/ui/CenteredCardLayout";

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: toSafeRedirect(search.redirect),
  }),
  beforeLoad: async ({ search }) => {
    const me = await restoreSession().catch(() => null);
    if (me?.organization_id !== null && me?.organization_id !== undefined) {
      throw redirect({ href: search.redirect ?? "/" });
    }
  },
  component: LoginPage,
});

/** The login page. */
function LoginPage() {
  const { t } = useTranslation("identity");
  const { redirect: redirectTo } = Route.useSearch();
  const navigate = useNavigate();
  const router = useRouter();

  return (
    <CenteredCardLayout title={t("login.title")} subtitle={t("login.subtitle")}>
      <LoginForm
        onLoggedIn={(me) => {
          if (me.organization_id === null) {
            void navigate({
              to: "/select-organization",
              search: { redirect: redirectTo },
            });
          } else {
            router.history.push(redirectTo ?? "/");
          }
        }}
      />
    </CenteredCardLayout>
  );
}
