/**
 * The vehicle list table (VEH-01): plate first, status as a tag, the view
 * action last. Server-paginated; the route owns page and filters.
 */
import type { Schema } from "@g3/api-client";
import { Button, type TableColumnsType, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { DataTable } from "../../../shared/ui/DataTable";
import { EmptyState } from "../../../shared/ui/states";
import { StatusTag } from "../../../shared/ui/StatusTag";
import {
  EMPTY_VALUE,
  formatVietnamDate,
} from "../../../shared/lib/vietnamTime";
import { useVehicleModelNames } from "./useVehicleModelNames";

/** A vehicle as the API returns it. */
type Vehicle = Schema<"VehicleResponse">;

/**
 * Props of `VehicleTable`.
 *
 * Attributes:
 *   vehiclePage: The current page from `useVehicleListQuery`, if loaded.
 *   page, pageSize: The page shown.
 *   isLoading: A page is loading.
 *   isFiltered: Filters are on (changes the empty text).
 *   onPageChange: Called with the new page and size.
 *   onOpen: Called with a vehicle's ID to open its detail page.
 */
interface VehicleTableProps {
  vehiclePage: Schema<"VehicleListResponse"> | undefined;
  page: number;
  pageSize: number;
  isLoading: boolean;
  isFiltered: boolean;
  onPageChange: (page: number, pageSize: number) => void;
  onOpen: (vehicleId: string) => void;
}

/** One page of vehicles. */
export function VehicleTable({
  vehiclePage,
  page,
  pageSize,
  isLoading,
  isFiltered,
  onPageChange,
  onOpen,
}: VehicleTableProps) {
  const { t } = useTranslation("vehicles");
  const { t: tCommon } = useTranslation();
  const modelNameOf = useVehicleModelNames();

  const columns: TableColumnsType<Vehicle> = [
    {
      key: "license_plate",
      title: t("fields.licensePlate"),
      render: (_, vehicle) => (
        <Typography.Link
          onClick={() => {
            onOpen(vehicle.vehicle_id);
          }}
        >
          {vehicle.license_plate}
        </Typography.Link>
      ),
    },
    { key: "vin", dataIndex: "vin", title: t("fields.vin") },
    {
      key: "vehicle_model_id",
      title: t("fields.model"),
      render: (_, vehicle) =>
        modelNameOf(vehicle.vehicle_model_id) ?? EMPTY_VALUE,
    },
    { key: "year", dataIndex: "year", title: t("fields.year") },
    {
      key: "acquired_at",
      title: t("fields.acquiredAt"),
      render: (_, vehicle) => formatVietnamDate(vehicle.acquired_at),
    },
    {
      key: "status",
      title: t("fields.status"),
      render: (_, vehicle) => <StatusTag value={vehicle.status} />,
    },
    {
      key: "actions",
      title: t("fields.actions"),
      align: "right",
      render: (_, vehicle) => (
        <Button
          size="small"
          onClick={() => {
            onOpen(vehicle.vehicle_id);
          }}
        >
          {tCommon("actions.view")}
        </Button>
      ),
    },
  ];

  return (
    <DataTable<Vehicle>
      columns={columns}
      rows={vehiclePage?.items}
      rowKey="vehicle_id"
      total={vehiclePage?.total ?? 0}
      page={page}
      pageSize={pageSize}
      isLoading={isLoading}
      empty={
        <EmptyState
          description={isFiltered ? t("list.emptyFiltered") : t("list.empty")}
        />
      }
      onPageChange={onPageChange}
    />
  );
}
