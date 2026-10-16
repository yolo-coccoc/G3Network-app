/**
 * Names of vehicle models by ID, for lists and summaries that only carry
 * `vehicle_model_id`. Uses the model catalog query (its first 100 models).
 */
import { useMemo } from "react";

import { useVehicleModelListQuery } from "../api/queries";

/**
 * React hook: a lookup of model names (`make model_name`).
 *
 * @returns A function giving a model's name, or `undefined` while unknown.
 */
export function useVehicleModelNames(): (
  vehicleModelId: string,
) => string | undefined {
  const vehicleModelListQuery = useVehicleModelListQuery();
  const namesById = useMemo(
    () =>
      new Map(
        (vehicleModelListQuery.data?.items ?? []).map((vehicleModel) => [
          vehicleModel.vehicle_model_id,
          `${vehicleModel.make} ${vehicleModel.model_name}`,
        ]),
      ),
    [vehicleModelListQuery.data],
  );
  return (vehicleModelId) => namesById.get(vehicleModelId);
}
