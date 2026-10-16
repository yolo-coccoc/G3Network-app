/**
 * Detail template (web-portal-ui.md §5): `PageHeader` (title = the record's
 * identifier, status tag, actions, breadcrumbs), the summary `Descriptions`,
 * then `Tabs` for related data (history, periods, related lists).
 */
import { Card, Flex, Tabs, type TabsProps } from "antd";
import type { ReactNode } from "react";

/**
 * Props of `DetailPageTemplate`.
 *
 * Attributes:
 *   header: The `PageHeader`.
 *   summary: The record's `Descriptions` (or a `Skeleton` while loading).
 *   tabs: Related data, one tab each; omitted when the record has none.
 */
interface DetailPageTemplateProps {
  header: ReactNode;
  summary: ReactNode;
  tabs?: TabsProps["items"];
}

/** Header, summary card and related-data tabs. */
export function DetailPageTemplate({
  header,
  summary,
  tabs,
}: DetailPageTemplateProps) {
  return (
    <Flex vertical gap="middle">
      {header}
      <Card>{summary}</Card>
      {tabs !== undefined && tabs.length > 0 && (
        <Card>
          <Tabs items={tabs} />
        </Card>
      )}
    </Flex>
  );
}
