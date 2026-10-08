import { useCallback, useEffect, useRef, useState } from "react";

// Fetches once on mount (and whenever `deps` changes), tracking
// loading/error/data uniformly so every page handles these the same way.
export function useApi(fetcher, deps = []) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(() => {
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcherRef
      .current()
      .then((data) => {
        if (!cancelled) setState({ loading: false, error: null, data });
      })
      .catch((err) => {
        if (!cancelled) setState({ loading: false, error: err, data: null });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => load(), [load]);

  return { ...state, reload: load };
}
