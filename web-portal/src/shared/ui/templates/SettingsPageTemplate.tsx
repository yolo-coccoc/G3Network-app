/**
 * Settings template (web-portal-ui.md §5): `PageHeader`, then grouped `Card`s,
 * each one form section with its own Save (changes need a reason when the
 * table is tracked).
 */
import { Col, Flex, Row } from "antd";
import type { ReactNode } from "react";

/**
 * Props of `SettingsPageTemplate`.
 *
 * Attributes:
 *   header: The `PageHeader`.
 *   children: The section cards (or a `Skeleton` while loading).
 */
interface SettingsPageTemplateProps {
  header: ReactNode;
  children: ReactNode;
}

/** Header and a column of section cards. */
export function SettingsPageTemplate({
  header,
  children,
}: SettingsPageTemplateProps) {
  return (
    <Flex vertical gap="middle">
      {header}
      <Row>
        <Col xs={24} lg={16} xl={12}>
          <Flex vertical gap="middle">
            {children}
          </Flex>
        </Col>
      </Row>
    </Flex>
  );
}
