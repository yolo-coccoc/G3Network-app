/**
 * A searchable select of organizations, for internal staff who act for
 * another organization (the owner of a new vehicle). Customer users only
 * find their own organization, so screens show it to internal staff only.
 */
import { Select } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useOrganizationListQuery } from "../api/queries";

/**
 * Props of `OrganizationSelect` (a controlled Form field).
 *
 * Attributes:
 *   value: The chosen organization ID.
 *   onChange: Called with the new ID, or `undefined` when cleared.
 */
interface OrganizationSelectProps {
  value?: string;
  onChange?: (organizationId: string | undefined) => void;
}

/** Organization search-and-pick. */
export function OrganizationSelect({
  value,
  onChange,
}: OrganizationSelectProps) {
  const { t } = useTranslation("identity");
  const [searchText, setSearchText] = useState("");
  const organizationListQuery = useOrganizationListQuery(searchText);

  return (
    <Select
      showSearch={{ filterOption: false, onSearch: setSearchText }}
      allowClear
      value={value}
      placeholder={t("organizationSelect.placeholder")}
      loading={organizationListQuery.isFetching}
      options={organizationListQuery.data?.items.map((organization) => ({
        value: organization.organization_id,
        label: organization.display_name,
      }))}
      onChange={(organizationId: string | undefined) => {
        onChange?.(organizationId);
      }}
    />
  );
}
