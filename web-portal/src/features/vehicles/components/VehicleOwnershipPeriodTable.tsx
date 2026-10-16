/**
 * A vehicle's owners over time (VEH-02, DM-22): one row per period, read
 * from the backend's period view; the open period is the current owner.
 */
import type { Schema } from "@g3/api-client";
import { Table, type TableColumnsType, Tag } from "antd";
import { useTranslation } from "react-i18next";

import { formatVietnamDate } from "../../../shared/lib/vietnamTime";
import { QueryState } from "../../../shared/ui/QueryState";
import { EmptyState } from "../../../shared/ui/states";
import { OrganizationName } from "../../identity";
import { useVehicleOwnershipPeriodListQuery } from "../api/queries";

/** One ownership period. */
type OwnershipPeriod = Schema<"VehicleOwnershipPeriodResponse">;

/**
 * Props of `VehicleOwnershipPeriodTable`.
 *
 * Attributes:
 *   vehicleId: The vehicle.
 */
interface VehicleOwnershipPeriodTableProps {
  vehicleId: string;
}

/** The ownership history table, with its own loading and error states. */
export function VehicleOwnershipPeriodTable({
  vehicleId,
}: VehicleOwnershipPeriodTableProps) {
  const { t } = useTranslation("vehicles");
  const ownershipPeriodListQuery =
    useVehicleOwnershipPeriodListQuery(vehicleId);

  const columns: TableColumnsType<OwnershipPeriod> = [
    {
      key: "organization_id",
      title: t("fields.owner"),
      render: (_, period) => (
        <OrganizationName organizationId={period.organization_id} />
      ),
    },
    {
      key: "owned_from",
      title: t("detail.ownedFrom"),
      render: (_, period) => formatVietnamDate(period.owned_from),
    },
    {
      key: "owned_until",
      title: t("detail.ownedUntil"),
      render: (_, period) =>
        period.owned_until === null || period.owned_until === undefined ? (
          <Tag color="processing">{t("detail.current")}</Tag>
        ) : (
          formatVietnamDate(period.owned_until)
        ),
    },
  ];

  return (
    <QueryState query={ownershipPeriodListQuery}>
      {(ownershipPeriods) => (
        <Table<OwnershipPeriod>
          columns={columns}
          dataSource={ownershipPeriods.items}
          rowKey={(period) => `${period.organization_id}-${period.owned_from}`}
          pagination={false}
          locale={{
            emptyText: (
              <EmptyState description={t("detail.noOwnershipPeriods")} />
            ),
          }}
        />
      )}
    </QueryState>
  );
}
