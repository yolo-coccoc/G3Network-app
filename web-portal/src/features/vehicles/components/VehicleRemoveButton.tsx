/**
 * Remove a vehicle from the system (VEH-01): a soft delete that keeps its
 * data and sets it INACTIVE (DM-25). Destructive and audited, so it goes
 * through the reason dialog with a danger button (web-portal-ui.md §4).
 */
import { DeleteOutlined } from "@ant-design/icons";
import { App, Button } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ReasonDialog } from "../../../shared/ui/ReasonDialog";
import { useReportApiError } from "../../../shared/ui/feedback";
import { useSoftDeleteVehicleMutation } from "../api/mutations";

/**
 * Props of `VehicleRemoveButton`.
 *
 * Attributes:
 *   vehicleId: The vehicle to remove.
 *   onRemoved: Called after the removal (the route leaves the detail page).
 */
interface VehicleRemoveButtonProps {
  vehicleId: string;
  onRemoved: () => void;
}

/** A danger button that opens the reason dialog and removes the vehicle. */
export function VehicleRemoveButton({
  vehicleId,
  onRemoved,
}: VehicleRemoveButtonProps) {
  const { t } = useTranslation("vehicles");
  const { t: tCommon } = useTranslation();
  const { message } = App.useApp();
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const softDeleteVehicleMutation = useSoftDeleteVehicleMutation(vehicleId);
  const reportApiError = useReportApiError();

  return (
    <>
      <Button
        danger
        icon={<DeleteOutlined />}
        onClick={() => {
          setIsDialogOpen(true);
        }}
      >
        {tCommon("actions.delete")}
      </Button>
      <ReasonDialog
        open={isDialogOpen}
        isDanger
        title={t("delete.title")}
        description={t("delete.description")}
        confirmText={t("delete.confirm")}
        onCancel={() => {
          setIsDialogOpen(false);
        }}
        onConfirm={async (reason) => {
          try {
            await softDeleteVehicleMutation.mutateAsync(reason);
            setIsDialogOpen(false);
            void message.success(t("delete.success"));
            onRemoved();
          } catch (error) {
            reportApiError(error);
          }
        }}
      />
    </>
  );
}
