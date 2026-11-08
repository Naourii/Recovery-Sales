import type { ZodType } from "zod";
import { ErrorEnvelopeSchema } from "./schemas";

const BASE = "/api/backend"; // proxied to BACKEND_URL by the Next.js route handler

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(
    0,
    "unknown_error",
    err instanceof Error ? err.message : "An unexpected error occurred.",
  );
}

type Options = {
  signal?: AbortSignal;
  /** Non-2xx statuses whose body should still be parsed with the schema (e.g. 503 from /health). */
  acceptStatuses?: number[];
};

async function request<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  schema: ZodType<T>,
  body: unknown,
  opts: Options,
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      signal: opts.signal,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (opts.signal?.aborted) throw err; // caller cancelled; not an error to display
    throw new ApiError(0, "network_error", "Could not reach the dashboard server.");
  }

  const text = await res.text();
  let json: unknown;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = undefined; // handled below
    }
  }

  const accepted = res.ok || opts.acceptStatuses?.includes(res.status);
  if (!accepted) {
    const envelope = ErrorEnvelopeSchema.safeParse(json);
    if (envelope.success) {
      const { code, message, details } = envelope.data.error;
      throw new ApiError(res.status, code, message, details);
    }
    throw new ApiError(res.status, "http_error", `Request failed with status ${res.status}.`);
  }

  if (json === undefined) {
    throw new ApiError(res.status, "invalid_response", "The backend returned an empty or non-JSON response.");
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new ApiError(
      res.status,
      "invalid_response",
      "The backend returned data in an unexpected format.",
      parsed.error.issues,
    );
  }
  return parsed.data;
}

export const apiGet = <T>(path: string, schema: ZodType<T>, opts: Options = {}) =>
  request("GET", path, schema, undefined, opts);

export const apiPost = <T>(path: string, schema: ZodType<T>, body: unknown = {}, opts: Options = {}) =>
  request("POST", path, schema, body, opts);
export const apiPut = <T>(path: string, schema: ZodType<T>, body: unknown = {}, opts: Options = {}) =>
  request("PUT", path, schema, body, opts);