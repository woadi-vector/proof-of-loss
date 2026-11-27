// The two questions a model is allowed to answer, through Firebase AI Logic
// hybrid inference: on-device Gemini Nano in Chrome when it is available, cloud
// Gemini otherwise. Every answer records where it ran.
//
// Models observe; code decides. Neither question asks for a price, a status or
// an opinion about the claim. A model names what it sees and answers yes, no or
// unsure; pol/service.py (the check_claim function) applies the rules.

import { getGenerativeModel, InferenceMode, Schema, type GenerativeModel, type Part } from "firebase/ai";
import { ai } from "./firebase";
import { CLOUD_MODEL } from "./config";
import type { Answer, ScheduleLine } from "./claim";

export type Mode = "prefer_on_device" | "only_on_device" | "only_in_cloud";

const spotSchemaJson = {
  type: "object",
  properties: {
    items: {
      type: "array",
      items: {
        type: "object",
        properties: {
          name: { type: "string" },
          brand_model: { type: "string" },
          clear: { type: "boolean" },
        },
        required: ["name", "brand_model", "clear"],
      },
    },
  },
  required: ["items"],
};

const spotSchema = Schema.object({
  properties: {
    items: Schema.array({
      items: Schema.object({
        properties: {
          name: Schema.string(),
          brand_model: Schema.string(),
          clear: Schema.boolean(),
        },
      }),
    }),
  },
});

const answerSchemaJson = {
  type: "object",
  properties: { answer: { type: "string", enum: ["yes", "no", "unsure"] } },
  required: ["answer"],
};

const answerSchema = Schema.object({
  properties: { answer: Schema.enumString({ enum: ["yes", "no", "unsure"] }) },
});

const SPOT_PROMPT = `You are helping a field insurance adjuster record what is in a room.
List each household item (furniture, electronics, appliances, clothing, decor, tools) you can see in this photo.
For each item give:
- name: a short plain name, like "65 inch television" or "leather recliner"
- brand_model: the brand and model only if you can read them on the item; otherwise ""
- clear: true only if the photo shows the item well enough for someone to identify it later
Do not guess brands. Do not estimate value, age or damage. Do not list walls, floors, windows or people.`;

function model(mode: Mode, json: object, schema: Schema): GenerativeModel {
  return getGenerativeModel(ai, {
    mode: mode as (typeof InferenceMode)[keyof typeof InferenceMode],
    inCloudParams: {
      model: CLOUD_MODEL,
      generationConfig: { responseMimeType: "application/json", responseSchema: schema },
    },
    onDeviceParams: {
      createOptions: { expectedInputs: [{ type: "image" }, { type: "text", languages: ["en"] }] },
      promptOptions: { responseConstraint: json },
    },
  });
}

// Downscale before sending: enough to recognise an item, far less data. The
// hash is always taken from the original file, never from this copy.
export async function imagePart(file: Blob, maxSide = 1024): Promise<Part> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  const data = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
  return { type: "inlineData", inlineData: { mimeType: "image/jpeg", data } };
}

function parseJson<T>(text: string): T {
  const t = text.trim().replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
  return JSON.parse(t) as T;
}

export interface Spotted {
  items: { name: string; brand_model: string; clear: boolean }[];
  source: string;
}

export async function spotItems(photo: Blob, mode: Mode): Promise<Spotted> {
  const m = model(mode, spotSchemaJson, spotSchema);
  const res = await m.generateContent([SPOT_PROMPT, await imagePart(photo)]);
  const out = parseJson<{ items?: Spotted["items"] }>(res.response.text());
  const items = (out.items ?? [])
    .filter((i) => i && typeof i.name === "string" && i.name.trim())
    .map((i) => ({ name: i.name.trim(), brand_model: (i.brand_model ?? "").trim(), clear: i.clear === true }));
  return { items, source: res.response.inferenceSource ?? "in_cloud" };
}

function claimText(line: ScheduleLine): string {
  const parts = [line.description, line.brand_model].filter(Boolean).join(", ");
  return line.quantity > 1 ? `${parts} (quantity ${line.quantity})` : parts;
}

function toAnswer(s: string | undefined): Answer {
  return s === "yes" ? true : s === "no" ? false : null;
}

// The reversed question. The first look found a difference (or did not find the
// item); this asks the opposite way round, with the frames, before anything
// reaches a person. Unsure is a real answer and is kept.
export async function secondLook(line: ScheduleLine, photos: Blob[], seen: boolean,
                                 mode: Mode): Promise<{ answer: Answer; source: string }> {
  const q = seen
    ? `The claim lists: ${claimText(line)}. Do these photos clearly show that item as described ` +
      `(same kind of item, same brand and model if given, same quantity)? ` +
      `Answer "yes", "no" or "unsure". Say "unsure" if the photos do not show enough.`
    : `The claim lists: ${claimText(line)}, in the ${line.room}. Look at these photos of that room. ` +
      `Is there any sign of that item, including damaged remains? ` +
      `Answer "yes" if there is any sign, "no" if the photos show the room well and there is none, ` +
      `"unsure" otherwise.`;
  const m = model(mode, answerSchemaJson, answerSchema);
  const parts: (string | Part)[] = [q];
  for (const p of photos.slice(0, 4)) parts.push(await imagePart(p, 768));
  const res = await m.generateContent(parts);
  const out = parseJson<{ answer?: string }>(res.response.text());
  return { answer: toAnswer(out.answer), source: res.response.inferenceSource ?? "in_cloud" };
}
