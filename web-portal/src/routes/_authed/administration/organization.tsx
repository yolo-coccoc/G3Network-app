/**
 * `/administration/organization` (ACC-23, ID-45), the Settings template's
 * sample page: the current organization's own settings.
 */
import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  ORGANIZATION_SETTINGS_FEATURE,
  OrganizationSettingsSections,
} from "../../../features/identity";
import { requireFeature } from "../../../shared/auth/guards";
import { PageHeader } from "../../../shared/ui/PageHeader";
import { SettingsPageTemplate } from "../../../shared/ui/templates/SettingsPageTemplate";

export const Route = createFileRoute("/_authed/administration/organization")({
  beforeLoad: ({ context }) => {
    requireFeature(context.me, ORGANIZATION_SETTINGS_FEATURE);
  },
  component: OrganizationSettingsPage,
});

/** The organization settings page. */
function OrganizationSettingsPage() {
  const { t } = useTranslation("identity");
  const { me } = Route.useRouteContext();

  return (
    <SettingsPageTemplate
      header={<PageHeader title={t("settings.pageTitle")} />}
    >
      <OrganizationSettingsSections organizationId={me.organization_id} />
    </SettingsPageTemplate>
  );
}
