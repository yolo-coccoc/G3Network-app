/**
 * `/select-organization` (ACC-04): a person with several organizations picks
 * one after login (`POST /auth/organization`); every later call acts for it.
 */
import { createFileRoute, redirect, useRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { OrganizationPicker } from "../features/identity";
import { toSafeRedirect } from "../shared/auth/redirect";
import { restoreSession } from "../shared/auth/session";
import { CenteredCardLayout } from "../shared/ui/CenteredCardLayout";

export const Route = createFileRoute("/select-organization")({
  validateSearch: (search: Record<string, unknown>): { redirect?: string } => ({
    redirect: toSafeRedirect(search.redirect),
  }),
  beforeLoad: async () => {
    const me = await restoreSession();
    if (me === null) {
      throw redirect({ to: "/login" });
    }
    return { me };
  },
  component: SelectOrganizationPage,
});

/** The organization picker page. */
function SelectOrganizationPage() {
  const { t } = useTranslation("identity");
  const { me } = Route.useRouteContext();
  const { redirect: redirectTo } = Route.useSearch();
  const router = useRouter();

  return (
    <CenteredCardLayout
      title={t("selectOrganization.title")}
      subtitle={t("selectOrganization.description")}
    >
      <OrganizationPicker
        memberships={me.memberships}
        onPicked={() => {
          router.history.push(redirectTo ?? "/");
        }}
      />
    </CenteredCardLayout>
  );
}
