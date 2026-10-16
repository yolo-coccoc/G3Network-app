/**
 * Shared shapes and pure helpers of the live-map components.
 */
import type { Schema } from "@g3/api-client";

/** One truck's latest state as the fleet live endpoint returns it. */
export type LiveVehicle = Schema<"FleetVehicleLiveStatusResponse">;

/**
 * The label of a truck on the map and in the list: its plate, else its VIN,
 * else the start of its ID.
 *
 * @param vehicle - The truck.
 * @returns A short identifying label.
 */
export function toLiveVehicleLabel(vehicle: LiveVehicle): string {
  return vehicle.license_plate ?? vehicle.vin ?? vehicle.vehicle_id.slice(0, 8);
}

/**
 * Whether the truck has a position to draw.
 *
 * @param vehicle - The truck.
 * @returns `true` when both coordinates are known.
 */
export function hasPosition(
  vehicle: LiveVehicle,
): vehicle is LiveVehicle & { latitude: number; longitude: number } {
  return (
    typeof vehicle.latitude === "number" &&
    typeof vehicle.longitude === "number"
  );
}
