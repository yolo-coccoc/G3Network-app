/**
 * The list table (web-portal-ui.md §5, web-portal-conventions.md §9): Ant
 * Design's Table with server-side pagination. Page and page size live in the
 * route's search params; the route passes them in and gets changes back. The
 * table shows its own loading row and the given empty state.
 */
import { Table, type TableColumnsType } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** Page sizes offered; the backend's maximum is 100. */
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

/**
 * Props of `DataTable`.
 *
 * Attributes:
 *   columns: Identifier column first, status as `StatusTag`, row actions last.
 *   rows: The current page's rows, or nothing while the first page loads.
 *   rowKey: The field that identifies a row (its `_id`).
 *   total: Rows over every page, as the API's `total`.
 *   page: The current page (1-based).
 *   pageSize: Rows per page.
 *   isLoading: Show the table's loading row.
 *   empty: What to show when there is no row (an `EmptyState`).
 *   onPageChange: Called with the new page and page size.
 */
interface DataTableProps<Row extends object> {
  columns: TableColumnsType<Row>;
  rows: Row[] | undefined;
  rowKey: keyof Row & string;
  total: number;
  page: number;
  pageSize: number;
  isLoading: boolean;
  empty: ReactNode;
  onPageChange: (page: number, pageSize: number) => void;
}

/** A server-paginated table. */
export function DataTable<Row extends object>({
  columns,
  rows,
  rowKey,
  total,
  page,
  pageSize,
  isLoading,
  empty,
  onPageChange,
}: DataTableProps<Row>) {
  const { t } = useTranslation();
  return (
    <Table<Row>
      columns={columns}
      dataSource={rows}
      rowKey={rowKey}
      loading={isLoading}
      locale={{ emptyText: empty }}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: true,
        pageSizeOptions: PAGE_SIZE_OPTIONS,
        showTotal: (count) => t("states.total", { total: count }),
      }}
      onChange={(pagination) => {
        onPageChange(pagination.current ?? 1, pagination.pageSize ?? pageSize);
      }}
    />
  );
}
