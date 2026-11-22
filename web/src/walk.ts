// A walk shared from the phone: walk.json plus the photos, frames and video it
// names. The phone hashed every file the moment it was captured. Here each file
// is hashed again and compared with that record. A file that does not match, or
// is missing, is shown and is never used as evidence. No model calls here.

export interface WalkItem { name: string; brand_model: string; clear: boolean }

export interface WalkPhoto {
  file: string;
  sha256: string;
  room: string;
  captured_at: string;
  recorded_at: string | null;
  provenance: string; // "captured_in_app" | "frame_from_video"
  from_video: string | null;
  at_ms: number | null;
  source: string | null; // where the phone's model ran: "on_device" | "in_cloud"
  model_error: string | null;
  items: WalkItem[];
  said: { name: string; brand_model: string }[];
  set_aside_as_structure: string[];
  set_aside_not_property: string[];
}

export interface WalkMention {
  at_ms: number; kind: string; text: string; brand_model: string;
  purchase_year: number | null; quantity: number | null;
}

export interface WalkVideo {
  file: string;
  sha256: string;
  room: string;
  started_at: string;
  duration_ms: number;
  listen_source: string | null;
  provenance: string; // "captured_in_app" | "from_glasses"
  recorded_at: string | null; // from the video's own metadata
  source_name: string | null;
  transcript: string | null;
  mentions: WalkMention[] | null;
}

export interface WalkRecord {
  walk_id: string;
  label: string;
  started_at: string;
  device: string;
  claim_number: string;
  loss_address: string;
  photos: WalkPhoto[];
  videos: WalkVideo[];
}

/** "Unknown", "N/A", "none" and the like are no brand at all (same rule as the phone). */
export function cleanBrand(b: string): string {
  const t = b.trim();
  const k = t.toLowerCase().replace(/[^a-z]/g, "");
  return ["", "unknown", "na", "none", "unbranded", "generic", "notvisible", "unclear"].includes(k) ? "" : t;
}

const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const strOrNull = (v: unknown): string | null => (typeof v === "string" ? v : null);
const numOrNull = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const arr = (v: unknown): any[] => (Array.isArray(v) ? v : []);
const strs = (v: unknown): string[] => arr(v).filter((x) => typeof x === "string");

/** Reads walk.json as the phone wrote it. Throws with a plain reason if it is not one. */
export function readWalk(text: string): WalkRecord {
  let o: any;
  try { o = JSON.parse(text); } catch { throw new Error("walk.json is not valid JSON"); }
  if (o?.format !== "pol-walk-1") throw new Error("this file is not a Proof of Loss walk (format pol-walk-1)");
  const photos: WalkPhoto[] = arr(o.photos).map((p) => ({
    file: str(p.file),
    sha256: str(p.sha256).toLowerCase(),
    room: str(p.room, "Unknown room") || "Unknown room",
    captured_at: str(p.captured_at),
    recorded_at: strOrNull(p.recorded_at),
    provenance: str(p.provenance, "captured_in_app"),
    from_video: strOrNull(p.from_video),
    at_ms: numOrNull(p.at_ms),
    source: strOrNull(p.source),
    model_error: strOrNull(p.model_error),
    items: arr(p.items).map((i) => ({ name: str(i.name), brand_model: cleanBrand(str(i.brand_model)), clear: i.clear !== false }))
      .filter((i) => i.name.trim() !== ""),
    said: arr(p.said).map((s) => ({ name: str(s.name), brand_model: cleanBrand(str(s.brand_model)) })).filter((s) => s.name.trim() !== ""),
    set_aside_as_structure: strs(p.set_aside_as_structure),
    set_aside_not_property: strs(p.set_aside_not_property),
  })).filter((p) => p.file && p.sha256);
  const videos: WalkVideo[] = arr(o.videos).map((v) => ({
    file: str(v.file),
    sha256: str(v.sha256).toLowerCase(),
    room: str(v.room),
    started_at: str(v.started_at),
    duration_ms: numOrNull(v.duration_ms) ?? 0,
    listen_source: strOrNull(v.listen_source),
    provenance: str(v.provenance, "captured_in_app") || "captured_in_app",
    recorded_at: strOrNull(v.recorded_at),
    source_name: strOrNull(v.source_name),
    transcript: strOrNull(v.transcript),
    mentions: Array.isArray(v.mentions) ? v.mentions.map((m: any) => ({
      at_ms: numOrNull(m.at_ms) ?? 0, kind: str(m.kind), text: str(m.text), brand_model: str(m.brand_model),
      purchase_year: numOrNull(m.purchase_year), quantity: numOrNull(m.quantity),
    })) : null,
  })).filter((v) => v.file && v.sha256);
  return { walk_id: str(o.walk_id), label: str(o.label), started_at: str(o.started_at), device: str(o.device),
    claim_number: str(o.claim_number), loss_address: str(o.loss_address), photos, videos };
}

export type FileCheck = "matches" | "changed" | "missing";

export interface Verification {
  files: { file: string; kind: "photo" | "video"; status: FileCheck; recorded: string; actual: string | null }[];
  extra: string[]; // files received that walk.json does not name: shown, never used
  matched: number;
  total: number;
}

/**
 * Compares the hash the phone recorded for each file with the hash of the file received.
 * `received` maps a file name to the SHA-256 of its bytes as hashed here.
 */
export function verifyWalk(walk: WalkRecord, received: Map<string, string>): Verification {
  const named = [
    ...walk.photos.map((p) => ({ file: p.file, kind: "photo" as const, recorded: p.sha256 })),
    ...walk.videos.map((v) => ({ file: v.file, kind: "video" as const, recorded: v.sha256 })),
  ];
  const files = named.map((n) => {
    const actual = received.get(n.file) ?? null;
    const status: FileCheck = actual === null ? "missing" : actual === n.recorded ? "matches" : "changed";
    return { ...n, status, actual };
  });
  const names = new Set(named.map((n) => n.file));
  const extra = [...received.keys()].filter((f) => !names.has(f));
  return { files, extra, matched: files.filter((f) => f.status === "matches").length, total: files.length };
}

/** "0:33" for a moment in the video. */
export function clock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
