import { describe, expect, it } from "vitest";
import { readWalk, verifyWalk } from "./walk";
import { evidenceRows, inventoryRows, sourceRows, summaryRows, type PricedLine } from "./workbook";

const walk = readWalk(JSON.stringify({
  format: "pol-walk-1", walk_id: "w1", claim_number: "12345", loss_address: "1211 Hazelwood",
  photos: [{ file: "a.jpg", sha256: "aaa", room: "Office", provenance: "frame_from_video", at_ms: 11000, source: "on_device", items: [] }],
  videos: [{ file: "v.mp4", sha256: "vvv", room: "Office", provenance: "from_glasses", duration_ms: 56000 }],
  inventory: [
    { line_id: "W1", room: "Office", description: "television", brand_model_said: "TCL 75S451",
      brand_readings: { TCL: 1 }, said: ["TV (TCL 75S451) at 0:11"], maybe_on_screen: false,
      sightings: [{ sha256: "aaa", at_ms: 11000, clear: true }] },
    { line_id: "W2", room: "Office", description: "jacket", brand_model_said: "Unknown", brand_readings: {},
      said: [], maybe_on_screen: true, sightings: [{ sha256: "aaa", at_ms: 11000, clear: true }] },
  ],
}));
const priced: PricedLine = {
  line_id: "W1", description: "television", brand_model: "TCL 75S451", query: "TCL 75S451 television",
  search_note: "searched for the model the adjuster named: TCL 75S451", replacement_cost: 539.5, currency: "USD",
  basis: "average of 2 listings ($529.00 to $549.99)",
  kept: [{ source: "Best Buy", url: "https://b", price: 549.99, title: "TCL 75S451" },
         { source: "Walmart", url: "https://w", price: 529, title: "TCL 75S451" }],
  set_aside: [{ source: "eBay", url: "https://e", price: 300, title: "TCL 75S451", why: "store not on the trusted list" }],
  flags: [], answer_sha256: "f".repeat(64), retrieved: "2026-11-22",
};

describe("workbook", () => {
  it("prices only from cited listings and leaves the rest unpriced", () => {
    const rows = inventoryRows(walk, new Map([["W1", priced]]));
    expect(rows[1]).toContain(539.5);
    expect(rows[1][3]).toBe("TCL 75S451");
    expect(rows[2][9]).toBeNull();                       // no listing, no price
    expect(rows[2][11]).toBe("not priced yet");
    expect(rows[2][8]).toMatch(/TV screen/);             // the on-screen flag travels to the sheet
    expect(walk.inventory[1].brand_model_said).toBe(""); // "Unknown" is no brand
  });
  it("lists every counted and set-aside listing with its link and the search hash", () => {
    const rows = sourceRows([priced]);
    expect(rows.slice(1).map((r) => r[3])).toEqual(["counted", "counted", "set aside"]);
    expect(rows[3][9]).toMatch(/trusted list/);
    expect(rows[1][11]).toHaveLength(64);
  });
  it("states each file's hash check and the glasses provenance", () => {
    const ver = verifyWalk(walk, new Map([["a.jpg", "aaa"]]));
    const rows = evidenceRows(walk, ver);
    expect(rows[1][6]).toBe("matches");
    expect(rows[2][6]).toBe("not received");
    expect(rows[2][4]).toMatch(/hashed on receipt/);
    expect(summaryRows(walk, ver, null, walk.inventory)[1]).toEqual(["Claim", "12345"]);
  });
});
