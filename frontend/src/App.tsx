import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import ChatPanel from "./ChatPanel";
import NotesPanel from "./NotesPanel";
import { Markdown } from "./Markdown";
import { IconDownload } from "./icons";
import {
  deleteSummary,
  fetchMetas,
  fetchPapers,
  fetchSummary,
  importPaper,
  paperUrl,
  searchFulltext,
  generateSummary,
  updatePaperMeta,
} from "./api";
import { downloadText, formatDate, formatSize, parseSummary } from "./summary";
import type {
  FilterKey,
  FulltextResult,
  Paper,
  PaperMeta,
} from "./types";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "starred", label: "收藏" },
  { key: "read", label: "已读" },
  { key: "unread", label: "未读" },
];

type TabKey = "split" | "chat" | "notes";

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "split", label: "对照阅读" },
  { key: "chat", label: "对话" },
  { key: "notes", label: "笔记" },
];

const EMPTY_META: PaperMeta = { starred: false, note: "", clips: [], digests: [] };
const THEME_KEY = "paperreader-theme";

/* ---------- 迷你图标（16px 线性风格） ---------- */

function IconStar({ filled }: { filled: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
      <path
        d="M12 2.6l2.8 6 6.4.6-4.8 4.3 1.4 6.3-5.8-3.3-5.8 3.3 1.4-6.3-4.8-4.3 6.4-.6z"
        fill={filled ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconRefresh({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      aria-hidden
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={spinning ? "spin" : undefined}
    >
      <path d="M20.5 12a8.5 8.5 0 1 1-2.5-6" />
      <path d="M20.5 3.5V7h-3.5" />
    </svg>
  );
}

function IconExternal() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M18 13.5V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 19V8a1.5 1.5 0 0 1 1.5-1.5H11" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 6h16M9 6V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V6m3 0-.8 13a2 2 0 0 1-2 1.9H8.8a2 2 0 0 1-2-1.9L6 6" />
    </svg>
  );
}

function IconSun() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M18.7 5.3l-1.5 1.5M6.8 17.2l-1.5 1.5" />
    </svg>
  );
}

function IconMoon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20.5 14.5A8.5 8.5 0 1 1 9.5 3.5a7 7 0 0 0 11 11z" />
    </svg>
  );
}

/* ---------- 工具函数 ---------- */

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 全文搜索片段：关键词高亮（已转义，无 XSS 风险）。 */
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts = text.split(new RegExp(`(${escapeRegExp(query)})`, "gi"));
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>
      )}
    </>
  );
}

/** 元信息统一为标签 + 徽章：作者/机构按分号拆，来源按逗号拆。 */
function metaChips(label: string, value: string): string[] {
  const sep = label === "来源" ? /[，,]/ : /[;；]/;
  const parts = value.split(sep).map((x) => x.trim()).filter(Boolean);
  return parts.length > 0 ? parts : [value];
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="meta-row">
      <span className="meta-label">{label}</span>
      <span className="meta-value">
        {metaChips(label, value).map((chip) => (
          <span key={chip} className="meta-chip">
            {chip}
          </span>
        ))}
      </span>
    </div>
  );
}

function themeFromStorage(): "light" | "dark" {
  try {
    return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

/* ---------- 主组件 ---------- */

export default function App() {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [stats, setStats] = useState({ total: 0, read: 0, unread: 0 });
  const [metas, setMetas] = useState<Record<string, PaperMeta>>({});
  /** 进行中的对话请求按论文跟踪：切走再切回思考框仍在，侧栏显示后台回答指示。 */
  const [pendingStems, setPendingStems] = useState<Record<string, true>>({});
  const setPending = useCallback((stem: string, on: boolean) => {
    setPendingStems((prev) => {
      if (on === !!prev[stem]) return prev;
      const next = { ...prev };
      if (on) next[stem] = true;
      else delete next[stem];
      return next;
    });
  }, []);
  /** 笔记页整理/重整状态按论文跟踪：切论文再切回不会误判为空闲、避免重复整理。 */
  const [notesBusy, setNotesBusy] = useState<Record<string, string>>({});
  const setNotesBusyFor = useCallback((stem: string, key: string | null) => {
    setNotesBusy((prev) => {
      if (key === null) {
        if (!(stem in prev)) return prev;
        const next = { ...prev };
        delete next[stem];
        return next;
      }
      if (prev[stem] === key) return prev;
      return { ...prev, [stem]: key };
    });
  }, []);
  const [filter, setFilter] = useState<FilterKey>("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  /** 当前选中论文的 ref：异步回调里判断「结果该归属哪篇论文」，防止跨论文覆盖视图。 */
  const selectedRef = useRef<string | null>(null);
  useEffect(() => {
    selectedRef.current = selected;
  }, [selected]);
  const [summaryMd, setSummaryMd] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("split");
  const [theme, setTheme] = useState<"light" | "dark">(themeFromStorage);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingSummary, setLoadingSummary] = useState(false);
  /** 正在生成 Summary 的论文 stem；null 表示空闲。按论文跟踪，不顶掉其他论文的 summary。 */
  const [generatingStem, setGeneratingStem] = useState<string | null>(null);
  const generatingStemRef = useRef<string | null>(null);
  useEffect(() => {
    generatingStemRef.current = generatingStem;
  }, [generatingStem]);
  const [marking, setMarking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fulltext, setFulltext] = useState<FulltextResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<{ type: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  const refreshList = useCallback(async () => {
    try {
      const data = await fetchPapers();
      setPapers(data.papers);
      setStats(data.stats);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    void refreshList();
    fetchMetas()
      .then(setMetas)
      .catch(() => undefined);
  }, [refreshList]);

  const loadSummary = useCallback(async (stem: string) => {
    setLoadingSummary(true);
    setSummaryMd(null);
    try {
      const data = await fetchSummary(stem);
      // 期间切换了论文：旧结果只放弃展示，不污染当前论文的视图
      if (selectedRef.current !== stem) return;
      setSummaryMd(data.read ? data.markdown : null);
      setError(null);
    } catch (e) {
      if (selectedRef.current !== stem) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (selectedRef.current === stem) setLoadingSummary(false);
    }
  }, []);

  const select = useCallback(
    (stem: string) => {
      setSelected(stem);
      setTab("split");
      void loadSummary(stem);
    },
    [loadSummary]
  );

  const generate = async (paper: Paper) => {
    // 生成状态按论文跟踪：只影响该论文自己的视图，其他论文照常显示已有 summary。
    // 非流式一次性生成：等待期间显示转圈与进度提示，完成后由服务端落盘并返回成品。
    setGeneratingStem(paper.stem);
    setError(null);
    try {
      const result = await generateSummary(paper.stem, paper.read);
      // 期间切换了论文：结果已由服务端落盘，不覆盖当前论文的视图
      if (selectedRef.current === paper.stem) {
        setSummaryMd(result.markdown);
      }
      await refreshList();
    } catch (e) {
      if (selectedRef.current === paper.stem) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setGeneratingStem((cur) => (cur === paper.stem ? null : cur));
    }
  };

  /** 对话页先摘要后回答：论文未读时先生成 Summary（含并发/重复防护），完成后返回。 */
  const ensureSummary = useCallback(
    async (stem: string) => {
      const paper = papers.find((p) => p.stem === stem);
      if (!paper) throw new Error("找不到这篇论文");
      if (paper.read) return;
      if (generatingStem === stem) {
        // 该论文已在生成中：等待其完成（轮询直到状态清空）
        while (generatingStemRef.current === stem) {
          await new Promise((r) => setTimeout(r, 500));
        }
        return;
      }
      await generate(paper);
    },
    [papers, generatingStem]
  );

  const markUnread = async (paper: Paper) => {
    if (!window.confirm("删除对应的 summary，将这篇论文标记为未读？")) return;
    setMarking(true);
    try {
      await deleteSummary(paper.stem);
      setSummaryMd(null);
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMarking(false);
    }
  };

  const toggleStar = async (paper: Paper) => {
    const prev = metas[paper.stem] ?? EMPTY_META;
    const next = { ...prev, starred: !prev.starred };
    setMetas((m) => ({ ...m, [paper.stem]: next }));
    try {
      const saved = await updatePaperMeta(paper.stem, { starred: next.starred });
      setMetas((m) => ({ ...m, [paper.stem]: saved }));
    } catch (e) {
      setMetas((m) => ({ ...m, [paper.stem]: prev }));
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  /** 局部更新论文元信息（note / clips / digests），乐观更新后持久化。 */
  const patchMeta = useCallback(
    async (stem: string, patch: Partial<PaperMeta>) => {
      const prev = metas[stem] ?? EMPTY_META;
      const optimistic = { ...prev, ...patch };
      setMetas((m) => ({ ...m, [stem]: optimistic }));
      try {
        const saved = await updatePaperMeta(stem, patch);
        setMetas((m) => ({ ...m, [stem]: saved }));
      } catch (e) {
        setMetas((m) => ({ ...m, [stem]: prev }));
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [metas]
  );

  /** 对话页「存入笔记」：追加一条问答摘录。 */
  const saveClip = useCallback(
    (stem: string, question: string, answer: string) => {
      const prev = metas[stem] ?? EMPTY_META;
      const clip = {
        id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
        ts: new Date().toISOString(),
        question,
        answer,
      };
      void patchMeta(stem, { clips: [...prev.clips, clip] });
    },
    [metas, patchMeta]
  );

  // 全文搜索：输入 >=2 字符时防抖触发
  useEffect(() => {
    const q = search.trim();
    if (q.length < 2) {
      setFulltext([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      searchFulltext(q)
        .then((r) => setFulltext(r.results))
        .catch(() => setFulltext([]))
        .finally(() => setSearching(false));
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const visible = useMemo(() => {
    const kw = search.trim().toLowerCase();
    const list = papers.filter((p) => {
      if (filter === "read" && !p.read) return false;
      if (filter === "unread" && p.read) return false;
      if (filter === "starred" && !metas[p.stem]?.starred) return false;
      if (!kw) return true;
      return (
        p.stem.toLowerCase().includes(kw) ||
        (p.title ?? "").toLowerCase().includes(kw)
      );
    });
    // 收藏优先，其余保持原有的修改时间倒序
    return [...list].sort(
      (a, b) =>
        Number(metas[b.stem]?.starred ?? false) -
        Number(metas[a.stem]?.starred ?? false)
    );
  }, [papers, filter, search, metas]);

  const current = papers.find((p) => p.stem === selected) ?? null;
  const currentMeta = (selected && metas[selected]) || EMPTY_META;
  const parsed = useMemo(
    () => (summaryMd ? parseSummary(summaryMd) : null),
    [summaryMd]
  );

  const exportSummary = () => {
    if (!summaryMd || !current) return;
    downloadText(`${current.stem}.md`, summaryMd);
  };

  const doImport = async () => {
    const url = importUrl.trim();
    if (!url || importing) return;
    setImporting(true);
    setImportMsg(null);
    try {
      const r = await importPaper(url);
      setImportMsg({
        type: "ok",
        text: `已下载 ${r.filename}（${formatSize(r.size)}），正在打开…`,
      });
      setImportUrl("");
      await refreshList();
      setTimeout(() => {
        setImportOpen(false);
        setImportMsg(null);
        select(r.stem);
      }, 900);
    } catch (e) {
      setImportMsg({
        type: "error",
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>
          <span className="logo">📄</span> PaperReader
          <span className="subtitle">论文阅读器</span>
        </h1>
        <div className="header-right">
          <div className="stats">
            <span>共 {stats.total} 篇</span>
            <span className="stat-read">已读 {stats.read}</span>
            <span className="stat-unread">未读 {stats.unread}</span>
          </div>
          <button
            className="icon-btn"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
            aria-label="切换主题"
          >
            {theme === "dark" ? <IconSun /> : <IconMoon />}
          </button>
        </div>
      </header>

      {error && <div className="error-bar">⚠ {error}</div>}

      <div className="layout">
        <aside className="sidebar">
          <div className="filter-row">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={`filter${filter === f.key ? " active" : ""}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="search-row">
            <input
              className="search"
              name="search"
              type="search"
              placeholder="搜索文件名、标题或全文…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button
              className={`icon-btn${importOpen ? " active" : ""}`}
              onClick={() => setImportOpen((v) => !v)}
              title="从 URL 下载 PDF 入库"
              aria-label="导入论文"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>
          </div>
          {importOpen && (
            <div className="import-panel">
              <input
                className="import-input"
                type="url"
                placeholder="粘贴 PDF 或 arXiv 链接，如 https://arxiv.org/abs/2609.01807"
                value={importUrl}
                disabled={importing}
                onChange={(e) => setImportUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                    void doImport();
                  }
                }}
                autoFocus
              />
              <button
                className="bt small import-btn"
                disabled={importing || !importUrl.trim()}
                onClick={() => void doImport()}
              >
                {importing ? "下载中…" : "下载入库"}
              </button>
              {importMsg && (
                <div className={`import-msg ${importMsg.type}`}>
                  {importMsg.text}
                </div>
              )}
            </div>
          )}
          <div className="paper-list">
            {loadingList ? (
              <div className="placeholder">加载论文列表中…</div>
            ) : visible.length === 0 ? (
              <div className="placeholder">没有符合条件的论文</div>
            ) : (
              visible.map((p) => {
                const starred = metas[p.stem]?.starred ?? false;
                return (
                  <button
                    key={p.stem}
                    className={`paper-item${selected === p.stem ? " active" : ""}`}
                    onClick={() => select(p.stem)}
                  >
                    <span className={`dot ${p.read ? "read" : "unread"}`} />
                    <span className="paper-item-main">
                      <span className="paper-item-title">
                        {p.title || p.stem}
                      </span>
                      <span className="paper-item-sub">
                        {p.stem} · {formatSize(p.size)} · {formatDate(p.mtime)}
                      </span>
                    </span>
                    {generatingStem === p.stem && (
                      <span
                        className="paper-item-generating"
                        title="AI 正在生成这篇论文的 Summary…"
                      />
                    )}
                    {notesBusy[p.stem] && (
                      <span
                        className="paper-item-digesting"
                        title="AI 正在整理这篇论文的笔记…"
                      />
                    )}
                    {pendingStems[p.stem] && (
                      <span
                        className="paper-item-pending"
                        title="AI 正在后台回答这篇论文的提问…"
                      />
                    )}
                    {starred && <span className="paper-item-star">★</span>}
                  </button>
                );
              })
            )}
            {search.trim().length >= 2 && (
              <div className="fulltext-block">
                <div className="fulltext-title">
                  全文命中 {searching ? "…" : `${fulltext.length} 篇`}
                </div>
                {!searching && fulltext.length === 0 && (
                  <div className="placeholder">Summary 中没有匹配内容</div>
                )}
                {fulltext.map((r) => (
                  <button
                    key={r.stem}
                    className="fulltext-item"
                    onClick={() => select(r.stem)}
                  >
                    <span className="fulltext-stem">
                      {r.title || r.stem}
                    </span>
                    <span className="fulltext-snippet">
                      <Highlight text={r.snippet} query={search.trim()} />
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </aside>

        <main className="detail">
          {!current ? (
            <div className="welcome card">
              <h2>开始阅读</h2>

              <div className="guide-section">
                <div className="guide-title">1 · 导入论文</div>
                <ul>
                  <li>
                    把 PDF 放入 <code>papers/</code> 文件夹
                  </li>
                  <li>
                    或点击左侧搜索框旁的 <span className="kbd">＋</span>{" "}
                    按钮，粘贴 PDF / arXiv
                    链接一键下载入库（arXiv 页面链接会自动转成 PDF 直链）
                  </li>
                </ul>
              </div>

              <div className="guide-section">
                <div className="guide-title">2 · 生成与阅读</div>
                <ul>
                  <li>
                    从左侧选择一篇论文，点工具栏左侧第一个按钮（刷新状图标）一次性生成
                    Summary（思考型模型通常需要几分钟；期间可切看其他论文，生成照常完成）
                  </li>
                  <li>「对照阅读」左边是原文 PDF，右边是 Summary 卡片，可边读边看</li>
                  <li>Summary 标题上方有「导出 Markdown」按钮，可下载 .md 文件</li>
                  <li>已读 = summaries/ 下存在对应 markdown</li>
                </ul>
              </div>

              <div className="guide-section">
                <div className="guide-title">3 · 工具栏按钮</div>
                <div className="icon-guide">
                  <div className="guide-row">
                    <span className="guide-icon">
                      <IconRefresh spinning={false} />
                    </span>
                    生成 / 重新生成 Summary
                  </div>
                  <div className="guide-row">
                    <span className="guide-icon">
                      <IconStar filled={false} />
                    </span>
                    收藏论文（列表置顶显示 ★）
                  </div>
                  <div className="guide-row">
                    <span className="guide-icon">
                      <IconExternal />
                    </span>
                    新窗口打开 PDF 原文
                  </div>
                  <div className="guide-row">
                    <span className="guide-icon">
                      <IconTrash />
                    </span>
                    删除 Summary，标记回未读
                  </div>
                </div>
              </div>

              <div className="guide-section">
                <div className="guide-title">4 · 更多</div>
                <ul>
                  <li>
                    「对话」：就论文内容与 AI
                    多轮追问，回答实时流式显示。未读论文直接提问时，AI
                    会先生成 Summary 再回答；回答后还会给出可点击的「继续追问」建议
                  </li>
                  <li>
                    「笔记」：对话里点「＋ 存入笔记」收集摘录，也可点「＋
                    写点自己的想法」手写卡片；一键融合整理成统一笔记，章节会注明来源；可导出全部笔记为
                    .md 文件
                  </li>
                  <li>
                    侧栏论文右侧的小转圈：蓝色 = AI
                    正在后台回答这篇论文的提问，橙色 = AI
                    正在整理这篇论文的笔记，绿色 = AI
                    正在生成这篇论文的 Summary（切去别的页面都会照常完成）
                  </li>
                  <li>
                    顶部搜索框：搜文件名 / 标题，输入 2
                    字以上还会全文搜索所有 Summary
                  </li>
                  <li>右上角的月亮 / 太阳图标：深色 / 浅色模式切换</li>
                </ul>
              </div>
            </div>
          ) : (
            <div className="detail-inner">
              <div className="detail-head">
                <div className="detail-title-row">
                  <h2 className="detail-title">
                    {current.title || current.stem}
                  </h2>
                  <span className={`badge ${current.read ? "read" : "unread"}`}>
                    {current.read ? "✅ 已读" : "⬜ 未读"}
                  </span>
                  {currentMeta.starred && (
                    <span className="badge starred">★ 已收藏</span>
                  )}
                </div>
                <div className="detail-meta">
                  {current.stem} · {formatSize(current.size)} ·{" "}
                  {formatDate(current.mtime)}
                  {parsed && parsed.metaPairs.length > 0 && (
                    <div className="meta-grid">
                      {parsed.metaPairs.map((m) => (
                        <MetaRow key={m.label} label={m.label} value={m.value} />
                      ))}
                    </div>
                  )}
                </div>

                <div className="detail-toolbar">
                  <div className="icon-group">
                    <button
                      className="icon-btn"
                      disabled={generatingStem !== null}
                      onClick={() => void generate(current)}
                      title={
                        generatingStem === current.stem
                          ? "正在生成…"
                          : generatingStem !== null
                            ? "已有论文正在生成 Summary，请稍候"
                            : current.read
                              ? "重新生成 Summary"
                              : "生成 Summary"
                      }
                    >
                      <IconRefresh spinning={generatingStem === current.stem} />
                    </button>
                    <button
                      className={`icon-btn${currentMeta.starred ? " starred" : ""}`}
                      onClick={() => void toggleStar(current)}
                      title={currentMeta.starred ? "取消收藏" : "收藏这篇论文"}
                    >
                      <IconStar filled={currentMeta.starred} />
                    </button>
                    <a
                      className="icon-btn"
                      href={paperUrl(current.stem, "/pdf")}
                      target="_blank"
                      rel="noreferrer"
                      title="新窗口打开 PDF"
                    >
                      <IconExternal />
                    </a>
                    {current.read && (
                      <button
                        className="icon-btn danger"
                        onClick={() => void markUnread(current)}
                        disabled={marking}
                        title="删除 Summary，标记为未读"
                      >
                        <IconTrash />
                      </button>
                    )}
                  </div>
                  {generatingStem === current.stem && (
                    <span className="toolbar-hint">
                      Agent 正在阅读论文并思考…（思考型模型，可能需要几分钟）
                    </span>
                  )}
                </div>
              </div>

              <nav className="tabs">
                {TABS.map((t) => (
                  <button
                    key={t.key}
                    className={`tab${tab === t.key ? " active" : ""}`}
                    onClick={() => setTab(t.key)}
                  >
                    {t.label}
                  </button>
                ))}
              </nav>

              <section
                className={`tab-body split-body${tab === "split" ? "" : " tab-hidden"}`}
              >
                  <iframe
                    key={current.stem}
                    className="pdf-frame split-pdf"
                    src={paperUrl(current.stem, "/pdf")}
                    title={`PDF 原文：${current.stem}`}
                  />
                  <div className="split-right">
                    {generatingStem === current.stem ? (
                      <div className="placeholder">
                        <div className="gen-head">
                          <span className="spinner" /> Agent
                          正在通读论文并撰写结构化 Summary…
                        </div>
                        <div className="gen-progress">
                          思考型模型通常需要几分钟，请耐心等待；期间可切换查看其他论文，生成会照常完成。
                        </div>
                      </div>
                    ) : loadingSummary ? (
                      <div className="placeholder">加载 Summary 中…</div>
                    ) : parsed && parsed.sections.length > 0 ? (
                      <>
                        {parsed.title && (
                          <div className="summary-tools">
                            <button
                              className="bt small"
                              onClick={exportSummary}
                              title="下载 Summary 为 .md 文件"
                            >
                              <IconDownload /> 导出 Markdown
                            </button>
                          </div>
                        )}
                        {parsed.title && (
                          <h1 className="summary-title">{parsed.title}</h1>
                        )}
                        {parsed.sections.map((s) => (
                          <section
                            key={s.heading}
                            className="section-card"
                            style={
                              {
                                "--sec-color": s.color,
                                "--sec-border": s.border,
                                "--sec-bg": s.bg,
                              } as CSSProperties
                            }
                          >
                            <h3>{s.heading}</h3>
                            <div className="section-body">
                              <Markdown text={s.body} />
                            </div>
                          </section>
                        ))}
                      </>
                    ) : (
                      <div className="placeholder card">
                        还没有 Summary。点上方「生成 Summary」让 AI
                        通读这篇论文，或直接到「对话」页提问——AI
                        会先生成 Summary 再回答你的问题。
                      </div>
                    )}
                  </div>
                </section>

              <section className={`tab-body chat-body${tab === "chat" ? "" : " tab-hidden"}`}>
                <ChatPanel
                  stem={current.stem}
                  hasSummary={current.read}
                  pending={!!pendingStems[current.stem]}
                  onPending={setPending}
                  onSaveClip={(question, answer) => saveClip(current.stem, question, answer)}
                  onEnsureSummary={ensureSummary}
                />
              </section>

              <section className={`tab-body notes-tab${tab === "notes" ? "" : " tab-hidden"}`}>
                <NotesPanel
                  stem={current.stem}
                  meta={currentMeta}
                  busyKey={notesBusy[current.stem] ?? null}
                  onBusy={setNotesBusyFor}
                  onPatch={(patch) => void patchMeta(current.stem, patch)}
                />
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
