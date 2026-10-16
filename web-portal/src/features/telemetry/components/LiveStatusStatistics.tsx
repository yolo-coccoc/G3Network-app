/**
 * The statistic row of the live map: trucks in scope, online, offline, and
 * without a position yet. Online is derived by the backend from the newest
 * data (`is_online`).
 */
import { Card, Col, Statistic } from "antd";
import { useTranslation } from "react-i18next";

import { hasPosition, type LiveVehicle } from "./liveVehicles";

/**
 * Props of `LiveStatusStatistics`.
 *
 * Attributes:
 *   vehicles: The trucks fetched.
 *   total: Trucks in scope (may exceed the fetched page).
 */
interface LiveStatusStatisticsProps {
  vehicles: LiveVehicle[];
  total: number;
}

/** Four statistic cards, one column each. */
export function LiveStatusStatistics({
  vehicles,
  total,
}: LiveStatusStatisticsProps) {
  const { t } = useTranslation("telemetry");
  const onlineCount = vehicles.filter((vehicle) => vehicle.is_online).length;
  const statistics = [
    { key: "total", title: t("live.total"), value: total },
    { key: "online", title: t("live.online"), value: onlineCount },
    {
      key: "offline",
      title: t("live.offline"),
      value: vehicles.length - onlineCount,
    },
    {
      key: "noPosition",
      title: t("live.noPosition"),
      value: vehicles.filter((vehicle) => !hasPosition(vehicle)).length,
    },
  ];

  return statistics.map((statistic) => (
    <Col key={statistic.key} xs={12} lg={6}>
      <Card>
        <Statistic title={statistic.title} value={statistic.value} />
      </Card>
    </Col>
  ));
}
