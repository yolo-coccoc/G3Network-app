/**
 * The standard data states (web-portal-ui.md §3): empty, error, no access and
 * not found. Loading is a `Skeleton` inside the template (`QueryState`) or a
 * table's own loading row; success feedback is `message.success`.
 */
import { Button, Empty, Result } from "antd";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { asApiError } from "../api/errors";

/**
 * Props of `EmptyState`.
 *
 * Attributes:
 *   description: One line saying why there is nothing.
 *   action: The primary action when the user may act ("Add a vehicle").
 */
interface EmptyStateProps {
  description: string;
  action?: ReactNode;
}

/** Nothing to show yet, with the action that fixes it. */
export function EmptyState({ description, action }: EmptyStateProps) {
  return (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description}>
      {action}
    </Empty>
  );
}

/**
 * Props of `ErrorState`.
 *
 * Attributes:
 *   error: What the query or mutation threw.
 *   onRetry: Retry the call; not offered for 403 and 404, where retrying cannot help.
 */
interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
}

/** A failed call: no permission (403), not found (404), or the mapped message with retry. */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const { t } = useTranslation();
  const apiError = asApiError(error);

  if (apiError.kind === "forbidden") {
    return <NoAccessState />;
  }
  if (apiError.kind === "notFound") {
    return (
      <Result
        status="404"
        title={t("errors.notFoundTitle")}
        subTitle={t("errors.notFound")}
      />
    );
  }
  return (
    <Result
      status="error"
      title={t("errors.errorTitle")}
      subTitle={
        apiError.detail === undefined
          ? t(`errors.${apiError.kind}`)
          : `${t(`errors.${apiError.kind}`)} ${t("errors.detail", { detail: apiError.detail })}`
      }
      extra={
        onRetry !== undefined && (
          <Button type="primary" onClick={onRetry}>
            {t("actions.retry")}
          </Button>
        )
      }
    />
  );
}

/** The caller lacks the feature (a route guard sent them here, or the API said 403). */
export function NoAccessState() {
  const { t } = useTranslation();
  return (
    <Result
      status="403"
      title={t("noAccess.title")}
      subTitle={t("noAccess.description")}
    />
  );
}

/**
 * Props of `NotFoundState`.
 *
 * Attributes:
 *   action: A way back (a link home).
 */
interface NotFoundStateProps {
  action?: ReactNode;
}

/** No page at this address. */
export function NotFoundState({ action }: NotFoundStateProps) {
  const { t } = useTranslation();
  return (
    <Result
      status="404"
      title={t("notFoundPage.title")}
      subTitle={t("notFoundPage.description")}
      extra={action}
    />
  );
}
