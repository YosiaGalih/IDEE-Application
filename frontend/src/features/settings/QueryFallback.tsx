import { ApiError } from "@/api/errors";
import { uxStateForError } from "@/api/uxState";
import { StateView } from "@/components/ui/StateView";

type Props = { query: { isPending: boolean; error: unknown; refetch: () => unknown } };

/** What a query without data shows: loading, or its error with a retry. */
export function QueryFallback({ query }: Props) {
  if (query.isPending) return <StateView state="loading" />;
  return (
    <StateView
      state={uxStateForError(query.error)}
      requestId={query.error instanceof ApiError ? query.error.requestId : null}
      onAction={() => void query.refetch()}
    />
  );
}
