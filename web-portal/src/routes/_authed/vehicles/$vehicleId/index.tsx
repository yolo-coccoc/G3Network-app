/**
 * `/vehicles/$vehicleId` (VEH-01), the Detail template's sample page and the
 * one shared detail page of a vehicle: summary, ownership history, and Edit /
 * Remove for writers.
 */
import { EditOutlined } from "@ant-design/icons";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Button } from "antd";
import { useTranslation } from "react-i18next";

import {
  useVehicleQuery,
  VEHICLE_READ_FEATURES,
  VEHICLE_WRITE_FEATURES,
  VehicleOwnershipPeriodTable,
  VehicleRemoveButton,
  VehicleSummary,
} from "../../../../features/vehicles";
import { Can } from "../../../../shared/auth/Can";
import { requireFeature } from "../../../../shared/auth/guards";
import { PageHeader } from "../../../../shared/ui/PageHeader";
import { QueryState } from "../../../../shared/ui/QueryState";
import { StatusTag } from "../../../../shared/ui/StatusTag";
import { DetailPageTemplate } from "../../../../shared/ui/templates/DetailPageTemplate";

export const Route = createFileRoute("/_authed/vehicles/$vehicleId/")({
  beforeLoad: ({ context }) => {
    requireFeature(context.me, VEHICLE_READ_FEATURES);
  },
  component: VehicleDetailPage,
});

/** The vehicle detail page. */
function VehicleDetailPage() {
  const { t } = useTranslation("vehicles");
  const { t: tCommon } = useTranslation();
  const { vehicleId } = Route.useParams();
  const navigate = useNavigate();
  const vehicleQuery = useVehicleQuery(vehicleId);
  const vehicle = vehicleQuery.data;

  return (
    <DetailPageTemplate
      header={
        <PageHeader
          title={vehicle?.license_plate ?? ""}
          status={vehicle && <StatusTag value={vehicle.status} />}
          breadcrumbs={[
            <Link key="list" to="/fleet/vehicles">
              {t("list.title")}
            </Link>,
            vehicle?.license_plate,
          ]}
          actions={
            vehicle && (
              <Can feature={VEHICLE_WRITE_FEATURES}>
                <Button
                  icon={<EditOutlined />}
                  onClick={() => {
                    void navigate({
                      to: "/vehicles/$vehicleId/edit",
                      params: { vehicleId },
                    });
                  }}
                >
                  {tCommon("actions.edit")}
                </Button>
                <VehicleRemoveButton
                  vehicleId={vehicleId}
                  onRemoved={() => {
                    void navigate({ to: "/fleet/vehicles" });
                  }}
                />
              </Can>
            )
          }
        />
      }
      summary={
        <QueryState query={vehicleQuery}>
          {(loadedVehicle) => <VehicleSummary vehicle={loadedVehicle} />}
        </QueryState>
      }
      tabs={
        vehicle && [
          {
            key: "ownership",
            label: t("detail.ownershipTab"),
            children: <VehicleOwnershipPeriodTable vehicleId={vehicleId} />,
          },
        ]
      }
    />
  );
}
