/**
 * Pure calendar geometry (no React, no imports) so it can be unit tested with
 * `node --test`. Dates are "YYYY-MM-DD" strings in a property's local calendar.
 *
 * Convention: a stay occupies the afternoon of its check-in day through the
 * morning of its check-out day, so in day units a bar spans
 * [checkInIndex + 0.5, checkOutIndex + 0.5). Back-to-back stays meet at the
 * midpoint of the turnover day instead of overlapping.
 */

export type CalendarMode = "month" | "week";

const DAY_MS = 86_400_000;

function toUtc(date: string): number {
  const [y, m, d] = date.split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function fromUtc(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(date: string, days: number): string {
  return fromUtc(toUtc(date) + days * DAY_MS);
}

export function diffDays(from: string, to: string): number {
  return Math.round((toUtc(to) - toUtc(from)) / DAY_MS);
}

/** 0 = Monday … 6 = Sunday. */
export function weekdayIndex(date: string): number {
  return (new Date(toUtc(date)).getUTCDay() + 6) % 7;
}

export function startOfWeek(date: string): string {
  return addDays(date, -weekdayIndex(date));
}

export function startOfMonth(date: string): string {
  return `${date.slice(0, 7)}-01`;
}

export function daysInMonth(date: string): number {
  const [y, m] = date.split("-").map(Number);
  return new Date(Date.UTC(y ?? 1970, m ?? 1, 0)).getUTCDate();
}

/** The visible range for a mode, anchored on any date inside it. */
export function visibleRange(anchor: string, mode: CalendarMode): { start: string; days: number } {
  if (mode === "week") return { start: startOfWeek(anchor), days: 7 };
  return { start: startOfMonth(anchor), days: daysInMonth(anchor) };
}

export function shiftAnchor(anchor: string, mode: CalendarMode, direction: -1 | 1): string {
  if (mode === "week") return addDays(anchor, 7 * direction);
  const [y, m] = anchor.split("-").map(Number);
  const next = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1 + direction, 1));
  return fromUtc(next.getTime());
}

export function rangeDays(start: string, days: number): string[] {
  return Array.from({ length: days }, (_, i) => addDays(start, i));
}

export type BarGeometry = {
  /** Fractional day offsets within the visible range, clipped to [0, days]. */
  start: number;
  end: number;
  clippedStart: boolean;
  clippedEnd: boolean;
};

/** Geometry of a stay bar, or null when it does not intersect the range. */
export function barGeometry(checkInDate: string, checkOutDate: string, rangeStart: string, days: number): BarGeometry | null {
  const rawStart = diffDays(rangeStart, checkInDate) + 0.5;
  const rawEnd = diffDays(rangeStart, checkOutDate) + 0.5;
  if (rawEnd <= 0 || rawStart >= days) return null;
  return {
    start: Math.max(rawStart, 0),
    end: Math.min(rawEnd, days),
    clippedStart: rawStart < 0,
    clippedEnd: rawEnd > days,
  };
}

/** True when two stays (local dates) overlap under the half-day convention. */
export function staysOverlap(a: { checkIn: string; checkOut: string }, b: { checkIn: string; checkOut: string }): boolean {
  return a.checkIn < b.checkOut && b.checkIn < a.checkOut;
}

/**
 * Greedy lane packing for bars in one unit row. Active stays never overlap
 * (the database forbids it), but cancelled/checked-out history can.
 */
export function assignLanes<T extends { start: number; end: number }>(bars: T[]): { lanes: number; placed: (T & { lane: number })[] } {
  const sorted = [...bars].sort((a, b) => a.start - b.start || a.end - b.end);
  const laneEnds: number[] = [];
  const placed = sorted.map((bar) => {
    let lane = laneEnds.findIndex((end) => end <= bar.start);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(bar.end);
    } else {
      laneEnds[lane] = bar.end;
    }
    return { ...bar, lane };
  });
  return { lanes: Math.max(laneEnds.length, 1), placed };
}
