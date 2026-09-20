import { useEffect, useRef, useState } from "react";
import { digestClips, redigest, updatePaperMeta } from "./api";
import { CiteMarkdown } from "./Cite";
import { IconDownload } from "./icons";
import { downloadText } from "./summary";
import type { NoteDigest, PaperMeta } from "./types";

function fmtTs(ts: string): string {
  try {
    return ts.replace("T", " ").slice(0, 16);
  } catch {
    return ts;
  }
}

type Patch = Partial<Pick<PaperMeta, "note" | "clips" | "digests">>;

interface NotesPanelProps {
  stem: string;
  meta: PaperMeta;
  /** 本篇论文的整理任务标识："clips" | digest.id；null 表示空闲。由 App 按论文跟踪。 */
  busyKey: string | null;
  onBusy: (stem: string, key: string | null) => void;
  onPatch: (patch: Patch) => void;
  /** 点击笔记里的 [p.N] 页码引用时跳转 PDF 对应页。 */
  onCitePage?: (page: number) => void;
}

export default function NotesPanel({
  stem,
  meta,
  busyKey,
  onBusy,
  onPatch,
  onCitePage,
}: NotesPanelProps) {
  const [editingNote, setEditingNote] = useState(false);
  const [noteDraft, setNoteDraft] = useState(meta.note);
  const [noteState, setNoteState] = useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const savedRef = useRef(meta.note);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    savedRef.current = meta.note;
    setNoteDraft(meta.note);
    setNoteState("idle");
    setEditingNote(false);
    // 注意：不清除 busyKey —— 整理任务属于论文本身，切走再切回仍显示「整理中…」
  }, [stem, meta.note]);

  // 手写笔记：输入停顿后自动保存
  useEffect(() => {
    if (noteDraft === savedRef.current) return;
    setNoteState("dirty");
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(async () => {
      setNoteState("saving");
      try {
        await updatePaperMeta(stem, { note: noteDraft });
        savedRef.current = noteDraft;
        onPatch({ note: noteDraft });
        setNoteState("saved");
      } catch {
        setNoteState("error");
      }
    }, 600);
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteDraft, stem]);

  const noteStateText: Record<typeof noteState, string> = {
    idle: "停顿后自动保存",
    dirty: "有修改，停顿后自动保存…",
    saving: "保存中…",
    saved: "已保存 ✓",
    error: "保存失败，请重试",
  };

  /** 整理全部摘录：已有整理笔记（如有）与新增摘录融合成一篇，完成后自动清空摘录。 */
  const digestAll = async () => {
    if (busyKey || meta.clips.length === 0) return;
    onBusy(stem, "clips");
    try {
      const prior = meta.digests.map((d) => d.text).join("\n\n---\n\n");
      const digest = await digestClips(stem, meta.clips, prior);
      onPatch({ digests: [digest], clips: [] });
    } catch (e) {
      window.alert("整理失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      onBusy(stem, null);
    }
  };

  /** 重新整理某篇已整理笔记。 */
  const redigestOne = async (d: NoteDigest) => {
    if (busyKey) return;
    onBusy(stem, d.id);
    try {
      const next = await redigest(stem, d.text);
      onPatch({
        digests: meta.digests.map((x) =>
          x.id === d.id ? { ...next, id: d.id } : x
        ),
      });
    } catch (e) {
      window.alert("重新整理失败：" + (e instanceof Error ? e.message : String(e)));
    } finally {
      onBusy(stem, null);
    }
  };

  /** 把已整理、摘录、手写笔记合并导出为一个 Markdown 文件。 */
  const exportNotes = () => {
    const now = new Date();
    const pad = (x: number) => String(x).padStart(2, "0");
    const ts = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(
      now.getHours()
    )}:${pad(now.getMinutes())}`;
    const parts: string[] = [`# 阅读笔记 · ${stem}`, "", `> 导出时间：${ts}`, ""];
    if (meta.digests.length > 0) {
      parts.push("## ✦ 已整理", "");
      for (const d of meta.digests) {
        parts.push(d.text, "", `*整理于 ${fmtTs(d.ts)}*`, "", "---", "");
      }
    }
    if (meta.clips.length > 0) {
      parts.push("## 💬 摘录", "");
      for (const c of meta.clips) {
        parts.push(`### ${fmtTs(c.ts)}`, "");
        if (c.question) parts.push(`**问**：${c.question}`, "");
        parts.push(c.answer, "", "---", "");
      }
    }
    if (meta.note.trim()) {
      parts.push("## ✎ 手写笔记", "", meta.note, "");
    }
    downloadText(`${stem}-notes.md`, parts.join("\n"));
  };

  const empty =
    meta.digests.length === 0 && meta.clips.length === 0 && !meta.note.trim();

  return (
    <div className="notes-body notes-rich">
      <div className="notes-head">
        <span className="notes-hint">
          对话里点「＋ 存入笔记」收集摘录，再整理成笔记；支持 Markdown
        </span>
        {meta.clips.length > 0 && (
          <button
            className="bt small primary"
            disabled={busyKey !== null}
            onClick={() => void digestAll()}
            title="让 AI 把已有整理笔记与全部摘录融合成一篇新笔记，完成后自动清空摘录"
          >
            {busyKey === "clips" ? "整理中…" : `✦ 整理全部摘录（${meta.clips.length}）`}
          </button>
        )}
        {!empty && (
          <button
            className="bt small"
            disabled={busyKey !== null}
            onClick={exportNotes}
            title="把已整理、摘录与手写笔记合并导出为 .md 文件"
          >
            <IconDownload /> 导出笔记
          </button>
        )}
      </div>

      {empty && (
        <div className="notes-empty card">
          <div className="chat-empty-title">还没有笔记</div>
          <div className="chat-empty-desc">
            到「对话」页向 AI 提问，点回答下方的「＋
            存入笔记」收集摘录，再回到这里一键整理。
          </div>
        </div>
      )}

      {meta.digests.length > 0 && (
        <section className="note-group">
          <h3 className="note-group-title digests">✦ 已整理 · {meta.digests.length}</h3>
          {meta.digests.map((d) => (
            <article key={d.id} className="note-card">
              <div className="note-render">
                <CiteMarkdown text={d.text} onCite={onCitePage} />
              </div>
              <footer className="note-card-foot">
                <span className="note-ts">{fmtTs(d.ts)}</span>
                <span className="note-actions">
                  <button
                    className="note-act"
                    disabled={busyKey !== null}
                    onClick={() => void redigestOne(d)}
                    title="让 AI 重新整理这一篇（更精炼、结构化）"
                  >
                    {busyKey === d.id ? "整理中…" : "↻ 重新整理"}
                  </button>
                  <button
                    className="note-act danger"
                    onClick={() => {
                      if (window.confirm("删除这篇整理笔记？")) {
                        onPatch({ digests: meta.digests.filter((x) => x.id !== d.id) });
                      }
                    }}
                  >
                    删除
                  </button>
                </span>
              </footer>
            </article>
          ))}
        </section>
      )}

      {meta.clips.length > 0 && (
        <section className="note-group">
          <h3 className="note-group-title clips">💬 追加的摘录 · {meta.clips.length}</h3>
          {meta.clips.map((c) => (
            <article key={c.id} className="note-card clip">
              {c.question && <div className="clip-q">问：{c.question}</div>}
              <div className="note-render clip-a">
                <CiteMarkdown text={c.answer} onCite={onCitePage} />
              </div>
              <footer className="note-card-foot">
                <span className="note-ts">{fmtTs(c.ts)}</span>
                <span className="note-actions">
                  <button
                    className="note-act danger"
                    onClick={() => onPatch({ clips: meta.clips.filter((x) => x.id !== c.id) })}
                  >
                    移除
                  </button>
                </span>
              </footer>
            </article>
          ))}
        </section>
      )}

      {!empty && !meta.note.trim() && !editingNote && (
        <section className="note-group">
          <h3 className="note-group-title manual">✎ 手写笔记</h3>
          <div className="manual-card manual-ghost">
            <button className="note-act" onClick={() => setEditingNote(true)}>
              ＋ 写点自己的想法（支持 Markdown，自动保存）
            </button>
          </div>
        </section>
      )}

      {(meta.note.trim() || editingNote) && (
        <section className="note-group">
          <h3 className="note-group-title manual">✎ 手写笔记</h3>
          <div className="manual-card">
            {editingNote ? (
              <>
                <textarea
                  className="notes-input"
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  placeholder="Markdown 语法可用"
                  autoFocus
                />
                <div className="manual-foot">
                  <span className={`notes-state ${noteState}`}>{noteStateText[noteState]}</span>
                  <button
                    className="bt small"
                    onClick={() => {
                      if (noteDraft !== savedRef.current) {
                        void updatePaperMeta(stem, { note: noteDraft }).then(() => {
                          savedRef.current = noteDraft;
                          onPatch({ note: noteDraft });
                        });
                      }
                      setEditingNote(false);
                    }}
                  >
                    完成
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="note-render">
                  <CiteMarkdown text={meta.note} onCite={onCitePage} />
                </div>
                <footer className="note-card-foot">
                  <span className={`notes-state ${noteState}`}>{noteStateText[noteState]}</span>
                  <button className="note-act" onClick={() => setEditingNote(true)}>
                    ✎ 编辑
                  </button>
                </footer>
              </>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
