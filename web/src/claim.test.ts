import { describe, expect, it } from "vitest";
import { buildPayload, needsSecondLook, parseCsv, readComps, readSchedule, suggestLine } from "./claim";

const SCHEDULE = `line,room,item,brand,model,qty,purchase_year,acquired,claimed_unit_cad
1,Living room,65 inch TV,Samsung,QN65,1,2022,purchase,1800
2,Living room,"Soundbar, wireless",Sonos,Arc,1,2023,purchase,"1,099"
3,Bedroom,Dresser,,,1,2015,purchase,
3,Bedroom,Dresser again,,,1,2015,purchase,700
,,,,,,,,
`;

describe("csv", () => {
  it("handles quotes, commas and blank rows", () => {
    const rows = parseCsv(SCHEDULE);
    expect(rows).toHaveLength(4);
    expect(rows[1].item).toBe("Soundbar, wireless");
  });
});

describe("schedule", () => {
  it("reads loose headers and states every gap", () => {
    const { lines, problems } = readSchedule(SCHEDULE);
    expect(lines.map((l) => l.line_id)).toEqual(["1", "2", "3"]);
    expect(lines[0]).toMatchObject({ brand_model: "Samsung QN65", claimed_amount: 1800, purchase_year: 2022 });
    expect(lines[1].claimed_amount).toBe(1099);
    expect(lines[2].claimed_amount).toBeNull();
    expect(problems.some((p) => p.includes("line 3") && p.includes("no claimed amount"))).toBe(true);
    expect(problems.some((p) => p.includes("appears twice"))).toBe(true);
  });
});

describe("comps", () => {
  it("groups cited listings by line", () => {
    const m = readComps("line,source,url,price,currency\n1,Store,https://a,1100,USD\n1,Shop,https://b,$1200,USD\n2,X,https://c,,USD\n");
    expect(m.get("1")?.map((c) => c.price)).toEqual([1100, 1200]);
    expect(m.has("2")).toBe(false);
  });
});

describe("suggestions", () => {
  const { lines } = readSchedule(SCHEDULE);
  it("matches by words and prefers the same room", () => {
    expect(suggestLine({ name: "flat screen TV", brand_model: "Samsung" }, "Living room", lines)).toBe("1");
    expect(suggestLine({ name: "soundbars", brand_model: "" }, "Living room", lines)).toBe("2");
    expect(suggestLine({ name: "floor lamp", brand_model: "" }, "Living room", lines)).toBeNull();
  });
});

describe("payload", () => {
  const { lines } = readSchedule(SCHEDULE);
  const photos = [{ sha256: "abc", name: "a.jpg", room: "Living room", provenance: "uploaded" as const,
    recordedAt: null, url: "" }];
  const items = [{ id: "abc-0", photoSha: "abc", name: "TV", brand_model: "Samsung QN65", clear: true,
    lineId: "1", source: "on_device" }];
  it("marks only photographed rooms as walked", () => {
    const p = buildPayload({ lossType: "fire", asOf: "2026-11-20", lines, photos, items, roomDamage: {},
      comps: new Map(), secondLooks: {}, accepted: [] }) as any;
    const rooms = Object.fromEntries(p.rooms.map((r: any) => [r.name, r.walked]));
    expect(rooms).toEqual({ "Living room": true, Bedroom: false });
    expect(p.sightings[0]).toMatchObject({ room: "Living room", line_id: "1", frame: "abc" });
  });
  it("finds lines waiting on a second look", () => {
    expect(needsSecondLook([
      { line_id: "1", second_look: "second look not run" },
      { line_id: "2", second_look: "" },
      { line_id: null, second_look: "second look not run" },
    ])).toEqual(["1"]);
  });
});
