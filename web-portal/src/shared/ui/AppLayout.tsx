/**
 * The frame of every signed-in page: a header with the brand, the tab bar and
 * the user's controls, and the content area. The app's shell fills the slots;
 * spacing and colors come from theme tokens.
 */
import { Flex, Layout, theme } from "antd";
import type { ReactNode } from "react";

/**
 * Props of `AppLayout`.
 *
 * Attributes:
 *   brand: The product name or logo, left.
 *   navigation: The tab bar, after the brand.
 *   actions: Organization switcher, language, account menu, right.
 *   children: The page.
 */
interface AppLayoutProps {
  brand: ReactNode;
  navigation: ReactNode;
  actions: ReactNode;
  children: ReactNode;
}

/** Header plus content area. */
export function AppLayout({
  brand,
  navigation,
  actions,
  children,
}: AppLayoutProps) {
  const { token } = theme.useToken();
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header
        style={{
          display: "flex",
          alignItems: "center",
          gap: token.marginLG,
          background: token.colorBgContainer,
          borderBottom: `${String(token.lineWidth)}px ${token.lineType} ${token.colorBorderSecondary}`,
          paddingInline: token.paddingLG,
        }}
      >
        {brand}
        <Flex flex={1} style={{ minWidth: 0 }}>
          {navigation}
        </Flex>
        <Flex align="center" gap="middle">
          {actions}
        </Flex>
      </Layout.Header>
      <Layout.Content style={{ padding: token.paddingLG }}>
        {children}
      </Layout.Content>
    </Layout>
  );
}
