/**
 * The vehicle list's filter bar: free-text search (plate or VIN) and status.
 * Values come from and go to the URL's search params through the route.
 */
import { Input, Select } from "antd";
import { useTranslation } from "react-i18next";

import { FilterField } from "../../../shared/ui/FilterField";
import { VEHICLE_STATUSES, type VehicleStatus } from "../search";

/**
 * The filters the bar edits.
 *
 * Attributes:
 *   q: Search text.
 *   status: Status filter.
 */
export interface VehicleFilters {
  q?: string;
  status?: VehicleStatus;
}

/**
 * Props of `VehicleFilterBar`.
 *
 * Attributes:
 *   filters: The filters in the URL.
 *   onChange: Called with the new filters (the route resets the page).
 */
interface VehicleFilterBarProps {
  filters: VehicleFilters;
  onChange: (filters: VehicleFilters) => void;
}

/** Search box and status select. */
export function VehicleFilterBar({ filters, onChange }: VehicleFilterBarProps) {
  const { t } = useTranslation("vehicles");
  const { t: tCommon } = useTranslation();

  return (
    <>
      <FilterField size="wide">
        <Input.Search
          key={filters.q ?? ""}
          allowClear
          defaultValue={filters.q}
          placeholder={t("list.searchPlaceholder")}
          aria-label={t("list.searchPlaceholder")}
          onSearch={(text) => {
            const searchText = text.trim();
            onChange({
              ...filters,
              q: searchText === "" ? undefined : searchText,
            });
          }}
        />
      </FilterField>
      <FilterField size="narrow">
        <Select<VehicleStatus | undefined>
          allowClear
          value={filters.status}
          placeholder={t("list.allStatuses")}
          aria-label={t("fields.status")}
          options={VEHICLE_STATUSES.map((status) => ({
            value: status,
            label: tCommon(`status.${status}`),
          }))}
          onChange={(status) => {
            onChange({ ...filters, status });
          }}
        />
      </FilterField>
    </>
  );
}
