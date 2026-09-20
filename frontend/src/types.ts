export interface Paper {
  stem: string;
  pdf_path: string;
  summary_path: string;
  size: number;
  mtime: number;
  read: boolean;
  title: string | null;
}

export interface PapersResponse {
  papers: Paper[];
  stats: { total: number; read: number; unread: number };
}

export interface SummaryResponse {
  stem: string;
  read: boolean;
  title: string | null;
  markdown: string | null;
}

export interface SummarizeResponse {
  stem: string;
  read: boolean;
  title: string;
  summary_path: string;
  pages: number;
  chars: number;
  markdown: string;
}

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
  error?: boolean;
}

export interface ChatResponse {
  stem: string;
  reply: string;
  model: string;
}

export type FilterKey = "all" | "starred" | "read" | "unread";

export interface FulltextResult {
  stem: string;
  title: string | null;
  snippet: string;
}

export interface FulltextResponse {
  query: string;
  results: FulltextResult[];
}

export interface ChatClip {
  id: string;
  ts: string;
  question: string;
  answer: string;
}

export interface NoteDigest {
  id: string;
  ts: string;
  text: string;
}

export interface PaperMeta {
  starred: boolean;
  note: string;
  clips: ChatClip[];
  digests: NoteDigest[];
}

export interface PaperMetaUpdate {
  starred?: boolean;
  note?: string;
  clips?: ChatClip[];
  digests?: NoteDigest[];
}
