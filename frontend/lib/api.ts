import type {
  AuthToken,
  Conversation,
  DocType,
  GeneratePresentationResponse,
  KnowledgeAnswerResponse,
  KnowledgeFile,
  KnowledgeFileListResponse,
  KnowledgeSearchResult,
  Plan,
  PlanListResponse,
  PresentationStylePayload,
  PreferencePreset,
  PreferenceSuggestion,
  RestoreResponse,
  Savepoint,
  TempPreferencesPayload,
  User
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const USER_KEY = "lesson-plan-agent-user";
const AUTH_TOKEN_KEY = "lesson-plan-agent-token";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 500) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getApiBaseUrl() {
  return API_URL.replace(/\/$/, "");
}

export function getStoredUser() {
  if (typeof window === "undefined") {
    return null;
  }

  const value = window.localStorage.getItem(USER_KEY);
  if (!value) {
    return null;
  }

  try {
    return JSON.parse(value) as User;
  } catch {
    return null;
  }
}

export function getStoredAuthToken() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

export function storeAuthToken(token: string | null) {
  if (typeof window === "undefined") {
    return;
  }
  if (token) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  } else {
    window.localStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

export function storeUser(user: User | null) {
  if (typeof window === "undefined") {
    return;
  }
  if (user) {
    window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  } else {
    window.localStorage.removeItem(USER_KEY);
  }
}

function buildUrl(path: string, params?: Record<string, string | number | undefined | null>) {
  const url = new URL(`${getApiBaseUrl()}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function parseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || `请求失败 (${response.status})`;
  } catch {
    return `请求失败 (${response.status})`;
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit & {
    auth?: boolean;
    token?: string | null;
    params?: Record<string, string | number | undefined | null>;
  } = {}
) {
  const headers = new Headers(init.headers);
  const token = init.token ?? getStoredAuthToken();

  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  if (init.auth !== false && token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path, init.params), {
    ...init,
    headers,
    credentials: init.credentials ?? "include",
    cache: "no-store"
  });

  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }

  return (await response.blob()) as T;
}

export async function login(username: string, password: string) {
  return apiRequest<AuthToken>("/api/auth/login", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ username, password })
  });
}

export async function register(username: string, password: string) {
  return apiRequest<User>("/api/auth/register", {
    method: "POST",
    auth: false,
    body: JSON.stringify({ username, password })
  });
}

export async function logout() {
  return apiRequest<void>("/api/auth/logout", {
    method: "POST"
  });
}

export async function fetchMe(token?: string | null) {
  return apiRequest<User>("/api/user/me", {
    token
  });
}

export async function fetchPlans(filters: {
  docType: DocType;
  subject?: string;
  grade?: string;
  query?: string;
}) {
  const normalizedSubject = filters.subject?.trim().toLowerCase() || "";
  const normalizedGrade = filters.grade?.trim().toLowerCase() || "";

  if (filters.query?.trim()) {
    const response = await apiRequest<PlanListResponse>("/api/plans/search", {
      params: {
        q: filters.query.trim(),
        doc_type: filters.docType
      }
    });

    const items = response.items.filter((item) => {
      if (
        normalizedSubject &&
        !String(item.subject || "")
          .trim()
          .toLowerCase()
          .includes(normalizedSubject)
      ) {
        return false;
      }
      if (
        normalizedGrade &&
        !String(item.grade || "")
          .trim()
          .toLowerCase()
          .includes(normalizedGrade)
      ) {
        return false;
      }
      return true;
    });

    return {
      items,
      total: items.length
    };
  }

  return apiRequest<PlanListResponse>("/api/plans", {
    params: {
      doc_type: filters.docType,
      subject: filters.subject?.trim(),
      grade: filters.grade?.trim()
    }
  });
}

export async function createPlan(payload: {
  title: string;
  doc_type: DocType;
  subject?: string;
  grade?: string;
  requirements?: string;
  additional_files?: string[];
  course_context?: string;
}) {
  return apiRequest<Plan>("/api/plans", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function getPlan(planId: string) {
  return apiRequest<Plan>(`/api/plans/${planId}`);
}

export async function generatePresentationFromPlan(
  planId: string,
  additionalFiles: string[] = [],
  courseContext?: string,
  presentationStyle?: PresentationStylePayload
) {
  return apiRequest<GeneratePresentationResponse>(`/api/plans/${planId}/generate-presentation`, {
    method: "POST",
    body: JSON.stringify({
      additional_files: additionalFiles,
      course_context: courseContext || undefined,
      presentation_style: presentationStyle
    })
  });
}

export async function generateLessonGames(
  planId: string,
  payload?: Partial<{
    game_count: number;
    templates: Array<"single_choice" | "true_false" | "flip_cards">;
    replace_existing: boolean;
  }>
) {
  return apiRequest<Plan>(`/api/plans/${planId}/generate-games`, {
    method: "POST",
    body: JSON.stringify({
      game_count: payload?.game_count ?? 3,
      templates: payload?.templates ?? [],
      replace_existing: payload?.replace_existing ?? true
    })
  });
}

export async function updatePresentation(
  planId: string,
  payload: {
    title?: string;
    content?: Record<string, unknown>;
    metadata?: Record<string, unknown>;
  }
) {
  return apiRequest<Plan>(`/api/presentations/${planId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function deletePlan(planId: string) {
  return apiRequest<void>(`/api/plans/${planId}`, {
    method: "DELETE"
  });
}

export async function createConversation(planId: string) {
  return apiRequest<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ plan_id: planId })
  });
}

export async function listConversations(planId: string) {
  return apiRequest<Conversation[]>("/api/conversations", {
    params: { plan_id: planId }
  });
}

export async function getTempPreferences(conversationId: string) {
  return apiRequest<TempPreferencesPayload>(`/api/conversations/${conversationId}/temp-preferences`);
}

export async function replaceTempPreferences(conversationId: string, payload: TempPreferencesPayload) {
  return apiRequest<TempPreferencesPayload>(`/api/conversations/${conversationId}/temp-preferences`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function createSavepoint(payload: {
  plan_id: string;
  label: string;
  snapshot: Record<string, unknown>;
  conversation_id?: string | null;
  persist_to_knowledge?: boolean;
  knowledge_title?: string;
  knowledge_description?: string;
  knowledge_tags?: string[];
}) {
  return apiRequest<Savepoint>("/api/savepoints", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function listSavepoints(planId: string) {
  return apiRequest<Savepoint[]>("/api/savepoints", {
    params: { plan_id: planId }
  });
}

export async function restoreSavepoint(savepointId: string) {
  return apiRequest<RestoreResponse>(`/api/savepoints/${savepointId}/restore`, {
    method: "POST"
  });
}

export async function deleteSavepoint(savepointId: string) {
  return apiRequest<void>(`/api/savepoints/${savepointId}`, {
    method: "DELETE"
  });
}

function parseFilename(headers: Headers, fallback: string) {
  const contentDisposition = headers.get("content-disposition") || "";
  const utfMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utfMatch?.[1]) {
    return decodeURIComponent(utfMatch[1]);
  }
  const plainMatch = contentDisposition.match(/filename=([^;]+)/i);
  if (plainMatch?.[1]) {
    return plainMatch[1].replace(/"/g, "");
  }
  return fallback;
}

async function download(path: string, init: RequestInit, fallback: string) {
  const headers = new Headers(init.headers);
  const token = getStoredAuthToken();

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers,
    credentials: init.credentials ?? "include"
  });

  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status);
  }

  return {
    filename: parseFilename(response.headers, fallback),
    blob: await response.blob()
  };
}

export async function exportLesson(planId: string, format: "docx" | "pdf") {
  return download(
    "/api/export",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ plan_id: planId, format, template: "default" })
    },
    `lesson-plan-${planId}.${format}`
  );
}

export async function exportPresentation(planId: string) {
  return download(
    `/api/presentations/${planId}/export`,
    {
      method: "POST"
    },
    `presentation-${planId}.pptx`
  );
}

export async function fetchKnowledgeFiles(fileType?: string) {
  return apiRequest<KnowledgeFileListResponse>("/api/knowledge/files", {
    params: { file_type: fileType }
  });
}

export async function uploadKnowledgeDocument(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiRequest<KnowledgeFile>("/api/knowledge/upload/document", {
    method: "POST",
    body: formData
  });
}

export async function uploadKnowledgeImage(file: File, description: string) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("description", description);
  return apiRequest<KnowledgeFile>("/api/knowledge/upload/image", {
    method: "POST",
    body: formData
  });
}

export async function deleteKnowledgeFile(fileId: string) {
  return apiRequest<void>(`/api/knowledge/files/${fileId}`, {
    method: "DELETE"
  });
}

export async function updateKnowledgeFile(
  fileId: string,
  payload: Partial<{
    filename: string;
    description: string | null;
    tags: string[];
  }>
) {
  return apiRequest<KnowledgeFile>(`/api/knowledge/files/${fileId}`, {
    method: "PATCH",
    body: JSON.stringify(payload)
  });
}

export async function searchKnowledge(payload: {
  query: string;
  top_k?: number;
  file_type?: string;
  enable_llm_rerank?: boolean;
}) {
  return apiRequest<KnowledgeSearchResult[]>("/api/knowledge/search", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function answerWithKnowledge(payload: {
  query: string;
  top_k?: number;
  file_type?: string;
  enable_llm_rerank?: boolean;
}) {
  return apiRequest<KnowledgeAnswerResponse>("/api/knowledge/answer", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function listPreferences() {
  return apiRequest<PreferencePreset[]>("/api/preferences");
}

export async function createPreference(payload: {
  name: string;
  description?: string;
  prompt_injection: string;
  structured_preferences?: TempPreferencesPayload;
  tags: string[];
  is_active: boolean;
}) {
  return apiRequest<PreferencePreset>("/api/preferences", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function updatePreference(
  presetId: string,
  payload: Partial<{
    name: string;
    description: string;
    prompt_injection: string;
    structured_preferences: TempPreferencesPayload;
    tags: string[];
    is_active: boolean;
  }>
) {
  return apiRequest<PreferencePreset>(`/api/preferences/${presetId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function deletePreference(presetId: string) {
  return apiRequest<void>(`/api/preferences/${presetId}`, {
    method: "DELETE"
  });
}

export async function togglePreference(presetId: string) {
  return apiRequest<PreferencePreset>(`/api/preferences/${presetId}/toggle`, {
    method: "PATCH"
  });
}

export async function parsePreferenceText(naturalLanguage: string) {
  return apiRequest<{ suggestions: PreferenceSuggestion[] }>("/api/preferences/parse", {
    method: "POST",
    body: JSON.stringify({ natural_language: naturalLanguage })
  });
}

export function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.URL.revokeObjectURL(url);
}
