/**
 * A vehicle's summary on its detail page: identity, model, owner, status
 * and its reason, and the record's times.
 */
import type { Schema } from "@g3/api-client";
import { Descriptions } from "antd";
import { useTranslation } from "react-i18next";

import {
  EMPTY_VALUE,
  formatVietnamDate,
  formatVietnamDateTime,
} from "../../../shared/lib/vietnamTime";
import { StatusTag } from "../../../shared/ui/StatusTag";
import { OrganizationName } from "../../identity";
import { useVehicleModelNames } from "./useVehicleModelNames";

/**
 * Props of `VehicleSummary`.
 *
 * Attributes:
 *   vehicle: The vehicle.
 */
interface VehicleSummaryProps {
  vehicle: Schema<"VehicleResponse">;
}

/** The vehicle's fields as a description list. */
export function VehicleSummary({ vehicle }: VehicleSummaryProps) {
  const { t } = useTranslation("vehicles");
  const modelNameOf = useVehicleModelNames();

  return (
    <Descriptions
      title={t("detail.summary")}
      column={{ xs: 1, md: 2, xl: 3 }}
      items={[
        {
          key: "license_plate",
          label: t("fields.licensePlate"),
          children: vehicle.license_plate,
        },
        { key: "vin", label: t("fields.vin"), children: vehicle.vin },
        {
          key: "model",
          label: t("fields.model"),
          children: modelNameOf(vehicle.vehicle_model_id) ?? EMPTY_VALUE,
        },
        { key: "year", label: t("fields.year"), children: vehicle.year },
        {
          key: "owner",
          label: t("fields.owner"),
          children: (
            <OrganizationName organizationId={vehicle.organization_id} />
          ),
        },
        {
          key: "acquired_at",
          label: t("fields.acquiredAt"),
          children: formatVietnamDate(vehicle.acquired_at),
        },
        {
          key: "status",
          label: t("fields.status"),
          children: <StatusTag value={vehicle.status} />,
        },
        {
          key: "status_reason",
          label: t("fields.statusReason"),
          children: vehicle.status_reason ?? EMPTY_VALUE,
        },
        {
          key: "updated_at",
          label: t("fields.updatedAt"),
          children: formatVietnamDateTime(vehicle.updated_at),
        },
      ]}
    />
  );
}
