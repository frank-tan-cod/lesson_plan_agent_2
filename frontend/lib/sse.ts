import { ApiError, getApiBaseUrl, getStoredAuthToken } from "@/lib/api";
import { sleep } from "@/lib/utils";

interface StreamSseOptions {
  path: string;
  body: Record<string, unknown>;
  signal?: AbortSignal;
  retries?: number;
  onEvent: (event: { event: string; data: unknown }) => void;
}

async function parseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail || `流式请求失败 (${response.status})`;
  } catch {
    return `流式请求失败 (${response.status})`;
  }
}

function parseSseChunk(chunk: string, emit: (event: string, data: unknown) => void) {
  const blocks = chunk.split("\n\n");
  const remainder = blocks.pop() ?? "";

  for (const block of blocks) {
    const lines = block.split("\n");
    let event = "message";
    const dataLines: string[] = [];

    for (const line of lines) {
      const normalized = line.replace(/\r$/, "");
      if (normalized.startsWith("event:")) {
        event = normalized.slice(6).trim();
      }
      if (normalized.startsWith("data:")) {
        dataLines.push(normalized.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      continue;
    }

    const raw = dataLines.join("\n");
    try {
      emit(event, JSON.parse(raw));
    } catch {
      emit(event, raw);
    }
  }

  return remainder;
}

export async function streamSse({ path, body, signal, retries = 1, onEvent }: StreamSseOptions) {
  let attempt = 0;

  while (attempt <= retries) {
    let hasReceivedAnyEvent = false;

    try {
      const headers = new Headers({
        "Content-Type": "application/json"
      });
      const token = getStoredAuthToken();
      if (token) {
        headers.set("Authorization", `Bearer ${token}`);
      }

      const response = await fetch(`${getApiBaseUrl()}${path}`, {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal,
        credentials: "include",
        cache: "no-store"
      });

      if (!response.ok) {
        throw new ApiError(await parseError(response), response.status);
      }

      if (!response.body) {
        throw new ApiError("服务端没有返回可读取的数据流。", 500);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        buffer = parseSseChunk(buffer, (event, data) => {
          hasReceivedAnyEvent = true;
          onEvent({ event, data });
        });
      }

      if (buffer.trim()) {
        parseSseChunk(`${buffer}\n\n`, (event, data) => {
          hasReceivedAnyEvent = true;
          onEvent({ event, data });
        });
      }

      return;
    } catch (error) {
      if (signal?.aborted) {
        return;
      }

      if (hasReceivedAnyEvent || attempt >= retries) {
        throw error;
      }

      await sleep(Math.min(400 * 2 ** attempt, 1600));
      attempt += 1;
    }
  }
}
