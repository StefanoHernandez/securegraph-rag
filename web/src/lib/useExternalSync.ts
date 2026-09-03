"use client";

import { useState } from "react";

/**
 * Apply a value that comes from outside the component to local state, at the
 * moment it changes.
 *
 * The obvious spelling is an effect:
 *
 *     useEffect(() => { setLocal(external); }, [external]);
 *
 * which renders the stale value first and then schedules a second render to
 * correct it — the cascading render `react-hooks/set-state-in-effect` warns
 * about. React's documented alternative is to adjust state *during* the
 * render that observes the change: React discards the in-progress output and
 * re-runs the component immediately, before anything is committed, so the
 * stale value never reaches the DOM.
 *
 * `onChange` is deliberately not a dependency: it is read on the render where
 * the change is detected, so an inline arrow function works and no memoisation
 * is required at the call site.
 */
export function useExternalSync<T>(external: T, onChange: (value: T) => void): void {
  const [previous, setPrevious] = useState<T>(external);

  if (!Object.is(external, previous)) {
    setPrevious(external);
    onChange(external);
  }
}
