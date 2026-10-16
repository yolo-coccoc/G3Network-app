/**
 * `/fleet/live` (FLT-04), the Live map template's sample page: pick a fleet
 * (in the URL), see its trucks on the map and in the list, polled every 15 s.
 * The route composes `fleet` (the picker) and `telemetry` (the positions), as
 * the backend's `telemetry → fleet` edge does.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Card, Skeleton } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  FLEET_LIVE_MAP_FEATURE,
  FleetScopePicker,
} from "../../../features/fleet";
import {
  LiveStatusStatistics,
  LiveVehicleList,
  LiveVehicleMap,
  useFleetLiveStatusListQuery,
} from "../../../features/telemetry";
import { requireFeature } from "../../../shared/auth/guards";
import {
  parseOptionalBoolean,
  parseOptionalText,
} from "../../../shared/lib/listSearch";
import { PageHeader } from "../../../shared/ui/PageHeader";
import { EmptyState, ErrorState } from "../../../shared/ui/states";
import { LiveMapPageTemplate } from "../../../shared/ui/templates/LiveMapPageTemplate";

/**
 * The live map's search params.
 *
 * Attributes:
 *   fleet_id: The fleet shown.
 *   include_descendants: Add the trucks of its sub-fleets.
 */
interface FleetLiveSearch {
  fleet_id?: string;
  include_descendants?: boolean;
}

export const Route = createFileRoute("/_authed/fleet/live")({
  validateSearch: (search: Record<string, unknown>): FleetLiveSearch => ({
    fleet_id: parseOptionalText(search.fleet_id),
    include_descendants: parseOptionalBoolean(search.include_descendants),
  }),
  beforeLoad: ({ context }) => {
    requireFeature(context.me, FLEET_LIVE_MAP_FEATURE);
  },
  component: FleetLiveMapPage,
});

/** The fleet live map page. */
function FleetLiveMapPage() {
  const { t } = useTranslation("telemetry");
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });
  const [selectedVehicleId, setSelectedVehicleId] = useState<string>();
  const includeDescendants = search.include_descendants ?? false;
  const liveStatusQuery = useFleetLiveStatusListQuery(
    search.fleet_id,
    includeDescendants,
  );
  const vehicles = liveStatusQuery.data?.items ?? [];

  let notice;
  if (search.fleet_id === undefined) {
    notice = (
      <Card>
        <EmptyState description={t("live.pickFleet")} />
      </Card>
    );
  } else if (liveStatusQuery.isPending) {
    notice = (
      <Card>
        <Skeleton active />
      </Card>
    );
  } else if (liveStatusQuery.isError) {
    notice = (
      <Card>
        <ErrorState
          error={liveStatusQuery.error}
          onRetry={() => {
            void liveStatusQuery.refetch();
          }}
        />
      </Card>
    );
  } else if (vehicles.length === 0) {
    notice = (
      <Card>
        <EmptyState description={t("live.noVehicles")} />
      </Card>
    );
  }

  return (
    <LiveMapPageTemplate
      header={
        <PageHeader
          title={t("live.title")}
          actions={
            <FleetScopePicker
              scope={{ fleetId: search.fleet_id, includeDescendants }}
              onChange={(scope) => {
                setSelectedVehicleId(undefined);
                void navigate({
                  search: {
                    fleet_id: scope.fleetId,
                    include_descendants: scope.includeDescendants
                      ? true
                      : undefined,
                  },
                });
              }}
            />
          }
        />
      }
      notice={notice}
      statistics={
        <LiveStatusStatistics
          vehicles={vehicles}
          total={liveStatusQuery.data?.total ?? 0}
        />
      }
      map={
        <LiveVehicleMap
          vehicles={vehicles}
          selectedVehicleId={selectedVehicleId}
          onSelect={setSelectedVehicleId}
        />
      }
      sideList={
        <LiveVehicleList
          vehicles={vehicles}
          selectedVehicleId={selectedVehicleId}
          onSelect={setSelectedVehicleId}
        />
      }
      lastRefreshedAt={liveStatusQuery.dataUpdatedAt}
    />
  );
}
