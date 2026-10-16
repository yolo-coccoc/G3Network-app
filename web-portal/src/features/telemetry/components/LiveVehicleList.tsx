/**
 * The side list of the live map: every truck in scope with its online state
 * and the time of its newest data. Selecting a truck here selects it on the
 * map, and the other way round.
 */
import { Flex, Menu, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { formatVietnamDateTime } from "../../../shared/lib/vietnamTime";
import { toOnlineStatus } from "../../../shared/ui/status";
import { StatusTag } from "../../../shared/ui/StatusTag";
import { type LiveVehicle, toLiveVehicleLabel } from "./liveVehicles";

/**
 * Props of `LiveVehicleList`.
 *
 * Attributes:
 *   vehicles: The trucks.
 *   selectedVehicleId: The highlighted truck.
 *   onSelect: Called with a truck's ID when it is clicked.
 */
interface LiveVehicleListProps {
  vehicles: LiveVehicle[];
  selectedVehicleId: string | undefined;
  onSelect: (vehicleId: string) => void;
}

/** Trucks as a selectable list. */
export function LiveVehicleList({
  vehicles,
  selectedVehicleId,
  onSelect,
}: LiveVehicleListProps) {
  const { t } = useTranslation("telemetry");

  return (
    <Flex vertical gap="small">
      <Typography.Title level={5}>{t("live.vehicles")}</Typography.Title>
      <Menu
        mode="inline"
        inlineIndent={0}
        selectedKeys={
          selectedVehicleId === undefined ? [] : [selectedVehicleId]
        }
        onClick={({ key }) => {
          onSelect(key);
        }}
        items={vehicles.map((vehicle) => ({
          key: vehicle.vehicle_id,
          label: (
            <Flex justify="space-between" align="center" gap="small">
              <Flex vertical>
                <Typography.Text strong>
                  {toLiveVehicleLabel(vehicle)}
                </Typography.Text>
                <Typography.Text type="secondary">
                  {vehicle.recorded_at === null ||
                  vehicle.recorded_at === undefined
                    ? t("live.noData")
                    : t("live.recordedAt", {
                        time: formatVietnamDateTime(vehicle.recorded_at),
                      })}
                </Typography.Text>
              </Flex>
              <StatusTag value={toOnlineStatus(vehicle.is_online)} />
            </Flex>
          ),
        }))}
      />
    </Flex>
  );
}
