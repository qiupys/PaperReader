import type {
  ChatClip,
  ChatResponse,
  ChatTurn,
  FulltextResponse,
  NoteDigest,
  PaperMeta,
  PaperMetaUpdate,
  PapersResponse,
  SummarizeResponse,
  SummaryResponse,
} from "./types";

/** stem 含子目录（a/b.pdf -> a/b），逐段编码、保留斜杠结构。 */
export function paperUrl(stem: string, suffix = ""): string {
  const encoded = stem
    .split("/")
    .map((seg) => encodeURIComponent(seg))
    .join("/");
  return `/api/papers/${encoded}${suffix}`;
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: unknown };
      if (data?.detail) detail = String(data.detail);
    } catch {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export async function fetchPapers(): Promise<PapersResponse> {
  return handle<PapersResponse>(await fetch("/api/papers"));
}

export async function fetchSummary(stem: string): Promise<SummaryResponse> {
  return handle<SummaryResponse>(await fetch(paperUrl(stem, "/summary")));
}

export async function generateSummary(
  stem: string,
  force: boolean
): Promise<SummarizeResponse> {
  return handle<SummarizeResponse>(
    await fetch(paperUrl(stem, `/summarize?force=${force}`), { method: "POST" })
  );
}

export async function deleteSummary(stem: string): Promise<void> {
  await handle<unknown>(await fetch(paperUrl(stem, "/summary"), { method: "DELETE" }));
}

export async function searchFulltext(q: string): Promise<FulltextResponse> {
  return handle<FulltextResponse>(
    await fetch(`/api/search?q=${encodeURIComponent(q)}`)
  );
}

export async function fetchMetas(): Promise<Record<string, PaperMeta>> {
  const data = await handle<{ metas: Record<string, PaperMeta> }>(
    await fetch("/api/meta")
  );
  return data.metas;
}

export async function updatePaperMeta(
  stem: string,
  patch: PaperMetaUpdate
): Promise<PaperMeta> {
  const data = await handle<{ meta: PaperMeta }>(
    await fetch(paperUrl(stem, "/meta"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    })
  );
  return data.meta;
}

export interface ImportResult {
  stem: string;
  filename: string;
  size: number;
}

export async function importPaper(url: string): Promise<ImportResult> {
  return handle<ImportResult>(
    await fetch("/api/papers/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    })
  );
}

export async function digestClips(
  stem: string,
  clips: ChatClip[],
  prior = ""
): Promise<NoteDigest> {
  const data = await handle<{ digest: NoteDigest }>(
    await fetch(paperUrl(stem, "/digest-clips"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clips, prior }),
    })
  );
  return data.digest;
}

export async function redigest(stem: string, text: string): Promise<NoteDigest> {
  const data = await handle<{ digest: NoteDigest }>(
    await fetch(paperUrl(stem, "/redigest"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
  );
  return data.digest;
}

export async function sendChat(
  stem: string,
  message: string,
  history: ChatTurn[]
): Promise<ChatResponse> {
  const body = {
    message,
    history: history
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content })),
  };
  return handle<ChatResponse>(
    await fetch(paperUrl(stem, "/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

// ---------- SSE 流式接口 ----------

/** 解析 text/event-stream 响应，逐事件回调；error 事件抛异常。 */
async function readSSE(
  resp: Response,
  onEvent: (evt: { type: string; [k: string]: unknown }) => void
): Promise<void> {
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: unknown };
      if (data?.detail) detail = String(data.detail);
    } catch {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!raw.startsWith("data:")) continue;
      const evt = JSON.parse(raw.slice(5).trim()) as {
        type: string;
        [k: string]: unknown;
      };
      if (evt.type === "error") throw new Error(String(evt.message ?? "生成失败"));
      onEvent(evt);
    }
  }
}

/** 流式对话：onDelta 逐段接收回答增量（不含思考过程）。 */
export async function streamChat(
  stem: string,
  message: string,
  history: ChatTurn[],
  onDelta: (text: string) => void
): Promise<void> {
  const body = {
    message,
    history: history
      .filter((m) => !m.error)
      .map((m) => ({ role: m.role, content: m.content })),
  };
  await readSSE(
    await fetch(paperUrl(stem, "/chat/stream"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
    (evt) => {
      if (evt.type === "delta") onDelta(String(evt.text ?? ""));
    }
  );
}

/** 流式生成 Summary：onDelta 接收原始增量（生成预览），完成返回渲染后的 markdown。 */
export async function streamSummary(
  stem: string,
  force: boolean,
  onDelta: (text: string) => void
): Promise<SummarizeResponse> {
  let result: SummarizeResponse | null = null;
  await readSSE(
    await fetch(paperUrl(stem, `/summarize/stream?force=${force}`), {
      method: "POST",
    }),
    (evt) => {
      if (evt.type === "delta") onDelta(String(evt.text ?? ""));
      else if (evt.type === "done") result = evt as unknown as SummarizeResponse;
    }
  );
  if (!result) throw new Error("生成流意外结束，请重试");
  return result;
}
