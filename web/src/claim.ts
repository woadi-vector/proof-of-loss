// Claim data in the browser: reading the schedule and comps files, suggesting
// which schedule line a spotted item belongs to, and building the request for
// the check_claim Cloud Function. No model calls and no page code here.

export interface ScheduleLine {
  line_id: string;
  room: string;
  description: string;
  category: string;
  brand_model: string;
  quantity: number;
  purchase_year: number | null;
  claimed_amount: number | null;
}

export interface Comp {
  source: string;
  url: string;
  price: number;
  title: string;
  retrieved: string;
  currency: string;
  region: string;
}

// Where a piece of evidence came from. Shown on every record so the app never
// claims more certainty than the evidence gives it.
export type Provenance = "captured_in_app" | "from_glasses" | "uploaded";

export interface Photo {
  sha256: string;
  name: string;
  room: string;
  provenance: Provenance;
  recordedAt: string | null; // from the file's own metadata, never the file system
  url: string; // object URL for display
}

export interface SpottedItem {
  id: string;
  photoSha: string;
  name: string;
  brand_model: string;
  clear: boolean;
  lineId: string | null; // null: not claimed on the schedule
  source: string; // "on_device" | "in_cloud"
}

export type Answer = true | false | null;

// ---------------------------------------------------------------- CSV ----

export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const t = text.replace(/^﻿/, "");
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (quoted) {
      if (c === '"' && t[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && t[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some((x) => x.trim() !== "")) rows.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some((x) => x.trim() !== "")) rows.push(row);
  if (!rows.length) return [];
  const head = rows[0].map((h) => h.trim().toLowerCase());
  return rows.slice(1).map((r) => Object.fromEntries(head.map((h, i) => [h, (r[i] ?? "").trim()])));
}

function pick(row: Record<string, string>, ...names: string[]): string {
  for (const n of names) {
    for (const k of Object.keys(row)) {
      if (k === n || k.startsWith(n)) {
        if (row[k] !== "") return row[k];
      }
    }
  }
  return "";
}

function num(s: string): number | null {
  const v = parseFloat(s.replace(/[$,\s]/g, ""));
  return Number.isFinite(v) ? v : null;
}

export interface ScheduleRead {
  lines: ScheduleLine[];
  problems: string[]; // every gap, stated; nothing filled in silently
}

export function readSchedule(text: string): ScheduleRead {
  const lines: ScheduleLine[] = [];
  const problems: string[] = [];
  const seen = new Set<string>();
  parseCsv(text).forEach((row, i) => {
    const at = `row ${i + 2}`;
    const id = pick(row, "line_id", "line", "id") || String(i + 1);
    const description = pick(row, "description", "item");
    const room = pick(row, "room");
    if (!description) { problems.push(`${at}: no item description; row skipped`); return; }
    if (!room) problems.push(`${at} (line ${id}): no room`);
    if (seen.has(id)) { problems.push(`${at}: line ${id} appears twice; second copy skipped`); return; }
    seen.add(id);
    const brandModel = pick(row, "brand_model") ||
      [pick(row, "brand"), pick(row, "model")].filter(Boolean).join(" ");
    const qty = num(pick(row, "quantity", "qty"));
    const year = num(pick(row, "purchase_year", "year"));
    const claimed = num(pick(row, "claimed_amount", "claimed_unit", "claimed", "price"));
    if (claimed === null) problems.push(`${at} (line ${id}): no claimed amount`);
    lines.push({
      line_id: id,
      room: room || "Unknown room",
      description,
      category: pick(row, "category"),
      brand_model: brandModel,
      quantity: qty && qty > 0 ? Math.round(qty) : 1,
      purchase_year: year ? Math.round(year) : null,
      claimed_amount: claimed,
    });
  });
  return { lines, problems };
}

export function readComps(text: string): Map<string, Comp[]> {
  const out = new Map<string, Comp[]>();
  for (const row of parseCsv(text)) {
    const line = pick(row, "line");
    const price = num(pick(row, "price"));
    if (!line || price === null) continue;
    const c: Comp = {
      source: pick(row, "source"), url: pick(row, "url"), price,
      title: pick(row, "title"), retrieved: pick(row, "retrieved"),
      currency: pick(row, "currency"), region: pick(row, "region"),
    };
    out.set(line, [...(out.get(line) ?? []), c]);
  }
  return out;
}

// -------------------------------------------------------- suggestions ----

const STOP = new Set(["a", "an", "the", "and", "of", "with", "in", "on", "for", "second", "set"]);

function tokens(s: string): string[] {
  return (s.toLowerCase().match(/[a-z0-9]+/g) ?? [])
    .filter((t) => !STOP.has(t))
    .map((t) => (t.length > 3 && t.endsWith("s") ? t.slice(0, -1) : t));
}

// A suggestion only; the adjuster picks the line. Prefers lines in the same
// room. Returns null when nothing shares a word with the item.
export function suggestLine(item: { name: string; brand_model: string }, room: string,
                            lines: ScheduleLine[], taken: Set<string> = new Set()): string | null {
  const want = new Set(tokens(`${item.name} ${item.brand_model}`));
  let best: { id: string; score: number } | null = null;
  for (const ln of lines) {
    const have = tokens(`${ln.description} ${ln.brand_model}`);
    let score = have.filter((t) => want.has(t)).length;
    if (score === 0) continue;
    if (ln.room.toLowerCase() === room.toLowerCase()) score += 0.5;
    if (taken.has(ln.line_id)) score -= 0.25;
    if (!best || score > best.score) best = { id: ln.line_id, score };
  }
  return best ? best.id : null;
}

// ---------------------------------------------------------- payload ----

export interface CheckInput {
  lossType: string;
  asOf: string;
  lines: ScheduleLine[];
  photos: Photo[];
  items: SpottedItem[];
  roomDamage: Record<string, string>;
  comps: Map<string, Comp[]>;
  secondLooks: Record<string, Answer>;
  accepted: string[];
}

export function roomsOf(lines: ScheduleLine[], photos: Photo[]): string[] {
  const names = new Map<string, string>();
  for (const r of [...lines.map((l) => l.room), ...photos.map((p) => p.room)]) {
    if (r && !names.has(r.toLowerCase())) names.set(r.toLowerCase(), r);
  }
  return [...names.values()];
}

// The request body for check_claim. A room counts as walked only when at least
// one photo was taken in it; a room nobody photographed comes back Not checked.
export function buildPayload(c: CheckInput): Record<string, unknown> {
  const walked = new Set(c.photos.map((p) => p.room.toLowerCase()));
  const rooms = roomsOf(c.lines, c.photos).map((name) => ({
    name,
    walked: walked.has(name.toLowerCase()),
    damage: c.roomDamage[name] ?? "none",
  }));
  const sightings = c.items.map((it) => {
    const photo = c.photos.find((p) => p.sha256 === it.photoSha);
    return {
      sighting_id: it.id,
      room: photo?.room ?? "Unknown room",
      description: it.name,
      line_id: it.lineId,
      brand_model: it.brand_model,
      clear: it.clear,
      frame: it.photoSha,
    };
  });
  const comps: Record<string, Omit<Comp, never>[]> = {};
  for (const ln of c.lines) {
    const found = c.comps.get(ln.line_id);
    if (found?.length) comps[ln.line_id] = found;
  }
  return {
    loss_type: c.lossType,
    as_of: c.asOf,
    schedule: c.lines,
    sightings,
    rooms,
    comps,
    second_looks: c.secondLooks,
    accepted: c.accepted,
  };
}

// Lines the check wants a second look on: it found a visual difference, or the
// item was not seen in a room that was walked, and no second look has run yet.
export function needsSecondLook(results: { line_id: string | null; second_look: string }[]): string[] {
  return results
    .filter((r) => r.line_id && r.second_look === "second look not run")
    .map((r) => r.line_id as string);
}
