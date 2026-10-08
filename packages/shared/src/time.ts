/**
 * Timezone helpers. Instants travel as UTC ISO strings; wall-clock values are
 * always interpreted in the *property's* IANA timezone, never the browser's.
 */

function tzOffsetMs(utcMs: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(utcMs));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUtc = Date.UTC(
    get("year"),
    get("month") - 1,
    get("day"),
    get("hour"),
    get("minute"),
    get("second"),
  );
  return asUtc - utcMs;
}

/** "2026-10-05" + "15:00" in "Europe/Amsterdam" → "2026-10-05T13:00:00.000Z". */
export function zonedToUtcIso(date: string, time: string, timeZone: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  if (!y || !m || !d || hh === undefined || mm === undefined) {
    throw new Error(`invalid date/time: ${date} ${time}`);
  }
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  const first = tzOffsetMs(guess, timeZone);
  let utc = guess - first;
  const second = tzOffsetMs(utc, timeZone);
  if (second !== first) utc = guess - second; // crossed a DST boundary
  return new Date(utc).toISOString();
}

/** UTC instant → { date: "YYYY-MM-DD", time: "HH:MM" } in `timeZone`. */
export function utcToZoned(iso: string, timeZone: string): { date: string; time: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return { date: `${get("year")}-${get("month")}-${get("day")}`, time: `${get("hour")}:${get("minute")}` };
}

/** Today's date ("YYYY-MM-DD") in `timeZone`. */
export function todayIn(timeZone: string, now: Date = new Date()): string {
  return utcToZoned(now.toISOString(), timeZone).date;
}

export function addDays(date: string, days: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const dt = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, (d ?? 1) + days));
  return dt.toISOString().slice(0, 10);
}

export function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}
