/**
 * Error mapping of API answers (web-portal-conventions.md §8, D15).
 *
 * The backend sends a domain error as `{"detail": "<English text>"}` with the
 * status of its base class, and a FastAPI validation error (422) as a
 * `detail` list with field locations. The portal shows a localized message per
 * status, with the backend text only as secondary detail (there are no
 * machine-readable error codes yet, deferred.md item 107), and puts 422
 * errors on the matching form fields.
 */

/** What kind of failure an API call ended in; each has its own message key. */
export type ApiErrorKind =
  | "badRequest"
  | "unauthenticated"
  | "forbidden"
  | "notFound"
  | "conflict"
  | "validation"
  | "locked"
  | "tooManyRequests"
  | "server"
  | "network";

/**
 * One field error of a 422 answer.
 *
 * Attributes:
 *   name: The field path inside the body or query (`["license_plate"]`).
 *   type: Pydantic's error type (`string_too_long`, `missing`...).
 *   context: Pydantic's limits for the type (`max_length`...), if any.
 *   message: Pydantic's English message, kept for logs only.
 */
export interface ApiFieldError {
  name: (string | number)[];
  type: string;
  context: Record<string, unknown>;
  message: string;
}

/**
 * A failed API call.
 *
 * Attributes:
 *   kind: The failure kind, from the status (or `network` with no answer).
 *   status: The HTTP status, `0` when the server was not reached.
 *   detail: The backend's English text for a domain error, if any.
 *   fieldErrors: The field errors of a 422 answer (empty otherwise).
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;
  readonly detail: string | undefined;
  readonly fieldErrors: ApiFieldError[];

  constructor(
    kind: ApiErrorKind,
    status: number,
    detail: string | undefined,
    fieldErrors: ApiFieldError[],
  ) {
    super(detail ?? kind);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
    this.fieldErrors = fieldErrors;
  }
}

/** Location prefixes FastAPI puts before the field name. */
const LOCATION_PREFIXES = new Set([
  "body",
  "query",
  "path",
  "header",
  "cookie",
]);

/**
 * Map an HTTP status to its failure kind.
 *
 * @param status - The response status (4xx or 5xx).
 * @returns The kind; an unknown 4xx is a bad request, every 5xx a server error.
 */
export function toApiErrorKind(status: number): ApiErrorKind {
  switch (status) {
    case 401:
      return "unauthenticated";
    case 403:
      return "forbidden";
    case 404:
      return "notFound";
    case 409:
      return "conflict";
    case 422:
      return "validation";
    case 423:
      return "locked";
    case 429:
      return "tooManyRequests";
    default:
      return status >= 500 ? "server" : "badRequest";
  }
}

/**
 * Read the field errors of a FastAPI validation body.
 *
 * @param detail - The `detail` value of the body.
 * @returns One entry per item that has a location; anything else is ignored.
 */
function toFieldErrors(detail: unknown): ApiFieldError[] {
  if (!Array.isArray(detail)) {
    return [];
  }
  return detail.flatMap((item: unknown) => {
    if (typeof item !== "object" || item === null) {
      return [];
    }
    const { loc, type, ctx, msg } = item as Record<string, unknown>;
    if (!Array.isArray(loc)) {
      return [];
    }
    const path = loc.filter(
      (part): part is string | number =>
        typeof part === "string" || typeof part === "number",
    );
    const name =
      typeof path[0] === "string" && LOCATION_PREFIXES.has(path[0])
        ? path.slice(1)
        : path;
    return [
      {
        name,
        type: typeof type === "string" ? type : "value_error",
        context:
          typeof ctx === "object" && ctx !== null
            ? (ctx as Record<string, unknown>)
            : {},
        message: typeof msg === "string" ? msg : "",
      },
    ];
  });
}

/**
 * Build the error of a failed answer.
 *
 * @param status - The response status.
 * @param body - The parsed body (openapi-fetch's `error`), if any.
 * @returns The error with its kind, the backend text and any field errors.
 */
export function toApiError(status: number, body: unknown): ApiError {
  const detail =
    typeof body === "object" && body !== null
      ? (body as Record<string, unknown>).detail
      : undefined;
  return new ApiError(
    toApiErrorKind(status),
    status,
    typeof detail === "string" ? detail : undefined,
    toFieldErrors(detail),
  );
}

/**
 * Turn anything a query or mutation threw into an `ApiError`.
 *
 * @param error - The thrown value; a non-API error means the server was not reached.
 * @returns The same `ApiError`, or a `network` one.
 */
export function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  return new ApiError("network", 0, undefined, []);
}

/** Message keys of field errors in the `common` namespace. */
export type FieldErrorMessageKey =
  | "fieldErrors.missing"
  | "fieldErrors.tooShort"
  | "fieldErrors.tooLong"
  | "fieldErrors.tooSmall"
  | "fieldErrors.tooLarge"
  | "fieldErrors.invalid";

/**
 * Choose the localized message of a field error.
 *
 * @param fieldError - One field error of a 422 answer.
 * @returns The message key and its values (the limit Pydantic reported).
 */
export function describeFieldError(fieldError: ApiFieldError): {
  key: FieldErrorMessageKey;
  values: Record<string, unknown>;
} {
  const { type, context } = fieldError;
  switch (type) {
    case "missing":
      return { key: "fieldErrors.missing", values: {} };
    case "string_too_short":
      return {
        key: "fieldErrors.tooShort",
        values: { min: context.min_length },
      };
    case "string_too_long":
      return {
        key: "fieldErrors.tooLong",
        values: { max: context.max_length },
      };
    case "greater_than_equal":
    case "greater_than":
      return {
        key: "fieldErrors.tooSmall",
        values: { min: context.ge ?? context.gt },
      };
    case "less_than_equal":
    case "less_than":
      return {
        key: "fieldErrors.tooLarge",
        values: { max: context.le ?? context.lt },
      };
    default:
      return { key: "fieldErrors.invalid", values: {} };
  }
}
