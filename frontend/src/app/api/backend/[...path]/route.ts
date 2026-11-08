import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const ALLOWED_ROOTS = new Set(["health", "leads", "messages", "analytics", "crm"]);
const TIMEOUT_MS = 30_000;

function errorResponse(status: number, code: string, message: string) {
  return Response.json(
    { error: { code, message } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const backend = process.env.BACKEND_URL?.trim().replace(/\/+$/, "");
  if (!backend) {
    return errorResponse(
      500,
      "backend_not_configured",
      "BACKEND_URL is not set. Create .env.local (see the setup notes) and restart the dev server.",
    );
  }

  const { path } = await ctx.params;
  if (path.length === 0 || !ALLOWED_ROOTS.has(path[0])) {
    return errorResponse(404, "not_found", "Unknown API path.");
  }

  const target = `${backend}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers({ Accept: "application/json" });
  const contentType = req.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  const apiKey = process.env.BACKEND_API_KEY?.trim();
  if (apiKey) headers.set("X-API-Key", apiKey); // stays on the server

  try {
    const upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const noBody = [204, 205, 304].includes(upstream.status);
    return new Response(noBody ? null : await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return errorResponse(
      502,
      "backend_unreachable",
      "Could not reach the backend. Is the FastAPI server running at BACKEND_URL?",
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PUT };