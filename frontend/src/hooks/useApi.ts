"use client";

import { useCallback, useEffect, useState } from "react";
import { type ApiError, toApiError } from "@/lib/api";

type Result<T> = { tick: number; data?: T; error?: ApiError };

export function useApi<T>(fetcher: (signal: AbortSignal) => Promise<T>) {
  const [tick, setTick] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetcher(controller.signal)
      .then((data) => setResult({ tick, data }))
      .catch((err) => {
        if (!controller.signal.aborted) setResult({ tick, error: toApiError(err) });
      });
    return () => controller.abort();
  }, [fetcher, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  return {
    data: result?.data,
    error: result?.error,
    loading: result === null || result.tick !== tick,
    reload,
  };
}