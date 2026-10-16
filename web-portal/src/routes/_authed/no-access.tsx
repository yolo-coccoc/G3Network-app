/**
 * `/no-access`: where a route guard sends a user without the page's feature
 * (web-portal-conventions.md §7), inside the shell so the tabs they may use
 * stay reachable.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { PageHeader } from "../../shared/ui/PageHeader";
import { NoAccessState } from "../../shared/ui/states";

export const Route = createFileRoute("/_authed/no-access")({
  component: NoAccessPage,
});

/** The no-access page. */
function NoAccessPage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t("noAccess.title")} />
      <NoAccessState />
    </>
  );
}
