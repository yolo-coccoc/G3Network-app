/**
 * The organization picker after login (ACC-04): a person with several active
 * memberships chooses where to work; the choice is `POST /auth/organization`.
 */
import type { Schema } from "@g3/api-client";
import { Button, Card, Flex, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import type { Me } from "../../../shared/auth/sessionStore";
import { useReportApiError } from "../../../shared/ui/feedback";
import { useSwitchOrganizationMutation } from "../api/mutations";

/** One of the caller's memberships, as `/auth/me` lists them. */
type Membership = Schema<"MembershipSummaryResponse">;

/**
 * Props of `OrganizationPicker`.
 *
 * Attributes:
 *   memberships: The caller's memberships; only active ones are offered.
 *   onPicked: Called with the profile in the chosen organization.
 */
interface OrganizationPickerProps {
  memberships: Membership[];
  onPicked: (me: Me) => void;
}

/** The caller's organizations, one card each with a Choose button. */
export function OrganizationPicker({
  memberships,
  onPicked,
}: OrganizationPickerProps) {
  const { t } = useTranslation("identity");
  const switchOrganizationMutation = useSwitchOrganizationMutation();
  const reportApiError = useReportApiError();
  const activeMemberships = memberships.filter(
    (membership) => membership.status === "ACTIVE",
  );

  return (
    <Flex vertical gap="small">
      {activeMemberships.map((membership) => (
        <Card key={membership.membership_id} size="small">
          <Flex justify="space-between" align="center" gap="middle">
            <Flex vertical gap="small">
              <Flex align="center" gap="small">
                <Typography.Text strong>
                  {membership.organization_name}
                </Typography.Text>
                {membership.is_internal && (
                  <Tag color="processing">
                    {t("selectOrganization.internal")}
                  </Tag>
                )}
              </Flex>
              <Flex gap="small" wrap>
                {membership.roles.map((role) => (
                  <Tag key={role}>{t(`roles.${role}`)}</Tag>
                ))}
              </Flex>
            </Flex>
            <Button
              type="primary"
              loading={
                switchOrganizationMutation.isPending &&
                switchOrganizationMutation.variables ===
                  membership.organization_id
              }
              disabled={switchOrganizationMutation.isPending}
              onClick={() => {
                switchOrganizationMutation.mutate(membership.organization_id, {
                  onSuccess: onPicked,
                  onError: (error) => {
                    reportApiError(error);
                  },
                });
              }}
            >
              {t("selectOrganization.choose")}
            </Button>
          </Flex>
        </Card>
      ))}
    </Flex>
  );
}
