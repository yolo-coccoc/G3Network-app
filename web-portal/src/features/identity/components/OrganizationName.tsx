/**
 * An organization's name from its ID: from the caller's memberships when it
 * is one of theirs, else from `GET /organizations/{id}`. An organization the
 * caller may not read (a customer looking at a truck's previous owner) is
 * shown by its ID.
 */
import { Typography } from "antd";

import { useSessionStore } from "../../../shared/auth/sessionStore";
import { useOrganizationQuery } from "../api/queries";

/**
 * Props of `OrganizationName`.
 *
 * Attributes:
 *   organizationId: The organization to name.
 */
interface OrganizationNameProps {
  organizationId: string;
}

/** The organization's display name, or its ID when it cannot be read. */
export function OrganizationName({ organizationId }: OrganizationNameProps) {
  const membershipName = useSessionStore(
    (state) =>
      state.me?.memberships.find(
        (membership) => membership.organization_id === organizationId,
      )?.organization_name,
  );
  const organizationQuery = useOrganizationQuery(
    organizationId,
    membershipName === undefined,
  );
  const name = membershipName ?? organizationQuery.data?.display_name;

  return name === undefined ? (
    <Typography.Text type="secondary" copyable>
      {organizationId}
    </Typography.Text>
  ) : (
    <Typography.Text>{name}</Typography.Text>
  );
}
