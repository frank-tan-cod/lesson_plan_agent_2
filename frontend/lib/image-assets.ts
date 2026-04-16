import { getApiBaseUrl } from "@/lib/api";
import type { KnowledgeFile } from "@/lib/types";

export function normalizeAssetText(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function buildKnowledgeImageUrl(file: Pick<KnowledgeFile, "storage_path">) {
  const normalizedPath = normalizeAssetText(file.storage_path);
  const filename = normalizedPath.split("/").filter(Boolean).pop();
  return filename ? `/uploads/images/${filename}` : "";
}

export function resolveImageAssetUrl(imageUrl: unknown) {
  const normalized = normalizeAssetText(imageUrl).trim();
  if (!normalized) {
    return "";
  }
  if (/^https?:\/\//i.test(normalized)) {
    return normalized;
  }
  if (normalized.startsWith("/")) {
    return `${getApiBaseUrl()}${normalized}`;
  }
  return normalized;
}

export function resolveUploadAssetUrl(assetUrl: unknown) {
  return resolveImageAssetUrl(assetUrl);
}
