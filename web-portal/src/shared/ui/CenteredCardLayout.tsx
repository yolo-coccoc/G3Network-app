/**
 * The frame of the pages outside the shell (login, organization picker): one
 * card in the middle of the screen. Not one of the five page templates
 * (web-portal-ui.md §5), which are for pages inside the tabs.
 */
import { Card, Flex, theme, Typography } from "antd";
import { type ReactNode, useEffect } from "react";
import { useTranslation } from "react-i18next";

/**
 * Props of `CenteredCardLayout`.
 *
 * Attributes:
 *   title: The card's title, also the browser title's first part.
 *   subtitle: One line under the title.
 *   children: The form or list.
 */
interface CenteredCardLayoutProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
}

/** A centered card on the layout background. */
export function CenteredCardLayout({
  title,
  subtitle,
  children,
}: CenteredCardLayoutProps) {
  const { token } = theme.useToken();
  const { t } = useTranslation();
  const appName = t("appName");

  useEffect(() => {
    document.title = `${title} · ${appName}`;
  }, [title, appName]);

  return (
    <Flex
      justify="center"
      align="center"
      style={{
        minHeight: "100vh",
        background: token.colorBgLayout,
        padding: token.padding,
      }}
    >
      <Card style={{ width: "100%", maxWidth: token.screenSM }}>
        <Flex vertical gap="middle">
          <Flex vertical>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {title}
            </Typography.Title>
            {subtitle !== undefined && (
              <Typography.Text type="secondary">{subtitle}</Typography.Text>
            )}
          </Flex>
          {children}
        </Flex>
      </Card>
    </Flex>
  );
}
