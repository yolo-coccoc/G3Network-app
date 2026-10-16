/**
 * The header of every page (web-portal-ui.md §5): title, optional status tag
 * and actions, breadcrumbs on detail and form pages only. The title is also
 * the browser tab's title.
 */
import { Breadcrumb, Flex, Typography } from "antd";
import { type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";

/**
 * Props of `PageHeader`.
 *
 * Attributes:
 *   title: The page title (on a detail page, the record's identifier).
 *   status: A `StatusTag` shown next to the title, if the record has one.
 *   actions: Buttons on the right (primary action first).
 *   breadcrumbs: The path back, for detail and form pages; each item is a link
 *     or text built by the route.
 */
interface PageHeaderProps {
  title: string;
  status?: ReactNode;
  actions?: ReactNode;
  breadcrumbs?: ReactNode[];
}

/** Page title row with breadcrumbs, status and actions. */
export function PageHeader({
  title,
  status,
  actions,
  breadcrumbs,
}: PageHeaderProps) {
  const { t } = useTranslation();
  const appName = t("appName");

  useEffect(() => {
    document.title = title === "" ? appName : `${title} · ${appName}`;
  }, [title, appName]);

  return (
    <Flex vertical gap="small">
      {breadcrumbs !== undefined && breadcrumbs.length > 0 && (
        <Breadcrumb items={breadcrumbs.map((item) => ({ title: item }))} />
      )}
      <Flex justify="space-between" align="center" gap="middle" wrap>
        <Flex align="center" gap="small">
          <Typography.Title level={3} style={{ margin: 0 }}>
            {title}
          </Typography.Title>
          {status}
        </Flex>
        {actions !== undefined && (
          <Flex gap="small" align="center" wrap>
            {actions}
          </Flex>
        )}
      </Flex>
    </Flex>
  );
}
