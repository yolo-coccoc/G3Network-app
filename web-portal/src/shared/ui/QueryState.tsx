/**
 * `<QueryState query={...}>{(data) => ...}</QueryState>`: the loading and
 * error states of one query in a page template's content area
 * (web-portal-ui.md §3). Loading is a `Skeleton`, never a blank page or a
 * full-page spinner; an error is the `ErrorState` with retry.
 */
import type { UseQueryResult } from "@tanstack/react-query";
import { Skeleton } from "antd";
import type { ReactNode } from "react";

import { ErrorState } from "./states";

/**
 * Props of `QueryState`.
 *
 * Attributes:
 *   query: The TanStack Query result.
 *   children: Renders the data once it is there.
 */
interface QueryStateProps<Data> {
  query: UseQueryResult<Data>;
  children: (data: Data) => ReactNode;
}

/** Skeleton while loading, error with retry on failure, else the content. */
export function QueryState<Data>({ query, children }: QueryStateProps<Data>) {
  if (query.isPending) {
    return <Skeleton active />;
  }
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }
  return children(query.data);
}
