/**
 * Feedback after a mutation (web-portal-ui.md §3, web-portal-conventions.md
 * §8): field errors of a 422 go on the matching form fields; anything else is
 * a toast with the localized message per status and the backend's text as
 * secondary detail.
 */
import { App, type FormInstance } from "antd";
import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { type ApiError, asApiError, describeFieldError } from "../api/errors";

/**
 * Build the user-facing message of a failed call.
 *
 * @param error - The failure.
 * @param translate - The `common` namespace's `t`.
 * @returns The localized message, followed by the backend text when there is one.
 */
function toErrorMessage(
  error: ApiError,
  translate: ReturnType<typeof useTranslation>["t"],
) {
  const message = translate(`errors.${error.kind}`);
  return error.detail === undefined
    ? message
    : `${message} ${translate("errors.detail", { detail: error.detail })}`;
}

/**
 * React hook: report a failed mutation, putting 422 errors on the form.
 *
 * @returns A function taking the thrown error and, optionally, the form whose
 *   fields the 422 errors belong to; field errors that match no form field,
 *   and every other failure, are shown as a toast.
 */
export function useReportApiError(): (
  error: unknown,
  form?: FormInstance,
) => void {
  const { message } = App.useApp();
  const { t } = useTranslation();

  return useCallback(
    (error: unknown, form?: FormInstance) => {
      const apiError = asApiError(error);
      if (apiError.kind === "validation" && form !== undefined) {
        const knownFields = apiError.fieldErrors.filter(
          (fieldError) => form.getFieldInstance(fieldError.name) !== undefined,
        );
        form.setFields(
          knownFields.map((fieldError) => {
            const { key, values } = describeFieldError(fieldError);
            return { name: fieldError.name, errors: [t(key, values)] };
          }),
        );
        if (
          knownFields.length === apiError.fieldErrors.length &&
          knownFields.length > 0
        ) {
          return;
        }
      }
      void message.error(toErrorMessage(apiError, t));
    },
    [message, t],
  );
}
