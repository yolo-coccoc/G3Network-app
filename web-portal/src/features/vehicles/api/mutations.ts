/**
 * Write hooks of the vehicles domain (web-portal-conventions.md §9): each
 * invalidates the vehicle list and the vehicle it touched.
 */
import type { Schema } from "@g3/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiClient, unwrap } from "../../../shared/api/client";
import { vehicleKeys } from "./keys";

/**
 * Register a vehicle (`POST /vehicles/`).
 *
 * @returns The mutation; its result is the new vehicle.
 */
export function useCreateVehicleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vehicleCreateRequest: Schema<"VehicleCreateRequest">) =>
      unwrap(
        await apiClient.POST("/api/v1/vehicles/", {
          body: vehicleCreateRequest,
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: vehicleKeys.lists() });
    },
  });
}

/**
 * Change a vehicle (`PATCH /vehicles/{vehicle_id}`); only the fields sent change.
 *
 * @param vehicleId - The vehicle's ID.
 * @returns The mutation; its result is the updated vehicle.
 */
export function useUpdateVehicleMutation(vehicleId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vehicleUpdateRequest: Schema<"VehicleUpdateRequest">) =>
      unwrap(
        await apiClient.PATCH("/api/v1/vehicles/{vehicle_id}", {
          params: { path: { vehicle_id: vehicleId } },
          body: vehicleUpdateRequest,
        }),
      ),
    onSuccess: async (vehicle) => {
      queryClient.setQueryData(vehicleKeys.detail(vehicleId), vehicle);
      await queryClient.invalidateQueries({ queryKey: vehicleKeys.lists() });
    },
  });
}

/**
 * Remove a vehicle from the system (`DELETE /vehicles/{vehicle_id}?reason=`):
 * a soft delete that also sets it INACTIVE with the reason (DM-25).
 *
 * @param vehicleId - The vehicle's ID.
 * @returns The mutation, called with the reason.
 */
export function useSoftDeleteVehicleMutation(vehicleId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (reason: string) =>
      unwrap(
        await apiClient.DELETE("/api/v1/vehicles/{vehicle_id}", {
          params: { path: { vehicle_id: vehicleId }, query: { reason } },
        }),
      ),
    onSuccess: async () => {
      queryClient.removeQueries({ queryKey: vehicleKeys.detail(vehicleId) });
      await queryClient.invalidateQueries({ queryKey: vehicleKeys.lists() });
    },
  });
}
