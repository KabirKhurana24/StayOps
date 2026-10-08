/**
 * Guest portal access token handling (pure, unit-tested).
 *
 * The host shares `https://…/guest#token=<token>`. The token travels in the URL
 * *fragment*, which browsers never send to servers (no access logs, no Referer).
 * On first load the portal moves it into localStorage and strips it from the URL.
 */

export const GUEST_TOKEN_KEY = "stayops.guestToken";
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,200}$/;

export function tokenFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const token = params.get("token");
  return token && TOKEN_PATTERN.test(token) ? token : null;
}

export const isPlausibleToken = (token: string | null | undefined): token is string => Boolean(token && TOKEN_PATTERN.test(token));

/** Google Maps directions to the property (opens the guest's maps app on mobile). */
export const directionsUrl = (address: string) => `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(address)}`;

/** "15:00" from "15:00:00". */
export const shortTime = (t: string) => t.slice(0, 5);
