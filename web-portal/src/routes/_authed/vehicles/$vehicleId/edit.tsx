/**
 * `/vehicles/$vehicleId/edit` (VEH-01), the Edit form sample page.
 */
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  useVehicleQuery,
  VEHICLE_WRITE_FEATURES,
  VehicleForm,
} from "../../../../features/vehicles";
import { requireFeature } from "../../../../shared/auth/guards";
import { PageHeader } from "../../../../shared/ui/PageHeader";
import { QueryState } from "../../../../shared/ui/QueryState";
import { FormPageTemplate } from "../../../../shared/ui/templates/FormPageTemplate";

export const Route = createFileRoute("/_authed/vehicles/$vehicleId/edit")({
  beforeLoad: ({ context }) => {
    requireFeature(context.me, VEHICLE_WRITE_FEATURES);
  },
  component: VehicleEditPage,
});

/** The edit-a-vehicle page. */
function VehicleEditPage() {
  const { t } = useTranslation("vehicles");
  const { vehicleId } = Route.useParams();
  const navigate = useNavigate();
  const vehicleQuery = useVehicleQuery(vehicleId);
  const plate = vehicleQuery.data?.license_plate ?? "";
  const openDetail = () => {
    void navigate({ to: "/vehicles/$vehicleId", params: { vehicleId } });
  };

  return (
    <FormPageTemplate
      header={
        <PageHeader
          title={t("form.editTitle", { plate })}
          breadcrumbs={[
            <Link key="list" to="/fleet/vehicles">
              {t("list.title")}
            </Link>,
            <Link key="detail" to="/vehicles/$vehicleId" params={{ vehicleId }}>
              {plate}
            </Link>,
            t("form.editTitle", { plate }),
          ]}
        />
      }
    >
      <QueryState query={vehicleQuery}>
        {(vehicle) => (
          <VehicleForm
            vehicle={vehicle}
            onSaved={openDetail}
            onCancel={openDetail}
          />
        )}
      </QueryState>
    </FormPageTemplate>
  );
}
