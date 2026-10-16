/**
 * `/fleet/vehicles` (VEH-01), the List template's sample page: search and
 * status filter in the URL, server-paginated table, Add for writers.
 */
import { PlusOutlined } from "@ant-design/icons";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button } from "antd";
import { useTranslation } from "react-i18next";

import {
  parseVehicleListSearch,
  useVehicleListQuery,
  VEHICLE_READ_FEATURES,
  VEHICLE_WRITE_FEATURES,
  VehicleFilterBar,
  VehicleTable,
} from "../../../../features/vehicles";
import { Can } from "../../../../shared/auth/Can";
import { requireFeature } from "../../../../shared/auth/guards";
import { resolvePage } from "../../../../shared/lib/listSearch";
import { PageHeader } from "../../../../shared/ui/PageHeader";
import { ErrorState } from "../../../../shared/ui/states";
import { ListPageTemplate } from "../../../../shared/ui/templates/ListPageTemplate";

export const Route = createFileRoute("/_authed/fleet/vehicles/")({
  validateSearch: parseVehicleListSearch,
  beforeLoad: ({ context }) => {
    requireFeature(context.me, VEHICLE_READ_FEATURES);
  },
  component: VehicleListPage,
});

/** The vehicle list page. */
function VehicleListPage() {
  const { t } = useTranslation("vehicles");
  const search = Route.useSearch();
  const { page, page_size: pageSize } = resolvePage(search);
  const navigate = useNavigate({ from: Route.fullPath });
  const vehicleListQuery = useVehicleListQuery({
    ...search,
    page,
    page_size: pageSize,
  });

  return (
    <ListPageTemplate
      header={
        <PageHeader
          title={t("list.title")}
          actions={
            <Can feature={VEHICLE_WRITE_FEATURES}>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => {
                  void navigate({ to: "/fleet/vehicles/new" });
                }}
              >
                {t("list.add")}
              </Button>
            </Can>
          }
        />
      }
      filters={
        <VehicleFilterBar
          filters={{ q: search.q, status: search.status }}
          onChange={(filters) => {
            void navigate({ search: { ...search, ...filters, page: 1 } });
          }}
        />
      }
    >
      {vehicleListQuery.isError ? (
        <ErrorState
          error={vehicleListQuery.error}
          onRetry={() => {
            void vehicleListQuery.refetch();
          }}
        />
      ) : (
        <VehicleTable
          vehiclePage={vehicleListQuery.data}
          page={page}
          pageSize={pageSize}
          isLoading={vehicleListQuery.isFetching}
          isFiltered={search.q !== undefined || search.status !== undefined}
          onPageChange={(nextPage, nextPageSize) => {
            void navigate({
              search: { ...search, page: nextPage, page_size: nextPageSize },
            });
          }}
          onOpen={(vehicleId) => {
            void navigate({
              to: "/vehicles/$vehicleId",
              params: { vehicleId },
            });
          }}
        />
      )}
    </ListPageTemplate>
  );
}
