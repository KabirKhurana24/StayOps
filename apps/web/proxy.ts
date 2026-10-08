import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";

import { env, isSupabaseConfigured } from "@/lib/env";

const PUBLIC_PATHS = ["/login", "/signup", "/auth"];

/**
 * Refreshes the Supabase session cookie on every navigation and gates the app:
 * unauthenticated users are sent to /login. This is a UX gate only — the API
 * independently verifies the JWT and tenant membership on every request.
 */
export async function proxy(request: NextRequest) {
  const isPublic = PUBLIC_PATHS.some((p) => request.nextUrl.pathname.startsWith(p));
  if (!isSupabaseConfigured) {
    return isPublic ? NextResponse.next() : redirectTo(request, "/login");
  }

  let response = NextResponse.next({ request });
  const supabase = createServerClient(env.supabaseUrl, env.supabasePublishableKey, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet, headers) => {
        for (const { name, value } of toSet) request.cookies.set(name, value);
        response = NextResponse.next({ request });
        for (const { name, value, options } of toSet) response.cookies.set(name, value, options);
        for (const [key, value] of Object.entries(headers ?? {})) response.headers.set(key, value);
      },
    },
  });

  // getClaims() verifies the JWT signature (JWKS) instead of trusting the cookie.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);

  if (!signedIn && !isPublic) return redirectTo(request, "/login", response);
  if (signedIn && (request.nextUrl.pathname === "/login" || request.nextUrl.pathname === "/signup")) {
    return redirectTo(request, "/", response);
  }
  return response;
}

function redirectTo(request: NextRequest, pathname: string, carry?: NextResponse) {
  const url = request.nextUrl.clone();
  url.pathname = pathname;
  url.search = "";
  if (pathname === "/login" && request.nextUrl.pathname !== "/") {
    url.searchParams.set("next", request.nextUrl.pathname);
  }
  const redirect = NextResponse.redirect(url);
  carry?.cookies.getAll().forEach((cookie) => redirect.cookies.set(cookie));
  return redirect;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
