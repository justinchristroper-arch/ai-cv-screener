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
 * **A refetch keeps the data it already has.** This is the load-bearing detail.
 * An earlier version reset to `loading` on every reload, which meant a caller
 * rendering a spinner for that state tore its whole subtree down and rebuilt it
 * after any refresh — so editing one requirement blanked the page, reset the
 * scroll position, and refetched three unrelated endpoints. `loading` now means
 * "nothing to show yet"; a reload over existing data is `refreshing`, and the
 * previous data stays on screen while it happens.
 *
 * The two things that always mattered are still handled: a request is aborted
 * when the component unmounts or its inputs change, and a response that arrives
 * after that is dropped rather than written into unmounted state.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type Resource<T> =
  { state: "loading" } | { state: "error"; error: Error } | { state: "ready"; data: T };

export interface UseResource<T> {
  resource: Resource<T>;
  /** True while a reload runs over data that is already on screen. */
  refreshing: boolean;
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
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  // Read in the effect without being a dependency of it: a reload must not
  // reset to `loading` just because data happens to be present.
  const hasData = useRef(false);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let live = true;

    if (hasData.current) {
      setRefreshing(true);
    } else {
      setResource({ state: "loading" });
    }

    loader(controller.signal)
      .then((data) => {
        if (!live) return;
        hasData.current = true;
        setResource({ state: "ready", data });
      })
      .catch((error: unknown) => {
        if (!live || controller.signal.aborted) return;
        // A failed refresh replaces the stale data rather than hiding the
        // failure behind it: showing numbers that are known to be out of date,
        // with no indication, would be worse than showing the error.
        hasData.current = false;
        setResource({
          state: "error",
          error: error instanceof Error ? error : new Error("Unknown error"),
        });
      })
      .finally(() => {
        if (live) setRefreshing(false);
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

  // A change of inputs is a different resource, not a refresh of this one.
  useEffect(() => {
    return () => {
      hasData.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { resource, refreshing, reload };
}

/**
 * Run a one-off action, tracking whether it is in flight and how it failed.
 *
 * Separate from `useResource` because an action is triggered by a person rather
 * than by a render, and its error belongs next to the button they pressed.
 *
 * `pending` names *what* is in flight, so a panel with many rows can disable the
 * one row being saved instead of every control on the panel — disabling them all
 * blurs whatever the person was using.
 */
export function useAction(): {
  busy: boolean;
  pending: string | null;
  error: Error | null;
  run: (action: () => Promise<unknown>, key?: string) => Promise<boolean>;
  clearError: () => void;
} {
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const run = useCallback(async (action: () => Promise<unknown>, key?: string) => {
    setBusy(true);
    setPending(key ?? null);
    setError(null);
    try {
      await action();
      return true;
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught : new Error("Unknown error"));
      return false;
    } finally {
      setBusy(false);
      setPending(null);
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return { busy, pending, error, run, clearError };
}
