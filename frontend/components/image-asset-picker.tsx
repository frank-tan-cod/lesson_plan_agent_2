"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { SafeImage } from "@/components/ui/safe-image";
import { Textarea } from "@/components/ui/textarea";
import { buildKnowledgeImageUrl, normalizeAssetText, resolveImageAssetUrl } from "@/lib/image-assets";
import {
  clampCropOffset,
  computeCropLayout,
  cropImageFile,
  loadImageNaturalSize,
  readFileAsDataUrl,
  type ImageCropPreset
} from "@/lib/image-processing";
import type { KnowledgeFile } from "@/lib/types";
import { cn } from "@/lib/utils";

const PREVIEW_VIEWPORT_WIDTH = 440;

interface ImageAssetPickerProps {
  assetLabel: string;
  descriptionLabel: string;
  descriptionPlaceholder: string;
  descriptionHelper?: string;
  descriptionValue: string;
  onDescriptionChange: (value: string) => void;
  previewHint: string;
  previewImageUrl: string;
  imageFiles: KnowledgeFile[];
  loadingImages: boolean;
  onRefreshImages: () => void;
  selectedFileId: string;
  onSelectFile: (fileId: string) => void;
  selectedFileDescription?: string | null;
  cropPresets: ImageCropPreset[];
  onUploadCropped: (file: File) => Promise<void> | void;
  uploading: boolean;
  onUseSelected: () => Promise<void> | void;
  usingSelected: boolean;
  onClear: () => Promise<void> | void;
  clearing: boolean;
  clearLabel: string;
  useSelectedLabel: string;
  uploadLabel: string;
}

export function ImageAssetPicker({
  assetLabel,
  descriptionLabel,
  descriptionPlaceholder,
  descriptionHelper,
  descriptionValue,
  onDescriptionChange,
  previewHint,
  previewImageUrl,
  imageFiles,
  loadingImages,
  onRefreshImages,
  selectedFileId,
  onSelectFile,
  selectedFileDescription,
  cropPresets,
  onUploadCropped,
  uploading,
  onUseSelected,
  usingSelected,
  onClear,
  clearing,
  clearLabel,
  useSelectedLabel,
  uploadLabel
}: ImageAssetPickerProps) {
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [localFileDataUrl, setLocalFileDataUrl] = useState("");
  const [localFileReady, setLocalFileReady] = useState(false);
  const [selectedPresetId, setSelectedPresetId] = useState(cropPresets[0]?.id || "");
  const [zoom, setZoom] = useState(1);
  const [offsetX, setOffsetX] = useState(0);
  const [offsetY, setOffsetY] = useState(0);
  const [preparingUpload, setPreparingUpload] = useState(false);
  const [uploadInputKey, setUploadInputKey] = useState(0);
  const [imageNaturalSize, setImageNaturalSize] = useState<{ width: number; height: number } | null>(null);

  const activePreset = useMemo(
    () => cropPresets.find((preset) => preset.id === selectedPresetId) || cropPresets[0] || null,
    [cropPresets, selectedPresetId]
  );
  const previewImageSrc = localFileDataUrl || resolveImageAssetUrl(previewImageUrl);
  const previewHeight = activePreset ? Math.round(PREVIEW_VIEWPORT_WIDTH / activePreset.aspectRatio) : PREVIEW_VIEWPORT_WIDTH;

  const cropLayout =
    activePreset && imageNaturalSize && localFileDataUrl
      ? computeCropLayout({
          imageWidth: imageNaturalSize.width,
          imageHeight: imageNaturalSize.height,
          viewportWidth: PREVIEW_VIEWPORT_WIDTH,
          viewportHeight: previewHeight,
          zoom,
          offsetX,
          offsetY
        })
      : null;

  useEffect(() => {
    setSelectedPresetId(cropPresets[0]?.id || "");
  }, [cropPresets]);

  useEffect(() => {
    setZoom(1);
    setOffsetX(0);
    setOffsetY(0);
  }, [localFile, selectedPresetId]);

  useEffect(() => {
    let cancelled = false;

    async function prepareLocalFile(file: File) {
      setLocalFileReady(false);
      try {
        const dataUrl = await readFileAsDataUrl(file);
        const size = await loadImageNaturalSize(dataUrl);
        if (cancelled) {
          return;
        }
        setLocalFileDataUrl(dataUrl);
        setImageNaturalSize(size);
        setLocalFileReady(true);
      } catch {
        if (cancelled) {
          return;
        }
        setLocalFileDataUrl("");
        setImageNaturalSize(null);
        setLocalFileReady(false);
      }
    }

    if (!localFile) {
      setLocalFileDataUrl("");
      setImageNaturalSize(null);
      setLocalFileReady(false);
      return;
    }

    void prepareLocalFile(localFile);
    return () => {
      cancelled = true;
    };
  }, [localFile]);

  async function handleUpload() {
    if (!localFile || !activePreset) {
      return;
    }

    setPreparingUpload(true);
    try {
      const croppedFile = await cropImageFile({
        file: localFile,
        preset: activePreset,
        crop: { zoom, offsetX, offsetY }
      });
      await Promise.resolve(onUploadCropped(croppedFile));
      setLocalFile(null);
      setLocalFileDataUrl("");
      setImageNaturalSize(null);
      setLocalFileReady(false);
      setUploadInputKey((current) => current + 1);
    } finally {
      setPreparingUpload(false);
    }
  }

  return (
    <div className="space-y-5">
      <label className="block text-sm font-semibold text-ink">
        {descriptionLabel}
        <Textarea
          className="mt-2 min-h-[92px] bg-white"
          placeholder={descriptionPlaceholder}
          value={descriptionValue}
          onChange={(event) => onDescriptionChange(event.target.value)}
        />
        {descriptionHelper ? <span className="mt-2 block text-xs leading-5 text-steel">{descriptionHelper}</span> : null}
      </label>

      <div className="rounded-[28px] bg-white p-4 ring-1 ring-slate-200">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-ink">{assetLabel}预览</p>
            <p className="mt-1 text-xs leading-5 text-steel">{previewHint}</p>
          </div>
          {activePreset ? <Badge className="bg-sand text-steel">{activePreset.label}</Badge> : null}
        </div>

        <div className="mt-4 overflow-hidden rounded-[24px] bg-[#eef3f8] p-3">
          <div
            className="relative mx-auto overflow-hidden rounded-[20px] bg-slate-100"
            style={{ width: "100%", maxWidth: `${PREVIEW_VIEWPORT_WIDTH}px`, aspectRatio: activePreset ? `${activePreset.aspectRatio}` : "1 / 1" }}
          >
            {localFileDataUrl && cropLayout ? (
              <>
                <SafeImage
                  src={localFileDataUrl}
                  alt={`${assetLabel}裁剪预览`}
                  width={Math.max(Math.round(cropLayout.width), 1)}
                  height={Math.max(Math.round(cropLayout.height), 1)}
                  sizes={`${PREVIEW_VIEWPORT_WIDTH}px`}
                  className="absolute left-0 top-0 max-w-none select-none"
                  style={{
                    width: `${cropLayout.width}px`,
                    height: `${cropLayout.height}px`,
                    transform: `translate(${cropLayout.translateX}px, ${cropLayout.translateY}px)`
                  }}
                />
                <div className="pointer-events-none absolute inset-0 ring-1 ring-white/85" />
                <div className="pointer-events-none absolute inset-3 rounded-[18px] border border-white/60" />
              </>
            ) : previewImageSrc ? (
              <SafeImage
                src={previewImageSrc}
                alt={`${assetLabel}预览`}
                fill
                sizes={`${PREVIEW_VIEWPORT_WIDTH}px`}
                className="object-cover"
              />
            ) : (
              <div className="flex h-full items-center justify-center px-6 text-center text-sm leading-6 text-steel">
                还没有设置{assetLabel}，可以从知识库选图，或先上传一张再裁剪。
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[0.98fr_0.92fr]">
        <div className="space-y-4 rounded-[28px] bg-white p-4 ring-1 ring-slate-200">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-ink">从知识库选择</p>
              <p className="mt-1 text-xs leading-5 text-steel">点击缩略图即可切换预览，确认后会应用到当前目标。</p>
            </div>
            <Button variant="secondary" disabled={loadingImages} onClick={onRefreshImages}>
              {loadingImages ? "加载中..." : "刷新资料"}
            </Button>
          </div>

          <div className="grid max-h-[260px] gap-3 overflow-y-auto pr-1 sm:grid-cols-2">
            {loadingImages ? (
              <div className="rounded-[22px] bg-sand/50 p-4 text-sm text-steel">正在加载图片资源…</div>
            ) : imageFiles.length ? (
              imageFiles.map((file) => {
                const fileUrl = resolveImageAssetUrl(buildKnowledgeImageUrl(file));
                const selected = file.id === selectedFileId;
                return (
                  <button
                    key={file.id}
                    type="button"
                    onClick={() => onSelectFile(file.id)}
                    className={cn(
                      "overflow-hidden rounded-[22px] border bg-[#f8fafc] text-left transition",
                      selected ? "border-ink shadow-soft ring-2 ring-ink/10" : "border-slate-200 hover:border-slate-300"
                    )}
                  >
                    <div className="aspect-[4/3] bg-slate-100">
                      {fileUrl ? (
                        <div className="relative h-full w-full">
                          <SafeImage
                            src={fileUrl}
                            alt={file.filename}
                            fill
                            sizes="(max-width: 768px) 50vw, 220px"
                            className="object-cover"
                          />
                        </div>
                      ) : (
                        <div className="flex h-full items-center justify-center px-3 text-xs text-steel">无预览</div>
                      )}
                    </div>
                    <div className="space-y-2 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="line-clamp-1 text-sm font-semibold text-ink">{file.filename}</p>
                        {selected ? <Badge className="bg-ink text-white">已选</Badge> : null}
                      </div>
                      <p className="line-clamp-2 text-xs leading-5 text-steel">
                        {normalizeAssetText(file.description).trim() || "暂无图片说明"}
                      </p>
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="rounded-[22px] bg-sand/50 p-4 text-sm leading-6 text-steel">
                知识库里还没有图片，先在右侧上传一张即可。
              </div>
            )}
          </div>

          {selectedFileDescription ? (
            <div className="rounded-[20px] bg-sand/45 px-4 py-3 text-sm leading-6 text-steel">
              已选图片说明：{selectedFileDescription}
            </div>
          ) : null}
        </div>

        <div className="space-y-4 rounded-[28px] bg-white p-4 ring-1 ring-slate-200">
          <div>
            <p className="text-sm font-semibold text-ink">上传并裁剪</p>
            <p className="mt-1 text-xs leading-5 text-steel">本地图片会先在浏览器里裁剪，再上传入知识库并立即用于当前目标。</p>
          </div>

          <label className="block text-sm font-semibold text-ink">
            选择图片
            <Input
              key={uploadInputKey}
              className="mt-2"
              type="file"
              accept="image/*"
              onChange={(event) => setLocalFile(event.target.files?.[0] || null)}
            />
          </label>

          <label className="block text-sm font-semibold text-ink">
            裁剪画幅
            <Select
              className="mt-2"
              value={selectedPresetId}
              onChange={(event) => setSelectedPresetId(event.target.value)}
              disabled={!cropPresets.length}
            >
              {cropPresets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.label}
                </option>
              ))}
            </Select>
            {activePreset?.helper ? <span className="mt-2 block text-xs leading-5 text-steel">{activePreset.helper}</span> : null}
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-semibold text-ink">
              缩放
              <input
                type="range"
                min={100}
                max={220}
                step={1}
                value={Math.round(zoom * 100)}
                disabled={!localFileDataUrl}
                onChange={(event) => setZoom(Number(event.target.value) / 100)}
                className="mt-3 w-full accent-[#183149]"
              />
              <span className="mt-2 block text-xs text-steel">{Math.round(zoom * 100)}%</span>
            </label>

            <label className="block text-sm font-semibold text-ink">
              横向位置
              <input
                type="range"
                min={-100}
                max={100}
                step={1}
                value={Math.round(offsetX * 100)}
                disabled={!localFileDataUrl}
                onChange={(event) => setOffsetX(clampCropOffset(Number(event.target.value) / 100))}
                className="mt-3 w-full accent-[#183149]"
              />
              <span className="mt-2 block text-xs text-steel">{Math.round(offsetX * 100)}%</span>
            </label>

            <label className="block text-sm font-semibold text-ink sm:col-span-2">
              纵向位置
              <input
                type="range"
                min={-100}
                max={100}
                step={1}
                value={Math.round(offsetY * 100)}
                disabled={!localFileDataUrl}
                onChange={(event) => setOffsetY(clampCropOffset(Number(event.target.value) / 100))}
                className="mt-3 w-full accent-[#183149]"
              />
              <span className="mt-2 block text-xs text-steel">{Math.round(offsetY * 100)}%</span>
            </label>
          </div>

          <div className="flex flex-wrap justify-end gap-3">
            <Button
              variant="secondary"
              disabled={clearing || usingSelected || uploading || preparingUpload}
              onClick={() => void Promise.resolve(onClear())}
            >
              {clearLabel}
            </Button>
            <Button
              variant="secondary"
              disabled={clearing || usingSelected || uploading || preparingUpload || !selectedFileId}
              onClick={() => void Promise.resolve(onUseSelected())}
            >
              {usingSelected ? "处理中..." : useSelectedLabel}
            </Button>
            <Button
              disabled={clearing || usingSelected || uploading || preparingUpload || !localFile || !localFileReady}
              onClick={() => void handleUpload()}
            >
              {uploading || preparingUpload ? "处理中..." : uploadLabel}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
