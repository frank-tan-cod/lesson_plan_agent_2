"use client";

export interface ImageCropPreset {
  id: string;
  label: string;
  aspectRatio: number;
  outputWidth: number;
  outputHeight: number;
  helper?: string;
}

export interface ImageCropState {
  zoom: number;
  offsetX: number;
  offsetY: number;
}

export interface ImageNaturalSize {
  width: number;
  height: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function clampCropOffset(value: number) {
  return clamp(value, -1, 1);
}

export async function readFileAsDataUrl(file: File) {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error || new Error("读取图片失败。"));
    reader.readAsDataURL(file);
  });
}

export async function loadImageElement(src: string) {
  return await new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("加载图片失败。"));
    image.src = src;
  });
}

export async function loadImageNaturalSize(src: string): Promise<ImageNaturalSize> {
  const image = await loadImageElement(src);
  return {
    width: image.naturalWidth || image.width,
    height: image.naturalHeight || image.height
  };
}

export function computeCropLayout({
  imageWidth,
  imageHeight,
  viewportWidth,
  viewportHeight,
  zoom,
  offsetX,
  offsetY
}: ImageCropState & {
    imageWidth: number;
    imageHeight: number;
    viewportWidth: number;
    viewportHeight: number;
  }) {
  const safeImageWidth = Math.max(imageWidth, 1);
  const safeImageHeight = Math.max(imageHeight, 1);
  const safeViewportWidth = Math.max(viewportWidth, 1);
  const safeViewportHeight = Math.max(viewportHeight, 1);
  const baseScale = Math.max(safeViewportWidth / safeImageWidth, safeViewportHeight / safeImageHeight);
  const scaledWidth = safeImageWidth * baseScale * Math.max(zoom, 1);
  const scaledHeight = safeImageHeight * baseScale * Math.max(zoom, 1);
  const centeredX = (safeViewportWidth - scaledWidth) / 2;
  const centeredY = (safeViewportHeight - scaledHeight) / 2;
  const maxShiftX = Math.max((scaledWidth - safeViewportWidth) / 2, 0);
  const maxShiftY = Math.max((scaledHeight - safeViewportHeight) / 2, 0);

  return {
    width: scaledWidth,
    height: scaledHeight,
    translateX: centeredX + clampCropOffset(offsetX) * maxShiftX,
    translateY: centeredY + clampCropOffset(offsetY) * maxShiftY,
    maxShiftX,
    maxShiftY
  };
}

function canvasToBlob(canvas: HTMLCanvasElement, type: string) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
          return;
        }
        reject(new Error("裁剪图片失败。"));
      },
      type,
      type === "image/png" ? undefined : 0.92
    );
  });
}

function inferOutputType(file: File) {
  if (file.type === "image/png" || file.type === "image/webp") {
    return file.type;
  }
  return "image/jpeg";
}

function inferExtension(type: string) {
  if (type === "image/png") {
    return "png";
  }
  if (type === "image/webp") {
    return "webp";
  }
  return "jpg";
}

export async function cropImageFile({
  file,
  preset,
  crop
}: {
  file: File;
  preset: ImageCropPreset;
  crop: ImageCropState;
}) {
  const src = await readFileAsDataUrl(file);
  const image = await loadImageElement(src);
  const canvas = document.createElement("canvas");
  canvas.width = preset.outputWidth;
  canvas.height = preset.outputHeight;

  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("当前浏览器不支持图片裁剪。");
  }

  const layout = computeCropLayout({
    imageWidth: image.naturalWidth || image.width,
    imageHeight: image.naturalHeight || image.height,
    viewportWidth: preset.outputWidth,
    viewportHeight: preset.outputHeight,
    zoom: crop.zoom,
    offsetX: crop.offsetX,
    offsetY: crop.offsetY
  });

  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, layout.translateX, layout.translateY, layout.width, layout.height);

  const outputType = inferOutputType(file);
  const blob = await canvasToBlob(canvas, outputType);
  const baseName = file.name.replace(/\.[^.]+$/, "") || "image";
  return new File([blob], `${baseName}-${preset.id}.${inferExtension(outputType)}`, {
    type: outputType,
    lastModified: Date.now()
  });
}
