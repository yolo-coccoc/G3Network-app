/**
 * The scope picker of fleet screens: one fleet, and whether its sub-fleets
 * count too (the API's `include_descendants`).
 */
import { Checkbox, Select } from "antd";
import { useTranslation } from "react-i18next";

import { FilterField } from "../../../shared/ui/FilterField";
import { useFleetListQuery } from "../api/queries";

/**
 * The picked scope.
 *
 * Attributes:
 *   fleetId: The fleet, or nothing before one is picked.
 *   includeDescendants: Count the trucks of every sub-fleet too.
 */
export interface FleetScope {
  fleetId: string | undefined;
  includeDescendants: boolean;
}

/**
 * Props of `FleetScopePicker`.
 *
 * Attributes:
 *   scope: The scope in the URL.
 *   onChange: Called with the new scope.
 */
interface FleetScopePickerProps {
  scope: FleetScope;
  onChange: (scope: FleetScope) => void;
}

/** Fleet select and sub-fleets checkbox. */
export function FleetScopePicker({ scope, onChange }: FleetScopePickerProps) {
  const { t } = useTranslation("fleet");
  const fleetListQuery = useFleetListQuery();

  return (
    <>
      <FilterField size="wide">
        <Select<string>
          showSearch={{ optionFilterProp: "label" }}
          value={scope.fleetId}
          loading={fleetListQuery.isPending}
          placeholder={t("fleetSelect.placeholder")}
          aria-label={t("fleetSelect.placeholder")}
          options={fleetListQuery.data?.items.map((fleet) => ({
            value: fleet.fleet_id,
            label: fleet.name ?? fleet.fleet_code ?? fleet.fleet_id,
          }))}
          onChange={(fleetId) => {
            onChange({ ...scope, fleetId });
          }}
        />
      </FilterField>
      <Checkbox
        checked={scope.includeDescendants}
        onChange={(event) => {
          onChange({ ...scope, includeDescendants: event.target.checked });
        }}
      >
        {t("fleetSelect.includeSubFleets")}
      </Checkbox>
    </>
  );
}
