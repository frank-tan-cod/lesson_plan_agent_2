import type { PresentationDensity, PresentationStylePayload, PresentationTheme } from "@/lib/types";

type ThemePalette = {
  background: string;
  surface: string;
  border: string;
  header: string;
  accent: string;
  titleOnHeader: string;
  titleOnCover: string;
  body: string;
  subtitle: string;
  coverBackground: string;
};

type FontScale = {
  title: number;
  coverTitle: number;
  subtitle: number;
  body: number;
  placeholder: number;
  branding: number;
};

type PreviewPage = Record<string, unknown> & {
  template: string;
  layout: string;
  title: string;
  body: string;
  bullet_points: string[];
  preview_page_number: number;
  preview_page_total: number;
  preview_original_index: number;
};

export const DEFAULT_PRESENTATION_STYLE: PresentationStylePayload = {
  theme: "scholastic_blue",
  density: "comfortable",
  school_name: null,
  logo_url: null,
  logo_file_id: null
};

export const THEME_LABELS: Record<PresentationTheme, string> = {
  scholastic_blue: "学院蓝",
  forest_green: "讲堂绿",
  sunrise_orange: "晨光橙"
};

export const DENSITY_LABELS: Record<PresentationDensity, string> = {
  comfortable: "舒展大字",
  balanced: "均衡",
  compact: "紧凑"
};

const THEME_PALETTES: Record<PresentationTheme, ThemePalette> = {
  scholastic_blue: {
    background: "#f7f3ea",
    surface: "#ffffff",
    border: "#ddcfba",
    header: "#183149",
    accent: "#c7a364",
    titleOnHeader: "#ffffff",
    titleOnCover: "#183149",
    body: "#232d3a",
    subtitle: "#5a6776",
    coverBackground: "#f6f1e7"
  },
  forest_green: {
    background: "#f3f8f1",
    surface: "#ffffff",
    border: "#c9ddcc",
    header: "#1e5043",
    accent: "#b4935b",
    titleOnHeader: "#ffffff",
    titleOnCover: "#1e5043",
    body: "#223630",
    subtitle: "#586d64",
    coverBackground: "#edf5ed"
  },
  sunrise_orange: {
    background: "#faf4eb",
    surface: "#ffffff",
    border: "#e8d1b9",
    header: "#7d4620",
    accent: "#dc8c47",
    titleOnHeader: "#ffffff",
    titleOnCover: "#7d4620",
    body: "#443126",
    subtitle: "#7c614f",
    coverBackground: "#f8efe4"
  }
};

const TEMPLATE_LIMITS: Record<string, { charsPerLine: number; maxLines: number }> = {
  title_body: { charsPerLine: 26, maxLines: 15 },
  title_body_image: { charsPerLine: 19, maxLines: 12 },
  title_subtitle: { charsPerLine: 24, maxLines: 4 }
};

function normalizeText(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeTheme(value: unknown): PresentationTheme {
  return value === "forest_green" || value === "sunrise_orange" || value === "scholastic_blue"
    ? value
    : DEFAULT_PRESENTATION_STYLE.theme;
}

function normalizeDensity(value: unknown): PresentationDensity {
  return value === "balanced" || value === "compact" || value === "comfortable"
    ? value
    : DEFAULT_PRESENTATION_STYLE.density;
}

function normalizeTemplate(value: unknown, layout: unknown) {
  const template = normalizeText(value).toLowerCase();
  const normalizedLayout = normalizeText(layout).toLowerCase();
  if (template === "title_subtitle" || normalizedLayout === "cover" || normalizedLayout === "closing") {
    return "title_subtitle";
  }
  if (template === "title_body_image" || normalizedLayout === "image") {
    return "title_body_image";
  }
  return "title_body";
}

function bodyToBullets(body: string) {
  return body
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function wrapParagraph(paragraph: string, width: number) {
  const lines: string[] = [];
  let current = "";
  for (const char of paragraph) {
    current += char;
    if (current.length >= width) {
      lines.push(current);
      current = "";
    }
  }
  if (current) {
    lines.push(current);
  }
  return lines.length ? lines : [paragraph];
}

function splitLinesIntoPages(lines: string[], maxLines: number) {
  const pages: string[][] = [];
  let current: string[] = [];

  lines.forEach((line) => {
    if (current.length && current.length + 1 > Math.max(maxLines, 1)) {
      pages.push(current);
      current = [];
    }
    current.push(line);
  });

  if (current.length) {
    pages.push(current);
  }

  return pages;
}

function rebalanceSparseTailPages(pageLines: string[][], maxLines: number) {
  if (pageLines.length < 2) {
    return pageLines;
  }

  const visibleTailLines = pageLines[pageLines.length - 1].filter((line) => line.trim()).length;
  const sparseThreshold = Math.max(2, Math.min(3, Math.floor(maxLines / 3)));
  if (visibleTailLines > sparseThreshold) {
    return pageLines;
  }

  const flatLines = pageLines.flat();
  if (flatLines.length <= maxLines) {
    return pageLines;
  }

  const rebalanced: string[][] = [];
  let cursor = 0;
  let remainingLines = flatLines.length;

  for (let pageIndex = 0; pageIndex < pageLines.length; pageIndex += 1) {
    const pagesLeft = pageLines.length - pageIndex;
    const pageSize = Math.min(maxLines, Math.max(Math.ceil(remainingLines / pagesLeft), 1));
    rebalanced.push(flatLines.slice(cursor, cursor + pageSize));
    cursor += pageSize;
    remainingLines -= pageSize;
  }

  return rebalanced;
}

function paginateSlideText(text: string, charsPerLine: number, maxLines: number) {
  const normalizedCharsPerLine = Math.max(charsPerLine, 1);
  const normalizedMaxLines = Math.max(maxLines, 1);
  const paragraphs = String(text || "")
    .split("\n")
    .map((item) => item.trim());
  const lines: string[] = [];

  paragraphs.forEach((paragraph) => {
    if (!paragraph) {
      if (lines.length && lines[lines.length - 1] !== "") {
        lines.push("");
      }
      return;
    }
    lines.push(...wrapParagraph(paragraph, normalizedCharsPerLine));
  });

  if (!lines.length) {
    return [""];
  }

  const splitPages = splitLinesIntoPages(lines, normalizedMaxLines);
  const singlePagePreferred = preferSinglePageForLightOverflow(splitPages, normalizedCharsPerLine, normalizedMaxLines);
  const pageLines = rebalanceSparseTailPages(singlePagePreferred, normalizedMaxLines);
  const pages = pageLines.map((page) => page.join("\n").trim()).filter(Boolean);

  return pages.filter(Boolean).length ? pages.filter(Boolean) : [String(text || "").trim()];
}

function preferSinglePageForLightOverflow(pageLines: string[][], charsPerLine: number, maxLines: number) {
  if (pageLines.length !== 2) {
    return pageLines;
  }

  const [firstPage, secondPage] = pageLines;
  const visibleFirstLines = firstPage.filter((line) => line.trim()).length;
  const visibleSecondLines = secondPage.filter((line) => line.trim()).length;
  const totalVisibleLines = visibleFirstLines + visibleSecondLines;
  if (visibleSecondLines === 0) {
    return [firstPage];
  }
  if (visibleSecondLines > Math.max(2, Math.min(3, Math.floor(maxLines / 4)))) {
    return pageLines;
  }
  if (totalVisibleLines > maxLines + 1) {
    return pageLines;
  }
  if (totalVisibleLines > Math.min(maxLines + 1, 6)) {
    return pageLines;
  }

  const firstPageText = firstPage.map((line) => line.trim()).join("");
  const secondPageText = secondPage.map((line) => line.trim()).join("");
  if (!secondPageText) {
    return [firstPage];
  }

  const overflowRatio = secondPageText.length / Math.max(charsPerLine, 1);
  if (visibleFirstLines >= maxLines - 1 && overflowRatio > 0.6) {
    return pageLines;
  }

  return [[...firstPage, ...secondPage]];
}

function resolveDensityLimits(density: PresentationDensity, charsPerLine: number, maxLines: number, hasImagePanel: boolean) {
  if (density === "comfortable") {
    const charPenalty = hasImagePanel ? 4 : 3;
    const linePenalty = hasImagePanel ? 3 : 4;
    return {
      charsPerLine: Math.max(charsPerLine - charPenalty, 12),
      maxLines: Math.max(maxLines - linePenalty, 4)
    };
  }
  if (density === "compact") {
    const charBonus = hasImagePanel ? 2 : 1;
    const lineBonus = hasImagePanel ? 1 : 2;
    return {
      charsPerLine: charsPerLine + charBonus,
      maxLines: maxLines + lineBonus
    };
  }
  return { charsPerLine, maxLines };
}

export function normalizePresentationStyle(style: unknown): PresentationStylePayload {
  if (!style || typeof style !== "object" || Array.isArray(style)) {
    return { ...DEFAULT_PRESENTATION_STYLE };
  }
  const payload = style as Record<string, unknown>;
  return {
    theme: normalizeTheme(payload.theme),
    density: normalizeDensity(payload.density),
    school_name: normalizeText(payload.school_name) || null,
    logo_url: normalizeText(payload.logo_url) || null,
    logo_file_id: normalizeText(payload.logo_file_id) || null
  };
}

export function extractPresentationStyle(metadata: unknown) {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return normalizePresentationStyle(null);
  }
  return normalizePresentationStyle((metadata as Record<string, unknown>).presentation_style);
}

export function getThemePalette(theme: PresentationTheme) {
  return THEME_PALETTES[theme] || THEME_PALETTES[DEFAULT_PRESENTATION_STYLE.theme];
}

export function resolveFontSizes(density: PresentationDensity, hasImagePanel: boolean): FontScale {
  if (density === "compact") {
    return {
      title: 24,
      coverTitle: 28,
      subtitle: 13,
      body: hasImagePanel ? 12 : 13,
      placeholder: 10,
      branding: 10
    };
  }
  if (density === "balanced") {
    return {
      title: 26,
      coverTitle: 30,
      subtitle: 15,
      body: hasImagePanel ? 14 : 15,
      placeholder: 11,
      branding: 11
    };
  }
  return {
    title: 28,
    coverTitle: 32,
    subtitle: 16,
    body: hasImagePanel ? 15 : 17,
    placeholder: 12,
    branding: 12
  };
}

export function slideUsesImagePanel(slide: Record<string, unknown>) {
  return normalizeTemplate(slide.template, slide.layout) === "title_body_image";
}

export function paginateSlidesForPreview(
  slides: Array<Record<string, unknown>>,
  style: PresentationStylePayload
): PreviewPage[] {
  const pages: PreviewPage[] = [];

  slides.forEach((slide, slideIndex) => {
    const template = normalizeTemplate(slide.template, slide.layout);
    const title = normalizeText(slide.title) || `第 ${slideIndex + 1} 页`;
    const subtitle = normalizeText(slide.subtitle);
    const body =
      normalizeText(slide.body) ||
      (Array.isArray(slide.bullet_points) ? slide.bullet_points.filter((item) => typeof item === "string").join("\n") : "");

    if (template === "title_subtitle") {
      pages.push({
        ...slide,
        template,
        layout: "cover",
        title,
        subtitle: subtitle || body,
        body: "",
        bullet_points: [],
        preview_page_number: 1,
        preview_page_total: 1,
        preview_original_index: slideIndex
      });
      return;
    }

    const base = TEMPLATE_LIMITS[template] || TEMPLATE_LIMITS.title_body;
    const hasImagePanel = template === "title_body_image";
    const limits = resolveDensityLimits(style.density, base.charsPerLine, base.maxLines, hasImagePanel);
    const chunks = paginateSlideText(body, limits.charsPerLine, limits.maxLines);

    chunks.forEach((chunk, pageIndex) => {
      pages.push({
        ...slide,
        template,
        layout: hasImagePanel ? "image" : "title_content",
        title: chunks.length > 1 ? `${title}（${pageIndex + 1}/${chunks.length}）` : title,
        body: chunk,
        bullet_points: bodyToBullets(chunk),
        preview_page_number: pageIndex + 1,
        preview_page_total: chunks.length,
        preview_original_index: slideIndex
      });
    });
  });

  return pages;
}
