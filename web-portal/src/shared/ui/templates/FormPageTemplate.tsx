/**
 * Create / edit form template (web-portal-ui.md §5): `PageHeader`, then one
 * `Card` holding a vertical Ant Design `Form` (labels above, required marks,
 * help text from i18n, Save / Cancel at the bottom). The form is narrower than
 * the page so lines stay readable.
 */
import { Card, Col, Flex, Row } from "antd";
import type { ReactNode } from "react";

/**
 * Props of `FormPageTemplate`.
 *
 * Attributes:
 *   header: The `PageHeader` (with breadcrumbs).
 *   children: The form (or a `Skeleton` while the record loads).
 */
interface FormPageTemplateProps {
  header: ReactNode;
  children: ReactNode;
}

/** Header and a card with the form. */
export function FormPageTemplate({ header, children }: FormPageTemplateProps) {
  return (
    <Flex vertical gap="middle">
      {header}
      <Row>
        <Col xs={24} lg={16} xl={12}>
          <Card>{children}</Card>
        </Col>
      </Row>
    </Flex>
  );
}
