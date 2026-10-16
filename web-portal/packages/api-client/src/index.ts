/**
 * Typed client for the G3 Network API (TT-28, web-portal-scaffold.md D3).
 *
 * `schema.d.ts` is generated from the backend's OpenAPI document by
 * `make api-client` and is never edited by hand; `make api-client-check`
 * (part of `make check`) fails when it no longer matches the backend.
 * This module only re-exports the generated types and wraps openapi-fetch.
 * Tokens, retries and error mapping belong to the application that creates
 * the client (the portal's `shared/api`), never to this package.
 */
import createClient, { type Client, type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type { Middleware } from "openapi-fetch";

/** A request or response body by its schema name, e.g. `Schema<"VehicleResponse">`. */
export type Schema<SchemaName extends keyof components["schemas"]> =
  components["schemas"][SchemaName];

/** The typed client; calls are written by path, e.g. `client.GET("/api/v1/auth/me")`. */
export type ApiClient = Client<paths>;

/**
 * Create a client for the API.
 *
 * @param options - openapi-fetch options; `baseUrl` is the origin the API is
 *   served from (empty for the same origin, as the portal uses, IS-13).
 * @returns A client typed by the generated `paths`; middleware (auth,
 *   errors) is added by the caller with `client.use(...)`.
 */
export function createApiClient(options: ClientOptions = {}): ApiClient {
  return createClient<paths>(options);
}
