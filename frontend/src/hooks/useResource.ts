/**
 * A small async-data hook: loading, error, data, refetch.
 *
 * docs/architecture.md section 10 planned a query library here. This app has
 * three screens and a dozen endpoints, and every screen loads its data once and
 * refetches after an action it triggered itself — there is no cache to share
 * between screens and no background invalidation to coordinate. Configuring a
 * query library would have been more code than this, so the deviation is
 * deliberate and recorded in the roadmap rather than silent.
 *
 * The two things that actually matter are handled: a request is aborted when
 * the component unmounts or its inputs change, and a response that arrives
 * after that is dropped rather than written into unmounted state.
 */

import { useCallback, useEffect, useState } from "react";

export type Resource<T> =
  { state: "loading" } | { state: "error"; error: Error } | { state: "ready"; data: T };

export interface UseResource<T> {
  resource: Resource<T>;
  /** Re-run the loader. Used after an action that changed the data. */
  reload: () => void;
}

/**
 * Load `loader` when `deps` change.
 *
 * `loader` is called with an AbortSignal and should pass it to fetch, so an
 * abandoned request does not hold a connection open.
 */
export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): UseResource<T> {
  const [resource, setResource] = useState<Resource<T>>({ state: "loading" });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let live = true;

    setResource({ state: "loading" });
    loader(controller.signal)
      .then((data) => {
        if (live) setResource({ state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted) return;
        setResource({
          state: "error",
          error: error instanceof Error ? error : new Error("Unknown error"),
        });
      });

    return () => {
      live = false;
      controller.abort();
    };
    // `loader` is intentionally not a dependency: callers write it inline, so
    // including it would re-run on every render. `deps` is the caller's
    // explicit statement of what the load depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { resource, reload };
}

/**
 * Run a one-off action, tracking whether it is in flight and how it failed.
 *
 * Separate from `useResource` because an action is triggered by a person rather
 * than by a render, and its error belongs next to the button they pressed.
 */
export function useAction(): {
  busy: boolean;
  error: Error | null;
  run: (action: () => Promise<unknown>) => Promise<boolean>;
  clearError: () => void;
} {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const run = useCallback(async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught : new Error("Unknown error"));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return { busy, error, run, clearError };
}
