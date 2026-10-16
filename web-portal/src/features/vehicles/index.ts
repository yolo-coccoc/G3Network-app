/**
 * Public surface of the vehicles feature (web-portal-conventions.md §4): only
 * re-exports, no logic. Routes import from here only.
 */
export { useVehicleListQuery, useVehicleQuery } from "./api/queries";
export {
  VehicleFilterBar,
  type VehicleFilters,
} from "./components/VehicleFilterBar";
export { VehicleForm } from "./components/VehicleForm";
export { VehicleOwnershipPeriodTable } from "./components/VehicleOwnershipPeriodTable";
export { VehicleRemoveButton } from "./components/VehicleRemoveButton";
export { VehicleSummary } from "./components/VehicleSummary";
export { VehicleTable } from "./components/VehicleTable";
export { VEHICLE_READ_FEATURES, VEHICLE_WRITE_FEATURES } from "./permissions";
export { parseVehicleListSearch, type VehicleListSearch } from "./search";
