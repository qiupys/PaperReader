import { useEffect, useMemo, useRef, useState } from "react";

function IconRefresh({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="14"
      height="14"
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

/**
 * ModelPicker — 侧栏顶部的模型选择下拉框。
 * 自定义面板（可搜索、带刷新），样式跟随应用的明暗主题变量。
 */
export default function ModelPicker({
  models,
  model,
  loading,
  error,
  onPick,
  onRefresh,
}: {
  models: string[];
  model: string;
  loading: boolean;
  error: boolean;
  onPick: (m: string) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  // 点击组件外关闭
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => m.toLowerCase().includes(q));
  }, [models, query]);

  const display = model || (error ? "模型列表不可用" : loading ? "加载模型中…" : "选择模型");

  return (
    <div className="model-row" ref={rootRef}>
      <button
        type="button"
        className={`model-trigger${open ? " open" : ""}`}
        onClick={() => {
          setOpen((v) => !v);
          setQuery("");
        }}
        title="选择 LLM 模型（Summary 生成与对话共用）"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="model-trigger-icon" aria-hidden>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="5" y="7" width="14" height="11" rx="2.5" />
            <path d="M12 7V4.5M9 12h.01M15 12h.01M9.5 15.2c1.6.9 3.4.9 5 0" />
            <circle cx="12" cy="3.5" r="1.4" />
          </svg>
        </span>
        <span className="model-trigger-label">{display}</span>
        <span className={`model-chevron${open ? " up" : ""}`} aria-hidden>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="model-panel" role="listbox">
          <div className="model-search-row">
            <input
              className="model-search"
              type="text"
              placeholder="搜索模型…"
              value={query}
              autoFocus
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && filtered.length > 0) {
                  onPick(filtered[0]);
                  setOpen(false);
                }
              }}
            />
            <button
              type="button"
              className="icon-btn"
              onClick={onRefresh}
              disabled={loading}
              title="重新拉取模型列表"
              aria-label="刷新模型列表"
            >
              <IconRefresh spinning={loading} />
            </button>
          </div>

          <div className="model-list">
            {loading && models.length === 0 && (
              <div className="model-empty">加载模型列表中…</div>
            )}
            {!loading && models.length === 0 && (
              <div className="model-empty model-empty-err">
                {error ? "列表拉取失败，点上方刷新重试" : "暂无可用模型"}
              </div>
            )}
            {filtered.map((m) => (
              <button
                type="button"
                key={m}
                role="option"
                aria-selected={m === model}
                className={`model-item${m === model ? " active" : ""}`}
                onClick={() => {
                  onPick(m);
                  setOpen(false);
                }}
              >
                <span className="model-item-name">{m}</span>
                {m === model && (
                  <span className="model-item-check" aria-hidden>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4 12.5l5 5L20 6.5" />
                    </svg>
                  </span>
                )}
              </button>
            ))}
            {models.length > 0 && filtered.length === 0 && (
              <div className="model-empty">没有匹配「{query}」的模型</div>
            )}
          </div>

          <div className="model-foot">
            <span>{models.length > 0 ? `${models.length} 个可用模型` : "—"}</span>
            <span className="model-foot-hint">Summary 与对话共用</span>
          </div>
        </div>
      )}
    </div>
  );
}
