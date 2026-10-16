/**
 * The portal shell (web-portal-conventions.md §4): brand, tab bar,
 * organization switcher, language switch and account menu around every page
 * of the signed-in area (`/_authed`). The login and organization picker pages
 * render without it.
 *
 * It wraps the router's output (`InnerWrap`), so route files never import
 * `app/` (the import direction is routes → features → shared).
 */
import { DownOutlined, UserOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import {
  useMatches,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { Button, Dropdown, Menu, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { OrganizationSwitcher } from "../features/identity";
import { endSession } from "../shared/auth/session";
import { type Me, useSessionStore } from "../shared/auth/sessionStore";
import { AppLayout } from "../shared/ui/AppLayout";
import { LanguageSwitch } from "../shared/ui/LanguageSwitch";
import { activePagePathOf, resolveHomePath, visibleTabsOf } from "./tabs";

/**
 * The tab bar: one entry per visible tab; a tab with several pages opens a
 * submenu of them.
 */
function TabBar({ features }: { features: readonly string[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const activePagePath = activePagePathOf(pathname);

  return (
    <Menu
      mode="horizontal"
      disabledOverflow
      selectedKeys={activePagePath === undefined ? [] : [activePagePath]}
      items={visibleTabsOf(features).map(({ tab, pages }) =>
        pages.length === 1 && pages[0] !== undefined
          ? { key: pages[0].path, label: t(tab.labelKey) }
          : {
              key: tab.id,
              label: t(tab.labelKey),
              children: pages.map((page) => ({
                key: page.path,
                label: t(page.labelKey),
              })),
            },
      )}
      onClick={({ key }) => {
        void navigate({ href: key });
      }}
    />
  );
}

/** The account menu: the user's name and logout. */
function AccountMenu({ me }: { me: Me }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  return (
    <Dropdown
      menu={{
        items: [{ key: "logout", label: t("actions.logout") }],
        onClick: () => {
          void endSession().finally(() => {
            queryClient.clear();
            void navigate({ to: "/login" });
          });
        },
      }}
    >
      <Button
        type="text"
        icon={<UserOutlined />}
        aria-label={t("header.account")}
      >
        {me.user.full_name}
        <DownOutlined />
      </Button>
    </Dropdown>
  );
}

/**
 * Wrap the router's output with the shell when the page is in the signed-in area.
 *
 * @param children - The matched route's content.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const me = useSessionStore((state) => state.me);
  const isSignedInArea = useMatches({
    select: (matches) =>
      matches.some((match) => match.routeId.startsWith("/_authed")),
  });

  if (!isSignedInArea || me === null) {
    return <>{children}</>;
  }
  return (
    <AppLayout
      brand={<Typography.Text strong>{t("appName")}</Typography.Text>}
      navigation={<TabBar features={me.features} />}
      actions={
        <>
          <OrganizationSwitcher
            me={me}
            onSwitched={(switchedMe) => {
              void navigate({ href: resolveHomePath(switchedMe.features) });
            }}
          />
          <LanguageSwitch />
          <AccountMenu me={me} />
        </>
      }
    >
      {children}
    </AppLayout>
  );
}
