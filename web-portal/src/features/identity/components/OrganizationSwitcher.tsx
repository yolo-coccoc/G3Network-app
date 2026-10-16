/**
 * The organization switcher of the header: a person with several active
 * memberships changes where they work without logging out. With one
 * organization it only shows its name.
 */
import { Select, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { Me } from "../../../shared/auth/sessionStore";
import { useReportApiError } from "../../../shared/ui/feedback";
import { useSwitchOrganizationMutation } from "../api/mutations";

/**
 * Props of `OrganizationSwitcher`.
 *
 * Attributes:
 *   me: The caller (current organization and memberships).
 *   onSwitched: Called with the profile in the new organization.
 */
interface OrganizationSwitcherProps {
  me: Me;
  onSwitched: (me: Me) => void;
}

/** A select of the caller's active organizations. */
export function OrganizationSwitcher({
  me,
  onSwitched,
}: OrganizationSwitcherProps) {
  const { t } = useTranslation("identity");
  const switchOrganizationMutation = useSwitchOrganizationMutation();
  const reportApiError = useReportApiError();
  const activeMemberships = me.memberships.filter(
    (membership) => membership.status === "ACTIVE",
  );
  const currentMembership = me.memberships.find(
    (membership) => membership.organization_id === me.organization_id,
  );

  if (activeMemberships.length <= 1) {
    return (
      <Typography.Text>{currentMembership?.organization_name}</Typography.Text>
    );
  }
  return (
    <Select
      aria-label={t("organizationSwitcher.label")}
      value={me.organization_id}
      loading={switchOrganizationMutation.isPending}
      popupMatchSelectWidth={false}
      options={activeMemberships.map((membership) => ({
        value: membership.organization_id,
        label: membership.organization_name,
      }))}
      onChange={(organizationId: string) => {
        switchOrganizationMutation.mutate(organizationId, {
          onSuccess: onSwitched,
          onError: (error) => {
            reportApiError(error);
          },
        });
      }}
    />
  );
}
