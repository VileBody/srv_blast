/** Signed S3 URLs have a query string after the extension. */
export function isVideoUrl(url: string): boolean {
  return /\.(mp4|webm|mov|m4v)(?:[?#]|$)/i.test(url);
}
