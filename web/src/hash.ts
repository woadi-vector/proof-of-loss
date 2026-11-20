// SHA-256 of the original bytes, taken the moment evidence is added. The file is
// never modified; downscaled copies for the model are made separately.
export async function sha256Hex(file: Blob): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
