/**
 * Browser-side SHA-256 via the Web Crypto API.
 *
 * The hash travels with the presign request so the backend can flag exact
 * duplicates BEFORE any bytes leave the device (fast rejection path), and is
 * re-sent with the confirm call to bind the storage object to the invoice.
 *
 * Note: crypto.subtle is only available in secure contexts (https, or
 * http://localhost during development).
 */
export async function calculateFileSHA256(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error(
      "Web Crypto is unavailable in this context. Open the app over HTTPS or localhost."
    );
  }
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  const bytes = new Uint8Array(digest);
  let hex = "";
  for (const byte of bytes) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}
