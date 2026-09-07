/**
 * A hash router in thirty lines.
 *
 * Three routes, no nested layouts, no loaders, no code splitting. A routing
 * library would be a dependency and a configuration file to earn its keep here,
 * and hash routing has one concrete advantage for this project: the production
 * build is a static bundle that works from any path with no server rewrite rule.
 */

import { useEffect, useState } from "react";

export type Route =
  | { name: "jobs" }
  | { name: "job"; jobId: string }
  | { name: "candidate"; candidateId: string }
  | { name: "unknown"; path: string };

/** Parse a location hash into a route. Exported so it can be tested directly. */
export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, "").replace(/\/+$/, "");
  if (path === "" || path === "jobs") return { name: "jobs" };

  const segments = path.split("/");
  if (segments.length === 2 && segments[0] === "jobs" && segments[1]) {
    return { name: "job", jobId: segments[1] };
  }
  if (segments.length === 2 && segments[0] === "candidates" && segments[1]) {
    return { name: "candidate", candidateId: segments[1] };
  }
  return { name: "unknown", path };
}

/** Build a hash href for a route. Keeps link construction in one place. */
export const href = {
  jobs: () => "#/jobs",
  job: (jobId: string) => `#/jobs/${jobId}`,
  candidate: (candidateId: string) => `#/candidates/${candidateId}`,
};

/** Navigate without a full page load. */
export function navigate(to: string): void {
  window.location.hash = to.startsWith("#") ? to : `#${to}`;
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return route;
}
