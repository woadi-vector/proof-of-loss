import "./styles.css";
import exifr from "exifr";
import {
  buildPayload, needsSecondLook, readComps, readSchedule, roomsOf, suggestLine,
  type Answer, type Comp, type Photo, type Provenance, type ScheduleLine, type SpottedItem,
} from "./claim";
import { secondLook, spotItems, type Mode } from "./ai";
import { appCheckNote, checkClaim, priceLines, type CheckResult, type LineResult, type Valuation } from "./firebase";
import { sha256Hex } from "./hash";
import * as XLSX from "xlsx";
import { evidenceRows, inventoryRows, sourceRows, summaryRows, type PriceResponse, type PricedLine } from "./workbook";
import { clock, readWalk, verifyWalk, type Verification, type WalkRecord, type WalkVideo } from "./walk";

// ------------------------------------------------------------- state ----

const state = {
  lossType: "fire",
  asOf: new Date().toISOString().slice(0, 10),
  lines: [] as ScheduleLine[],
  problems: [] as string[],
  comps: new Map<string, Comp[]>(),
  photos: [] as Photo[],
  files: new Map<string, Blob>(),
  items: [] as SpottedItem[],
  roomDamage: {} as Record<string, string>,
  secondLooks: {} as Record<string, Answer>,
  accepted: new Set<string>(),
  mode: "prefer_on_device" as Mode,
  log: [] as { what: string; source: string; ms: number; ok: boolean }[],
  result: null as CheckResult | null,
  walk: null as WalkRecord | null,
  verification: null as Verification | null,
  videoUrls: new Map<string, string>(),
  prices: null as PriceResponse | null,
  unpicked: new Set<string>(), // inventory lines the reviewer chose not to price
  busy: "",
  error: "",
};

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;

function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

const money = (v: number | null | undefined) =>
  v === null || v === undefined ? "none" : `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const SOURCE_LABEL: Record<string, string> = { on_device: "On device", in_cloud: "Cloud", said: "Named aloud" };
const PROVENANCE_LABEL: Record<Provenance, string> = {
  captured_in_app: "Captured in app",
  frame_from_video: "Frame from the walk video",
  from_glasses: "Received from glasses",
  uploaded: "Uploaded",
};
const STATUS_LABEL: Record<string, string> = {
  consistent: "Consistent",
  discrepancy: "Discrepancy",
  cant_tell: "Can't tell",
  not_observed: "Not observed",
  not_checked: "Not checked",
  observed_not_claimed: "Seen, not claimed",
};

// ---------------------------------------------------------- actions ----

async function timed<T>(what: string, fn: () => Promise<T & { source?: string }>): Promise<T & { source?: string }> {
  const t0 = performance.now();
  try {
    const r = await fn();
    state.log.unshift({ what, source: r.source ?? "", ms: performance.now() - t0, ok: true });
    return r;
  } catch (e) {
    state.log.unshift({ what, source: "", ms: performance.now() - t0, ok: false });
    throw e;
  }
}

async function loadSchedule(text: string) {
  const { lines, problems } = readSchedule(text);
  state.lines = lines;
  state.problems = problems;
  state.result = null;
  state.secondLooks = {};
  state.accepted.clear();
  for (const it of state.items) if (it.lineId && !lines.some((l) => l.line_id === it.lineId)) it.lineId = null;
  render();
}

async function addPhotos(files: FileList, room: string, provenance: Provenance) {
  for (const file of Array.from(files)) {
    if (!file.type.startsWith("image/")) continue;
    const sha = await sha256Hex(file);
    if (state.photos.some((p) => p.sha256 === sha)) continue; // same bytes, same evidence
    let recordedAt: string | null = null;
    try {
      const ex = await exifr.parse(file, ["DateTimeOriginal"]);
      if (ex?.DateTimeOriginal instanceof Date) recordedAt = ex.DateTimeOriginal.toISOString().slice(0, 10);
    } catch { /* no metadata: stays null and says so */ }
    state.photos.push({ sha256: sha, name: file.name, room, provenance, recordedAt, url: URL.createObjectURL(file) });
    state.files.set(sha, file);
    render();
    await spot(sha);
  }
}

// A walk shared from the phone. Every file is hashed again here and compared with
// the hash the phone recorded at capture. Only files that match become evidence;
// the phone's own readings come with them, so nothing is sent to a model again.
async function addWalk(files: FileList) {
  state.error = "";
  const all = Array.from(files);
  const record = all.find((f) => f.name.toLowerCase().endsWith(".json"));
  if (!record) { state.error = "Pick walk.json together with the photos and video the phone shared."; render(); return; }
  let walk: WalkRecord;
  try { walk = readWalk(await record.text()); } catch (e) { state.error = `Could not open the walk: ${(e as Error).message}`; render(); return; }
  const received = new Map<string, string>();
  const byName = new Map<string, File>();
  for (const [i, f] of all.filter((f) => f !== record).entries()) {
    state.busy = `Checking file ${i + 1} of ${all.length - 1} against the hash recorded on the phone`;
    render();
    received.set(f.name, await sha256Hex(f));
    byName.set(f.name, f);
  }
  const ver = verifyWalk(walk, received);
  state.walk = walk;
  state.verification = ver;
  const ok = new Set(ver.files.filter((f) => f.status === "matches").map((f) => f.file));
  for (const v of walk.videos) if (ok.has(v.file)) state.videoUrls.set(v.sha256, URL.createObjectURL(byName.get(v.file)!));
  const taken = new Set(state.items.map((i) => i.lineId).filter(Boolean) as string[]);
  for (const p of walk.photos) {
    if (!ok.has(p.file) || state.photos.some((x) => x.sha256 === p.sha256)) continue;
    const file = byName.get(p.file)!;
    state.photos.push({
      sha256: p.sha256, name: p.file, room: p.room,
      provenance: p.provenance === "frame_from_video" ? "frame_from_video" : "captured_in_app",
      recordedAt: p.recorded_at, url: URL.createObjectURL(file), atMs: p.at_ms, phoneSource: p.source,
      setAside: [...p.set_aside_as_structure, ...p.set_aside_not_property],
      modelError: p.model_error,
    });
    state.files.set(p.sha256, file);
    const add = (name: string, brand: string, clear: boolean, source: string, n: string) => {
      const lineId = suggestLine({ name, brand_model: brand }, p.room, state.lines, taken);
      if (lineId) taken.add(lineId);
      state.items.push({ id: `${p.sha256.slice(0, 8)}-${n}`, photoSha: p.sha256, name, brand_model: brand,
        clear, lineId, source });
    };
    p.items.forEach((it, n) => add(it.name, it.brand_model, it.clear, p.source ?? "", String(n)));
    p.said.forEach((s, n) => add(s.name, s.brand_model, true, "said", `s${n}`));
  }
  state.busy = "";
  state.result = null;
  render();
}

// Replacement cost for the walk's inventory lines: one cloud search per line,
// listings chosen and averaged by code, every listing cited.
async function priceWalk() {
  const w = state.walk;
  if (!w) return;
  const lines = w.inventory.filter((l) => !state.unpicked.has(l.line_id)).slice(0, 25);
  state.busy = `Searching new-retail listings for ${lines.length} lines in the cloud`;
  state.error = "";
  render();
  try {
    const r = await timed(`Replacement cost for ${lines.length} lines`, async () => ({
      ...(await priceLines({ currency: "USD", region: "US", lines: lines.map((l) => ({
        line_id: l.line_id, description: l.description, brand_model: l.brand_model_said })) })).data,
      source: "in_cloud" }));
    state.prices = r;
  } catch (e) {
    state.error = `Pricing did not run: ${(e as Error).message}`;
  }
  state.busy = "";
  render();
}

function downloadWorkbook() {
  const w = state.walk, v = state.verification;
  if (!w || !v) return;
  const prices = new Map<string, PricedLine>((state.prices?.lines ?? []).map((p) => [p.line_id, p]));
  const book = XLSX.utils.book_new();
  const add = (name: string, rows: unknown[][]) => XLSX.utils.book_append_sheet(book, XLSX.utils.aoa_to_sheet(rows), name);
  add("Summary", summaryRows(w, v, state.prices, w.inventory));
  add("Inventory", inventoryRows(w, prices));
  add("Sources", sourceRows(state.prices?.lines ?? []));
  add("Evidence", evidenceRows(w, v));
  XLSX.writeFile(book, `claim_${(w.claim_number || w.walk_id.slice(0, 8)).replace(/[^A-Za-z0-9_-]/g, "_")}_workbook.xlsx`);
}

function inventoryPanel(): string {
  const w = state.walk;
  if (!w?.inventory.length) return "";
  const prices = new Map<string, PricedLine>((state.prices?.lines ?? []).map((p) => [p.line_id, p]));
  const money2 = (n: number | null | undefined) => n == null ? "" : `$${n.toFixed(2)}`;
  const picked = w.inventory.filter((l) => !state.unpicked.has(l.line_id)).length;
  return `<div class="inv">
    <h3>Inventory from the walk (${w.inventory.length} lines)</h3>
    <p class="muted">Rolled up on the phone; every line keeps its sightings. Untick a line to leave it unpriced.</p>
    <table class="invt"><thead><tr><th></th><th>Line</th><th>Item</th><th>Said</th><th>Seen</th><th>Replacement cost</th><th>Listings</th></tr></thead><tbody>
    ${w.inventory.map((l) => { const p = prices.get(l.line_id); return `<tr class="${l.maybe_on_screen ? "confirm" : ""}">
      <td><input type="checkbox" data-pick="${esc(l.line_id)}" ${state.unpicked.has(l.line_id) ? "" : "checked"}></td>
      <td>${esc(l.line_id)}</td>
      <td>${esc(l.description)}${l.maybe_on_screen ? ` <span class="chip warn">may be on the TV screen</span>` : ""}</td>
      <td>${esc(l.brand_model_said)}</td><td class="num">${l.sightings.length}x</td>
      <td class="num">${p ? (p.replacement_cost != null ? `<b>${money2(p.replacement_cost)}</b><div class="muted small">${esc(p.basis)}</div>` :
        `<span class="muted">${p.error ? `search failed<div class="small">${esc(p.error.slice(0, 160))}</div>` : "no listing kept"}</span>`) : ""}</td>
      <td>${p ? `${p.kept.map((k) => `<a href="${esc(k.url)}" target="_blank" rel="noopener">${esc(k.source)} ${money2(k.price)}</a>`).join("<br>")}
        ${p.set_aside.length ? `<details><summary>${p.set_aside.length} set aside</summary><ul>${p.set_aside.map((s) =>
          `<li>${esc(s.source)} ${money2(s.price)}: ${esc(s.why)}</li>`).join("")}</ul></details>` : ""}
        <div class="muted small">${esc(p.search_note)}</div>` : ""}</td></tr>`; }).join("")}
    </tbody></table>
    <div class="row">
      <button id="price" class="primary" ${!picked || state.busy ? "disabled" : ""}>Find replacement cost (${Math.min(picked, 25)} searches)</button>
      <button id="workbook" class="ghost">Download claim workbook (.xlsx)</button>
      ${state.prices ? `<span class="pill">${state.prices.priced} priced · ${money2(state.prices.total_replacement_cost)} total, one of each</span>` : ""}
    </div>
    <p class="muted">Prices are averages of new items from established retailers, each listing linked. Refurbished, resale,
      marketplace, accessory and different-model listings are set aside with the reason. No model writes a price.</p>
  </div>`;
}

function walkPanel(): string {
  const w = state.walk, v = state.verification;
  if (!w || !v) return "";
  const bad = v.files.filter((f) => f.status !== "matches");
  const fromWalk = w.photos.filter((p) => v.files.some((f) => f.file === p.file && f.status === "matches"));
  const onDevice = fromWalk.filter((p) => p.source === "on_device").length;
  const inCloud = fromWalk.filter((p) => p.source === "in_cloud").length;
  return `<div class="walk">
    <div class="vsum ${bad.length ? "warn" : "ok"}">
      <b>${v.matched} of ${v.total}</b> files match the SHA-256 the phone recorded.
      ${(() => {
        const changed = bad.filter((f) => f.status === "changed").length;
        const missing = bad.length - changed;
        const parts = [changed ? `${changed} changed since capture` : "", missing ? `${missing} not received` : ""].filter(Boolean);
        return parts.length ? `${parts.join(", ")}; ${bad.length === 1 ? "it is" : "they are"} not used as evidence.` : "";
      })()}
    </div>
    <p><b>${w.claim_number ? `Claim ${esc(w.claim_number)}` : "No claim number on this walk"}</b>${
      w.loss_address ? ` · ${esc(w.loss_address)}` : ""}</p>
    <p class="muted">Walk ${esc(w.walk_id.slice(0, 8))} · ${esc(w.device)} · started ${esc(w.started_at.slice(0, 16).replace("T", " "))} UTC ·
      the phone read ${onDevice} on device and ${inCloud} in the cloud.</p>
    ${bad.length ? `<ul class="bad">${bad.map((f) => `<li><span class="chip ${f.status === "changed" ? "err" : "warn"}">${
      f.status === "changed" ? "Does not match" : "Not received"}</span> ${esc(f.file)}${
      f.status === "changed" ? ` <span class="mono muted">recorded ${f.recorded.slice(0, 12)}…, received ${f.actual!.slice(0, 12)}…</span>` : ""}</li>`).join("")}</ul>` : ""}
    ${v.extra.length ? `<p class="muted">Not named in walk.json, so not used: ${v.extra.map(esc).join(", ")}</p>` : ""}
    ${w.videos.map((vid) => videoCard(vid)).join("")}
    ${inventoryPanel()}
  </div>`;
}

function videoCard(v: WalkVideo): string {
  const url = state.videoUrls.get(v.sha256);
  const said = (v.mentions ?? []).map((m) => `${clock(m.at_ms)} ${m.kind === "room" ? `[${esc(m.text)}]` : esc(m.text)}${
    m.brand_model ? ` (${esc(m.brand_model)})` : ""}${m.purchase_year ? `, bought ${m.purchase_year}` : ""}`).join(" · ");
  return `<div class="video">
    ${url ? `<video src="${url}" controls preload="metadata"></video>` : `<div class="novideo muted">Video not received or does not match its hash</div>`}
    <div class="pmeta">
      <div><b>Walk video · ${esc(v.room)}</b> · ${v.provenance === "from_glasses"
        ? `Received from glasses · recorded ${v.recorded_at ? esc(v.recorded_at.slice(0, 16).replace("T", " ")) + " UTC" : "(date not in the file)"}`
        : "Captured in app"} · ${clock(v.duration_ms)}</div>
      <div class="muted">${v.provenance === "from_glasses"
        ? `Hashed when the phone received it${v.source_name ? ` (${esc(v.source_name)})` : ""}, not when the glasses recorded it.`
        : "Hashed on the phone the moment recording stopped."}</div>
      <div class="mono" title="${v.sha256}">SHA-256 ${v.sha256.slice(0, 16)}…</div>
      ${v.mentions ? `<div><span class="src in_cloud">Cloud</span> Heard: ${said || "nothing named"}</div>` :
        `<div class="muted">Narration not heard on the phone yet.</div>`}
      ${v.transcript ? `<details><summary>Transcript, word for word</summary><p>${esc(v.transcript)}</p></details>` : ""}
    </div></div>`;
}

async function spot(sha: string) {
  const photo = state.photos.find((p) => p.sha256 === sha)!;
  state.busy = `Looking at ${photo.name}`;
  state.error = "";
  render();
  try {
    const r = await timed(`Items in ${photo.name}`, () => spotItems(state.files.get(sha)!, state.mode));
    state.items = state.items.filter((i) => i.photoSha !== sha);
    const taken = new Set(state.items.map((i) => i.lineId).filter(Boolean) as string[]);
    r.items.forEach((it, n) => {
      const lineId = suggestLine(it, photo.room, state.lines, taken);
      if (lineId) taken.add(lineId);
      state.items.push({ id: `${sha.slice(0, 8)}-${n}`, photoSha: sha, ...it, lineId, source: r.source });
    });
  } catch (e) {
    state.error = `Could not read ${photo.name}: ${(e as Error).message}`;
  }
  state.busy = "";
  state.result = null;
  render();
}

function framesFor(line: ScheduleLine, seen: boolean): Blob[] {
  const shas = seen
    ? state.items.filter((i) => i.lineId === line.line_id && i.clear).map((i) => i.photoSha)
    : state.photos.filter((p) => p.room.toLowerCase() === line.room.toLowerCase()).map((p) => p.sha256);
  return [...new Set(shas)].map((s) => state.files.get(s)!).filter(Boolean);
}

async function runCheck() {
  state.error = "";
  try {
    for (let pass = 0; pass < 2; pass++) {
      state.busy = pass === 0 ? "Checking the claim" : "Checking again with the second looks";
      render();
      const payload = buildPayload({
        lossType: state.lossType, asOf: state.asOf, lines: state.lines, photos: state.photos,
        items: state.items, roomDamage: state.roomDamage, comps: state.comps,
        secondLooks: state.secondLooks, accepted: [...state.accepted],
      });
      state.result = (await checkClaim(payload)).data;
      const pending = needsSecondLook(state.result.results).filter((id) => !(id in state.secondLooks));
      if (!pending.length) break;
      for (const id of pending) {
        const line = state.lines.find((l) => l.line_id === id)!;
        const seen = state.items.some((i) => i.lineId === id);
        const frames = framesFor(line, seen);
        if (!frames.length) { state.secondLooks[id] = null; continue; }
        state.busy = `Second look: ${line.description}`;
        render();
        try {
          const r = await timed(`Second look: ${line.description}`, () => secondLook(line, frames, seen, state.mode));
          state.secondLooks[id] = r.answer;
        } catch {
          state.secondLooks[id] = null; // could not run: the line stays Can't tell
        }
      }
    }
  } catch (e) {
    state.error = `The check did not run: ${(e as Error).message}`;
  }
  state.busy = "";
  render();
}

// ---------------------------------------------------------- render ----

function render() {
  const rooms = roomsOf(state.lines, state.photos);
  const walked = new Set(state.photos.map((p) => p.room.toLowerCase()));
  const onDevice = state.log.filter((l) => l.ok && l.source === "on_device").length;
  const inCloud = state.log.filter((l) => l.ok && l.source === "in_cloud").length;

  $("#app").innerHTML = `
  <header class="top">
    <div>
      <h1>Proof of Loss</h1>
      <p class="sub">Contents review. Every line stays <b>Needs review</b> until a person signs it off.</p>
    </div>
    <div class="meta">
      <label>Model
        <select id="mode">
          ${(["prefer_on_device", "only_on_device", "only_in_cloud"] as Mode[]).map((m) =>
            `<option value="${m}" ${m === state.mode ? "selected" : ""}>${
              { prefer_on_device: "On device first, cloud fallback", only_on_device: "On device only",
                only_in_cloud: "Cloud only" }[m]}</option>`).join("")}
        </select>
      </label>
      <span class="pill">${esc(appCheckNote)}</span>
      <span class="pill">${onDevice} on device · ${inCloud} cloud</span>
    </div>
  </header>

  ${state.busy ? `<div class="banner busy">${esc(state.busy)}…</div>` : ""}
  ${state.error ? `<div class="banner err">${esc(state.error)}</div>` : ""}

  <section>
    <h2><span class="n">1</span> Claim</h2>
    <div class="row">
      <label>Loss
        <select id="loss">${["fire", "water", "theft", "other"].map((t) =>
          `<option ${t === state.lossType ? "selected" : ""}>${t}</option>`).join("")}</select>
      </label>
      <label>Valued as of <input id="asof" type="date" value="${state.asOf}"></label>
      <label class="file">Sworn schedule (CSV)<input id="schedule" type="file" accept=".csv,text/csv"></label>
      <button id="sample" class="ghost">Load sample claim</button>
      <label class="file">Cited listings (comps.csv)<input id="comps" type="file" accept=".csv,text/csv"></label>
    </div>
    ${state.lines.length ? `<p class="muted">${state.lines.length} lines,
      ${money(state.lines.reduce((s, l) => s + (l.claimed_amount ?? 0) * l.quantity, 0))} claimed,
      cited listings for ${[...state.comps.keys()].filter((k) => state.lines.some((l) => l.line_id === k)).length} lines.</p>` : ""}
    ${state.problems.length ? `<details class="problems" open><summary>${state.problems.length} gaps in the schedule, left as they are</summary>
      <ul>${state.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>` : ""}
  </section>

  <section>
    <h2><span class="n">2</span> Walk evidence</h2>
    <div class="row">
      <label>Room
        <input id="room" list="roomlist" placeholder="Living room" value="${esc(rooms[0] ?? "")}">
        <datalist id="roomlist">${rooms.map((r) => `<option value="${esc(r)}">`).join("")}</datalist>
      </label>
      <label>Came from
        <select id="prov">
          <option value="uploaded">Uploaded</option>
          <option value="from_glasses">Received from glasses</option>
        </select>
      </label>
      <label class="file primary">Add photos<input id="photos" type="file" accept="image/*" multiple></label>
      <label class="file primary">Open a walk from the phone<input id="walkfiles" type="file" accept=".json,image/*,video/*" multiple></label>
    </div>
    <p class="muted">For a phone walk, pick walk.json and every photo and video the phone shared, all at once.</p>
    ${walkPanel()}
    <div class="photos">
      ${state.photos.map((p) => photoCard(p)).join("") || `<p class="muted">No photos yet. Each photo is hashed (SHA-256) when it is added and never changed.</p>`}
    </div>
  </section>

  <section>
    <h2><span class="n">3</span> Rooms</h2>
    <table class="rooms"><thead><tr><th>Room</th><th>Walked</th><th>Damage</th></tr></thead><tbody>
      ${rooms.map((r) => `<tr><td>${esc(r)}</td>
        <td>${walked.has(r.toLowerCase()) ? "Yes" : `<span class="muted">No photos: lines here come back Not checked</span>`}</td>
        <td><select data-room="${esc(r)}">${["none", "light", "burned_through", "flooded"].map((d) =>
          `<option value="${d}" ${(state.roomDamage[r] ?? "none") === d ? "selected" : ""}>${d.replace("_", " ")}</option>`).join("")}</select></td></tr>`).join("")
        || `<tr><td colspan="3" class="muted">Rooms come from the schedule and the photos.</td></tr>`}
    </tbody></table>
  </section>

  <section>
    <h2><span class="n">4</span> Check</h2>
    <button id="run" class="primary" ${!state.lines.length || state.busy ? "disabled" : ""}>Run check</button>
    ${state.result ? resultsView(state.result) : ""}
  </section>

  <section>
    <h2>Model calls</h2>
    ${state.log.length ? `<table class="log"><tbody>${state.log.slice(0, 30).map((l) => `<tr>
      <td>${esc(l.what)}</td><td>${l.ok ? `<span class="src ${l.source}">${SOURCE_LABEL[l.source] ?? "?"}</span>` : `<span class="src fail">Failed</span>`}</td>
      <td class="num">${(l.ms / 1000).toFixed(1)} s</td></tr>`).join("")}</tbody></table>` :
      `<p class="muted">Each call shows whether it ran on this device or in the cloud.</p>`}
  </section>`;

  bind();
}

function photoCard(p: Photo): string {
  const items = state.items.filter((i) => i.photoSha === p.sha256);
  return `<div class="photo">
    <img src="${p.url}" alt="${esc(p.name)}">
    <div class="pmeta">
      <div><b>${esc(p.room)}</b> · ${esc(PROVENANCE_LABEL[p.provenance])}${
        p.atMs !== undefined && p.atMs !== null ? ` at ${clock(p.atMs)}` : ""}${
        p.phoneSource ? ` · read <span class="src ${p.phoneSource}">${SOURCE_LABEL[p.phoneSource] ?? p.phoneSource}</span> on the phone` : ""}</div>
      <div class="mono" title="${p.sha256}">SHA-256 ${p.sha256.slice(0, 16)}…</div>
      <div class="muted">${p.recordedAt ? `Recorded ${p.recordedAt}, per the file's own metadata` : "No recorded date in the file"}</div>
      <ul class="items">${items.map((it) => `<li>
        <span class="src ${it.source}">${SOURCE_LABEL[it.source] ?? ""}</span>
        <span>${esc(it.name)}${it.brand_model ? ` <span class="muted">(${esc(it.brand_model)})</span>` : ""}</span>
        ${it.clear ? "" : `<span class="chip warn">unclear frame</span>`}
        <select data-item="${it.id}">
          <option value="">Not on the schedule</option>
          ${state.lines.map((l) => `<option value="${esc(l.line_id)}" ${l.line_id === it.lineId ? "selected" : ""}>
            ${esc(l.line_id)}: ${esc(l.description)}</option>`).join("")}
        </select></li>`).join("")}</ul>
      ${p.modelError && !items.length ? `<div class="chip warn">Not read on the phone: ${esc(p.modelError)}</div>` : ""}
      ${p.setAside?.length ? `<div class="muted">Set aside on the phone (building or not property): ${p.setAside.map(esc).join(", ")}</div>` : ""}
      <button class="ghost small" data-respot="${p.sha256}">Look again</button>
    </div></div>`;
}

function lineView(r: LineResult, v: Valuation | undefined): string {
  const line = state.lines.find((l) => l.line_id === r.line_id);
  const title = line ? `${line.line_id}: ${line.description}${line.brand_model ? ` (${line.brand_model})` : ""}` : `Sighting ${r.sighting_id}`;
  const accepted = r.line_id ? state.accepted.has(r.line_id) : false;
  return `<details class="line ${r.status}">
    <summary>
      <span class="status ${r.status}">${STATUS_LABEL[r.status] ?? r.status}</span>
      <span class="ltitle">${esc(title)}</span>
      <span class="room muted">${esc(r.room)}</span>
      ${v ? `<span class="num">${money(v.claimed_total)} claimed</span><span class="num">${money(v.documented_total)} documented</span>` : ""}
    </summary>
    <div class="ldetail">
      <p>${esc(r.reason)}</p>
      ${r.differences.length ? `<ul>${r.differences.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
      ${r.second_look ? `<p class="muted">${esc(r.second_look)}</p>` : ""}
      ${v ? `<p><b>Replacement cost:</b> ${v.replacement_cost === null ? "none: no receipt and no cited listings" :
        `${money(v.replacement_cost)} each, ${esc(v.price_basis)}`}</p>
        ${v.price_sources.length ? `<ul class="sources">${v.price_sources.map((s) =>
          `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.source)}</a> ${money(s.price)} ${esc(s.title)}</li>`).join("")}</ul>` : ""}
        <p class="muted">${esc(v.depreciation_explained)}</p>
        ${v.flags.length ? `<p class="muted">Flags: ${v.flags.map(esc).join("; ")}</p>` : ""}` : ""}
      ${r.line_id && r.status !== "consistent" ? `<button class="ghost small" data-accept="${esc(r.line_id)}">
        ${accepted ? "Accepted in review (undo)" : "Accept in review"}</button>` : ""}
    </div></details>`;
}

function resultsView(res: CheckResult): string {
  const dv = res.documented_value;
  const claimed = Object.values(res.valuations).reduce((s, v) => s + (v.claimed_total ?? 0), 0);
  const counted = res.results.filter((r) => r.line_id && (r.status === "consistent" || state.accepted.has(r.line_id)));
  const notCounted = res.results.filter((r) => r.line_id && !counted.includes(r));
  const extra = res.results.filter((r) => !r.line_id);
  const order = ["discrepancy", "not_observed", "cant_tell", "not_checked"];
  notCounted.sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return `
    <div class="totals">
      <div><span class="big">${money(claimed)}</span><span>claimed</span></div>
      <div><span class="big">${money(dv.assetValue)}</span><span>documented (replacement cost of counted, priced lines)</span></div>
      <div><span class="big">${dv.linesCounted}</span><span>lines counted</span></div>
      <div><span class="big">${notCounted.length}</span><span>lines not counted</span></div>
      ${dv.linesUnpriced ? `<div><span class="big">${dv.linesUnpriced}</span><span>counted but no price yet</span></div>` : ""}
    </div>
    <p class="muted">The documented figure never includes a line the walk did not support or a price without a cited source.
      It is a fact for the reviewer, not a verdict on the claim.</p>
    <h3>Not counted (${notCounted.length})</h3>
    ${notCounted.map((r) => lineView(r, res.valuations[r.line_id!])).join("") || `<p class="muted">None.</p>`}
    <h3>Counted (${counted.length})</h3>
    ${counted.map((r) => lineView(r, res.valuations[r.line_id!])).join("") || `<p class="muted">None.</p>`}
    ${extra.length ? `<h3>Seen on the walk, not on the schedule (${extra.length})</h3>
      ${extra.map((r) => lineView(r, undefined)).join("")}` : ""}`;
}

function bind() {
  $("#mode").onchange = (e) => { state.mode = (e.target as HTMLSelectElement).value as Mode; };
  $("#loss").onchange = (e) => { state.lossType = (e.target as HTMLSelectElement).value; state.result = null; render(); };
  $("#asof").onchange = (e) => { state.asOf = (e.target as HTMLInputElement).value; };
  $<HTMLInputElement>("#schedule").onchange = async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) await loadSchedule(await f.text());
  };
  $("#sample").onclick = async () => loadSchedule(await (await fetch(`${import.meta.env.BASE_URL}sample_schedule.csv`)).text());
  $<HTMLInputElement>("#comps").onchange = async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) { state.comps = readComps(await f.text()); state.result = null; render(); }
  };
  $<HTMLInputElement>("#photos").onchange = async (e) => {
    const files = (e.target as HTMLInputElement).files;
    const room = $<HTMLInputElement>("#room").value.trim() || "Unknown room";
    const prov = $<HTMLSelectElement>("#prov").value as Provenance;
    if (files?.length) await addPhotos(files, room, prov);
  };
  document.querySelector<HTMLButtonElement>("#price")?.addEventListener("click", () => priceWalk());
  document.querySelector<HTMLButtonElement>("#workbook")?.addEventListener("click", () => downloadWorkbook());
  document.querySelectorAll<HTMLInputElement>("input[data-pick]").forEach((c) => {
    c.onchange = () => { if (c.checked) state.unpicked.delete(c.dataset.pick!); else state.unpicked.add(c.dataset.pick!); render(); };
  });
  $<HTMLInputElement>("#walkfiles").onchange = async (e) => {
    const files = (e.target as HTMLInputElement).files;
    if (files?.length) await addWalk(files);
  };
  document.querySelectorAll<HTMLSelectElement>("select[data-item]").forEach((s) => {
    s.onchange = () => {
      const it = state.items.find((i) => i.id === s.dataset.item)!;
      it.lineId = s.value || null;
      state.result = null;
      render();
    };
  });
  document.querySelectorAll<HTMLSelectElement>("select[data-room]").forEach((s) => {
    s.onchange = () => { state.roomDamage[s.dataset.room!] = s.value; state.result = null; render(); };
  });
  document.querySelectorAll<HTMLButtonElement>("button[data-respot]").forEach((b) => {
    b.onclick = () => spot(b.dataset.respot!);
  });
  document.querySelectorAll<HTMLButtonElement>("button[data-accept]").forEach((b) => {
    b.onclick = () => {
      const id = b.dataset.accept!;
      if (state.accepted.has(id)) state.accepted.delete(id); else state.accepted.add(id);
      runCheck();
    };
  });
  $<HTMLButtonElement>("#run").onclick = () => {
    // A new check starts from fresh second looks: the evidence may have changed.
    state.secondLooks = {};
    runCheck();
  };
}

render();
