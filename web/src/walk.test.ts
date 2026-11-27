import { describe, expect, it } from "vitest";
import { cleanBrand, clock, readWalk, verifyWalk } from "./walk";

const WALK = JSON.stringify({
  format: "pol-walk-1", walk_id: "w1", claim_number: "FL-2026-0417", loss_address: "12 Palm Ct", label: "Office", started_at: "2026-11-21T19:50:00Z", device: "SM-F966U",
  photos: [
    { file: "aaa.jpg", sha256: "AAA", room: "Office", captured_at: "t", recorded_at: null,
      provenance: "frame_from_video", from_video: "vvv", at_ms: 33000, source: "on_device",
      items: [{ name: "computer monitor", brand_model: "Dell", clear: true }, { name: "", brand_model: "" }],
      said: [{ name: "computer monitor", brand_model: "" }],
      set_aside_as_structure: ["wall"], set_aside_not_property: ["dog"] },
    { file: "bbb.jpg", sha256: "bbb", room: "Office", captured_at: "t", items: [] },
    { file: "ccc.jpg", sha256: "ccc", room: "", captured_at: "t", items: [] },
  ],
  videos: [{ file: "vvv.mp4", sha256: "vvv", room: "Office", started_at: "t", duration_ms: 44000,
    listen_source: "in_cloud", transcript: "[5] Computer monitor",
    mentions: [{ at_ms: 5000, kind: "item", text: "computer monitor", brand_model: "", purchase_year: null, quantity: null }] }],
});

describe("readWalk", () => {
  it("reads the phone's record and keeps provenance, said lines and set-asides", () => {
    const w = readWalk(WALK);
    expect(w.photos).toHaveLength(3);
    expect([w.claim_number, w.loss_address]).toEqual(["FL-2026-0417", "12 Palm Ct"]);
    expect(w.photos[0]).toMatchObject({ sha256: "aaa", provenance: "frame_from_video", at_ms: 33000, source: "on_device" });
    expect(w.photos[0].items).toHaveLength(1); // the nameless item is dropped, not invented
    expect(w.photos[0].said[0].name).toBe("computer monitor");
    expect(w.photos[0].set_aside_not_property).toEqual(["dog"]);
    expect(w.photos[2].room).toBe("Unknown room");
    expect(w.videos[0].mentions?.[0].text).toBe("computer monitor");
    expect(w.videos[0].provenance).toBe("captured_in_app"); // older walks without the field
  });
  it("refuses a file that is not a walk", () => {
    expect(() => readWalk("{}")).toThrow(/not a Proof of Loss walk/);
    expect(() => readWalk("nope")).toThrow(/not valid JSON/);
  });
});

describe("verifyWalk", () => {
  it("marks each file as matching, changed or missing, and lists files the record does not name", () => {
    const w = readWalk(WALK);
    const v = verifyWalk(w, new Map([["aaa.jpg", "aaa"], ["bbb.jpg", "xxx"], ["vvv.mp4", "vvv"], ["stray.jpg", "s"]]));
    const by = Object.fromEntries(v.files.map((f) => [f.file, f.status]));
    expect(by).toEqual({ "aaa.jpg": "matches", "bbb.jpg": "changed", "ccc.jpg": "missing", "vvv.mp4": "matches" });
    expect(v.extra).toEqual(["stray.jpg"]);
    expect([v.matched, v.total]).toEqual([2, 4]);
  });
});

describe("cleanBrand", () => {
  it("drops non-brands and keeps real ones", () => {
    expect(cleanBrand("Unknown")).toBe("");
    expect(cleanBrand(" N/A ")).toBe("");
    expect(cleanBrand("ProForm")).toBe("ProForm");
  });
});

describe("clock", () => {
  it("formats moments in the video", () => {
    expect(clock(33000)).toBe("0:33");
    expect(clock(65000)).toBe("1:05");
  });
});
