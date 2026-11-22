// The claim workbook for a walk: Inventory, Sources and Evidence sheets. Built from
// the phone's walk record, the hash check done here, and the cited listings the
// price_lines function returned. Every price row carries its source and link; a
// line with no cited listing has no price, never a guess. Pure: no page code.

import type { Verification, WalkLine, WalkRecord } from "./walk";

export interface Listing { source: string; url: string; price: number; title: string; retrieved?: string; currency?: string }
export interface SetAside { source: string; title: string; price: number; url: string; why: string }
export interface PricedLine {
  line_id: string;
  description: string;
  brand_model: string;
  query: string;
  search_note: string;
  replacement_cost: number | null;
  currency?: string;
  basis: string;
  kept: Listing[];
  set_aside: SetAside[];
  flags: string[];
  answer_sha256: string | null;
  retrieved?: string;
  error?: string;
}
export interface PriceResponse {
  currency: string;
  region: string;
  lines: PricedLine[];
  priced: number;
  unpriced: number;
  total_replacement_cost: number;
}

type Row = (string | number | null)[];

const clock = (ms: number | null) => ms === null ? "" : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

export function inventoryRows(walk: WalkRecord, prices: Map<string, PricedLine>): Row[] {
  const head: Row = ["Line", "Room", "Item", "Brand/model said by adjuster", "Brand read by camera (count)",
    "Sightings", "First seen", "Last seen", "Needs a look", "Replacement cost (each)", "Currency", "How priced",
    "Listings counted", "Flags", "Review status"];
  const rows: Row[] = [head];
  for (const l of walk.inventory) {
    const p = prices.get(l.line_id);
    const times = l.sightings.map((s) => s.at_ms).filter((t): t is number => t !== null).sort((a, b) => a - b);
    rows.push([
      l.line_id, l.room, l.description, l.brand_model_said,
      Object.entries(l.brand_readings).map(([b, n]) => `${b} x${n}`).join("; "),
      l.sightings.length, clock(times[0] ?? null), clock(times[times.length - 1] ?? null),
      l.maybe_on_screen ? "May be on the TV screen; confirm" : "",
      p?.replacement_cost ?? null, p?.replacement_cost != null ? (p.currency ?? "") : "",
      p ? (p.replacement_cost != null ? `${p.basis}; ${p.search_note}` : (p.error ? `not priced: ${p.error}` : "not priced: no listing kept")) : "not priced yet",
      p?.kept.length ?? 0, p?.flags.join("; ") ?? "", "Needs review",
    ]);
  }
  return rows;
}

export function sourceRows(prices: PricedLine[]): Row[] {
  const rows: Row[] = [["Line", "Item", "Search", "Counted?", "Store", "Listing title", "Price", "Currency", "Link",
    "Why set aside", "Retrieved", "SHA-256 of the search answer"]];
  for (const p of prices) {
    for (const k of p.kept) rows.push([p.line_id, p.description, p.query, "counted", k.source, k.title, k.price,
      k.currency ?? "", k.url, "", k.retrieved ?? p.retrieved ?? "", p.answer_sha256 ?? ""]);
    for (const s of p.set_aside) rows.push([p.line_id, p.description, p.query, "set aside", s.source, s.title, s.price,
      "", s.url, s.why, p.retrieved ?? "", p.answer_sha256 ?? ""]);
  }
  return rows;
}

export function evidenceRows(walk: WalkRecord, ver: Verification): Row[] {
  const rows: Row[] = [["File", "Kind", "Room", "Moment in video", "Provenance", "SHA-256 recorded on the phone",
    "Check here", "Where the phone's model ran"]];
  for (const f of ver.files) {
    const p = walk.photos.find((x) => x.file === f.file);
    const v = walk.videos.find((x) => x.file === f.file);
    rows.push([f.file, f.kind, p?.room ?? v?.room ?? "", clock(p?.at_ms ?? null),
      p?.provenance ?? (v?.provenance === "from_glasses" ? "received from glasses (hashed on receipt)" : "captured in app"),
      f.recorded, f.status === "matches" ? "matches" : f.status === "changed" ? "DOES NOT MATCH" : "not received",
      p?.source === "on_device" ? "on device" : p?.source === "in_cloud" ? "cloud" : ""]);
  }
  return rows;
}

export function summaryRows(walk: WalkRecord, ver: Verification, res: PriceResponse | null, lines: WalkLine[]): Row[] {
  return [
    ["Proof of Loss: walk inventory"],
    ["Claim", walk.claim_number || "(none entered)"],
    ["Loss address", walk.loss_address],
    ["Walk", walk.walk_id],
    ["Device", walk.device],
    ["Files matching the hash recorded on the phone", `${ver.matched} of ${ver.total}`],
    ["Inventory lines", lines.length],
    ["Lines priced from cited listings", res?.priced ?? 0],
    ["Lines not priced", lines.length - (res?.priced ?? 0)],
    ["Total replacement cost (priced lines, one each)", res?.total_replacement_cost ?? 0],
    [],
    ["Every line is Needs review until a person signs it off. Replacement cost only: actual cash value is the carrier's to apply."],
    ["A price is the average of the cited new-retail listings counted for that line; every listing and every listing set aside is on the Sources sheet with its link."],
  ];
}
