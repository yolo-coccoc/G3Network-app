/**
 * Live map / dashboard template (web-portal-ui.md §5): `PageHeader` with the
 * scope picker, a row of statistics, the map with a side list synced to it,
 * and the time of the last refresh (the screen polls, conventions §9).
 */
import { Card, Col, Flex, Row, theme, Typography } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatVietnamClock } from "../../lib/vietnamTime";

/**
 * Props of `LiveMapPageTemplate`.
 *
 * Attributes:
 *   header: The `PageHeader`; its actions hold the scope picker.
 *   statistics: `Statistic` cards (one `Col` each), or nothing before a scope is picked.
 *   map: The map.
 *   sideList: The list synced with the map.
 *   lastRefreshedAt: When the data was last fetched (ms), for the refresh line.
 *   notice: What to show instead of the map and list (pick a scope, error).
 */
interface LiveMapPageTemplateProps {
  header: ReactNode;
  statistics?: ReactNode;
  map: ReactNode;
  sideList: ReactNode;
  lastRefreshedAt?: number;
  notice?: ReactNode;
}

/** Header, statistics, map with side list, refresh time. */
export function LiveMapPageTemplate({
  header,
  statistics,
  map,
  sideList,
  lastRefreshedAt,
  notice,
}: LiveMapPageTemplateProps) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  // The map needs a fixed height; the side list scrolls within the same height.
  const panelHeight = `calc(100vh - ${String(token.controlHeightLG * 9)}px)`;

  return (
    <Flex vertical gap="middle">
      {header}
      {notice ?? (
        <>
          {statistics !== undefined && (
            <Row gutter={[token.margin, token.margin]}>{statistics}</Row>
          )}
          <Row gutter={[token.margin, token.margin]}>
            <Col xs={24} lg={16}>
              <Card
                styles={{
                  body: {
                    padding: 0,
                    height: panelHeight,
                    minHeight: token.controlHeightLG * 10,
                  },
                }}
              >
                {map}
              </Card>
            </Col>
            <Col xs={24} lg={8}>
              <Card
                styles={{
                  body: {
                    height: panelHeight,
                    minHeight: token.controlHeightLG * 10,
                    overflowY: "auto",
                  },
                }}
              >
                {sideList}
              </Card>
            </Col>
          </Row>
          {lastRefreshedAt !== undefined && lastRefreshedAt > 0 && (
            <Typography.Text type="secondary">
              {t("states.lastRefreshed", {
                time: formatVietnamClock(lastRefreshedAt),
              })}
            </Typography.Text>
          )}
        </>
      )}
    </Flex>
  );
}
