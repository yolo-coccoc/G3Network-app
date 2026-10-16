/**
 * List template (web-portal-ui.md §5): `PageHeader` (title, primary action),
 * filter bar (state in search params), then the `DataTable` (or the error
 * state in its place). Full content width.
 */
import { Card, Flex } from "antd";
import type { ReactNode } from "react";

/**
 * Props of `ListPageTemplate`.
 *
 * Attributes:
 *   header: The `PageHeader`.
 *   filters: The filter bar (search, selects, date range).
 *   children: The table, or the `ErrorState` when the list failed.
 */
interface ListPageTemplateProps {
  header: ReactNode;
  filters?: ReactNode;
  children: ReactNode;
}

/** Header, filters and table, stacked. */
export function ListPageTemplate({
  header,
  filters,
  children,
}: ListPageTemplateProps) {
  return (
    <Flex vertical gap="middle">
      {header}
      <Card>
        <Flex vertical gap="middle">
          {filters !== undefined && (
            <Flex gap="small" wrap>
              {filters}
            </Flex>
          )}
          {children}
        </Flex>
      </Card>
    </Flex>
  );
}
