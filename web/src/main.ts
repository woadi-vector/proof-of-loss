import "./styles.css";
import exifr from "exifr";
import {
  buildPayload, needsSecondLook, readComps, readSchedule, roomsOf, suggestLine,
  type Answer, type Comp, type Photo, type Provenance, type ScheduleLine, type SpottedItem,
} from "./claim";
import { secondLook, spotItems, type Mode } from "./ai";
import { appCheckNote, checkClaim, type CheckResult, type LineResult, type Valuation } from "./firebase";
import { sha256Hex } from "./hash";

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

const SOURCE_LABEL: Record<string, string> = { on_device: "On device", in_cloud: "Cloud" };
const PROVENANCE_LABEL: Record<Provenance, string> = {
  captured_in_app: "Captured in app",
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
    </div>
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
      <div><b>${esc(p.room)}</b> · ${esc(PROVENANCE_LABEL[p.provenance])}</div>
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
