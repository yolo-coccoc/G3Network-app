/**
 * The live map of a fleet (FLT-04): one point per truck with a position,
 * colored by online state through the status map.
 */
import { MapView } from "../../../shared/ui/MapView";
import { STATUS_PRESETS, toOnlineStatus } from "../../../shared/ui/status";
import {
  hasPosition,
  type LiveVehicle,
  toLiveVehicleLabel,
} from "./liveVehicles";

/**
 * Props of `LiveVehicleMap`.
 *
 * Attributes:
 *   vehicles: The trucks; those without a position are left out.
 *   selectedVehicleId: The highlighted truck.
 *   onSelect: Called with a truck's ID when its point is clicked.
 */
interface LiveVehicleMapProps {
  vehicles: LiveVehicle[];
  selectedVehicleId: string | undefined;
  onSelect: (vehicleId: string) => void;
}

/** Trucks on the map. */
export function LiveVehicleMap({
  vehicles,
  selectedVehicleId,
  onSelect,
}: LiveVehicleMapProps) {
  return (
    <MapView
      points={vehicles.filter(hasPosition).map((vehicle) => ({
        id: vehicle.vehicle_id,
        latitude: vehicle.latitude,
        longitude: vehicle.longitude,
        preset: STATUS_PRESETS[toOnlineStatus(vehicle.is_online)],
        label: toLiveVehicleLabel(vehicle),
      }))}
      selectedId={selectedVehicleId}
      onSelect={onSelect}
    />
  );
}
