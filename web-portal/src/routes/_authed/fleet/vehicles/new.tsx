/**
 * `/fleet/vehicles/new` (VEH-01), the Create form sample page: register a
 * vehicle, then open it.
 */
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import {
  VEHICLE_WRITE_FEATURES,
  VehicleForm,
} from "../../../../features/vehicles";
import { requireFeature } from "../../../../shared/auth/guards";
import { PageHeader } from "../../../../shared/ui/PageHeader";
import { FormPageTemplate } from "../../../../shared/ui/templates/FormPageTemplate";

export const Route = createFileRoute("/_authed/fleet/vehicles/new")({
  beforeLoad: ({ context }) => {
    requireFeature(context.me, VEHICLE_WRITE_FEATURES);
  },
  component: VehicleCreatePage,
});

/** The register-a-vehicle page. */
function VehicleCreatePage() {
  const { t } = useTranslation("vehicles");
  const navigate = useNavigate();

  return (
    <FormPageTemplate
      header={
        <PageHeader
          title={t("form.createTitle")}
          breadcrumbs={[
            <Link key="list" to="/fleet/vehicles">
              {t("list.title")}
            </Link>,
            t("form.createTitle"),
          ]}
        />
      }
    >
      <VehicleForm
        onSaved={(vehicle) => {
          void navigate({
            to: "/vehicles/$vehicleId",
            params: { vehicleId: vehicle.vehicle_id },
          });
        }}
        onCancel={() => {
          void navigate({ to: "/fleet/vehicles" });
        }}
      />
    </FormPageTemplate>
  );
}
