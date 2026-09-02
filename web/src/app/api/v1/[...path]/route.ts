import { NextRequest, NextResponse } from "next/server";

const BACKEND_ORIGIN =
  process.env.SECURGRAPH_API_ORIGIN ||
  process.env.NEXT_PUBLIC_API_DIRECT ||
  "http://127.0.0.1:8000";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "set-cookie",
  "transfer-encoding",
  "upgrade",
]);

// The backend resolves the caller's address from X-Forwarded-For, but only
// when the peer is a configured trusted proxy — this BFF. That is safe only
// if the value arriving here was not written by the browser: otherwise a
// caller picks a fresh address per request and never hits the login rate
// limit. A Next route handler exposes no peer address, so this proxy cannot
// append a real one; it drops what the caller claims and lets the backend
// fall back to the peer address.
//
// Set SECURGRAPH_TRUST_PROXY_HEADERS=true only when a reverse proxy that
// overwrites these headers sits in front of Next — then the chain reaching
// the backend is trustworthy and per-client limiting works end to end.
const FORWARDING_HEADERS = new Set(["x-forwarded-for", "x-real-ip", "forwarded"]);

function trustsInboundForwardingHeaders(): boolean {
  return process.env.SECURGRAPH_TRUST_PROXY_HEADERS === "true";
}

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function proxy(request: NextRequest, context: RouteContext) {
  const params = await context.params;
  const path = params.path.join("/");
  const target = new URL(`/api/v1/${path}${request.nextUrl.search}`, BACKEND_ORIGIN);
  const headers = new Headers();

  const trustForwarding = trustsInboundForwardingHeaders();
  for (const [key, value] of request.headers.entries()) {
    const lower = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(lower) || lower === "host") {
      continue;
    }
    if (!trustForwarding && FORWARDING_HEADERS.has(lower)) {
      continue;
    }
    headers.set(key, value);
  }

  const method = request.method.toUpperCase();
  const body =
    method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "Backend service unavailable." }, { status: 503 });
  }

  const responseHeaders = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      responseHeaders.set(key, value);
    }
  });

  const setCookies =
    (
      upstream.headers as Headers & {
        getSetCookie?: () => string[];
      }
    ).getSetCookie?.() ?? [];
  for (const cookie of setCookies) {
    responseHeaders.append("Set-Cookie", cookie);
  }

  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
