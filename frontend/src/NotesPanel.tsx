import { useEffect, useState } from "react";
import { digestClips, redigest } from "./api";
import { Markdown } from "./Markdown";
import { IconDownload } from "./icons";
import { downloadText } from "./summary";
import type { ManualNote, NoteDigest, PaperMeta } from "./types";

function fmtTs(ts: string): string {
  try {
    return ts.replace("T", " ").slice(0, 16);
  } catch {
    return ts;
  }
}

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/**
 * 手写笔记草稿的「暂存」是后台行为：输入即静默写入 localStorage，不打扰编辑，
 * 切论文/刷新都不丢；点「保存」才生成卡片并清除草稿。
 * key 不带 id = 新建草稿；带 id = 某张卡片的编辑草稿。
 */
const draftKey = (stem: string, id?: string) =>
  id ? `paperreader-notedraft:${stem}:${id}` : `paperreader-notedraft:${stem}`;

function readDraft(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeDraft(key: string, text: string | null): void {
  try {
    if (text && text.trim()) localStorage.setItem(key, text);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** 旧版单条手写笔记（meta.note）在卡片列表中的占位 id。 */
const LEGACY_ID = "__legacy__";

type Patch = Partial<Pick<PaperMeta, "note" | "notes" | "clips" | "digests">>;

interface NotesPanelProps {
  stem: string;
  meta: PaperMeta;
  /** 本篇论文的整理任务标识："clips" | digest.id；null 表示空闲。由 App 按论文跟踪。 */
  busyKey: string | null;
  onBusy: (stem: string, key: string | null) => void;
  onPatch: (patch: Patch) => void;
}

export default function NotesPanel({
  stem,
  meta,
  busyKey,
  onBusy,
  onPatch,
}: NotesPanelProps) {
  const [composing, setComposing] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  useEffect(() => {
    setComposing(false);
    setEditingId(null);
    setEditDraft("");
    setNoteDraft("");
    // 注意：不清除 busyKey —— 整理任务属于论文本身，切走再切回仍显示「整理中…」
  }, [stem]);

  /** 卡片列表 = 新版 notes + 旧版单条 note（兼容展示，首次编辑/整理时自然迁移）。 */
  const cards: ManualNote[] = [
    ...(meta.note.trim() ? [{ id: LEGACY_ID, ts: "", text: meta.note.trim() }] : []),
    ...(meta.notes ?? []),
  ];
  const hasStagedDraft = !!readDraft(draftKey(stem))?.trim();

  /** 整理全部：已有整理笔记、手写笔记卡片与新增摘录融合成一篇，完成后自动清空摘录与手写笔记卡片。 */
  const canDigest =
    meta.clips.length > 0 || cards.some((c) => c.text.trim()) || !!(meta.notes ?? []).length;
  const digestAll = async () => {
    if (busyKey || !canDigest) return;
    onBusy(stem, "clips");
    try {
      const priorParts = meta.digests.map((d) => d.text);
      if (meta.note.trim()) priorParts.push(meta.note.trim());
      for (const c of meta.notes ?? []) {
        if (c.text.trim()) priorParts.push(c.text);
      }
      const digest = await digestClips(
        stem,
        meta.clips,
        priorParts.join("\n\n---\n\n")
      );
      // 手写卡片内容已被融合进整理笔记，一并清空（legacy note 同步迁移清零）
      onPatch({ digests: [digest], clips: [], notes: [], note: "" });
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

  /* ---------- 手写笔记：新建（后台自动暂存草稿） ---------- */

  const startCompose = () => {
    setNoteDraft(readDraft(draftKey(stem)) ?? "");
    setComposing(true);
  };

  const changeCompose = (text: string) => {
    setNoteDraft(text);
    writeDraft(draftKey(stem), text); // 后台暂存，不打扰编辑
  };

  const cancelCompose = () => {
    writeDraft(draftKey(stem), null);
    setNoteDraft("");
    setComposing(false);
  };

  const saveCompose = () => {
    const text = noteDraft.trim();
    if (!text) return;
    const card: ManualNote = { id: newId(), ts: new Date().toISOString(), text };
    onPatch({ notes: [...(meta.notes ?? []), card] });
    writeDraft(draftKey(stem), null);
    setNoteDraft("");
    setComposing(false);
  };

  /* ---------- 手写笔记：编辑 / 删除已有卡片 ---------- */

  const startEdit = (card: ManualNote) => {
    setEditDraft(readDraft(draftKey(stem, card.id)) ?? card.text);
    setEditingId(card.id);
  };

  const changeEdit = (text: string) => {
    setEditDraft(text);
    if (editingId) writeDraft(draftKey(stem, editingId), text); // 后台暂存
  };

  const cancelEdit = () => {
    if (editingId) writeDraft(draftKey(stem, editingId), null);
    setEditingId(null);
    setEditDraft("");
  };

  const saveEdit = (card: ManualNote) => {
    const text = editDraft.trim();
    if (!text) return;
    if (card.id === LEGACY_ID) {
      // 旧版单条笔记 → 顺手迁移为卡片，并清空 note 字段
      const migrated: ManualNote = { id: newId(), ts: new Date().toISOString(), text };
      onPatch({ notes: [...(meta.notes ?? []), migrated], note: "" });
    } else {
      onPatch({
        notes: (meta.notes ?? []).map((c) =>
          c.id === card.id ? { ...c, text } : c
        ),
      });
    }
    writeDraft(draftKey(stem, card.id), null);
    setEditingId(null);
    setEditDraft("");
  };

  const deleteCard = (card: ManualNote) => {
    const label = card.text.length > 24 ? `${card.text.slice(0, 24)}…` : card.text;
    if (!window.confirm(`删除这条手写笔记？\n\n${label}`)) return;
    writeDraft(draftKey(stem, card.id), null);
    if (card.id === LEGACY_ID) {
      onPatch({ note: "" });
    } else {
      onPatch({ notes: (meta.notes ?? []).filter((c) => c.id !== card.id) });
    }
  };

  /* ---------- 导出 ---------- */

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
    if (cards.length > 0) {
      parts.push("## ✎ 手写笔记", "");
      for (const c of cards) {
        if (c.ts) parts.push(`### ${fmtTs(c.ts)}`);
        parts.push(c.text, "", "---", "");
      }
    }
    downloadText(`${stem}-notes.md`, parts.join("\n"));
  };

  const empty =
    meta.digests.length === 0 && meta.clips.length === 0 && cards.length === 0;
  const statusHint = (text: string) =>
    text.trim() ? "草稿已自动暂存，点「保存」后生效" : "";

  return (
    <div className="notes-body notes-rich">
      <div className="notes-head">
        <span className="notes-hint">
          摘录与手写想法一键融合整理，章节自动注明来源；支持 Markdown
        </span>
        {canDigest && (
          <button
            className="bt small primary"
            disabled={busyKey !== null}
            onClick={() => void digestAll()}
            title="让 AI 把已有整理笔记、手写笔记与全部摘录融合成一篇新笔记，完成后自动清空摘录与手写笔记卡片"
          >
            {busyKey === "clips"
              ? "整理中…"
              : meta.clips.length > 0
                ? `✦ 整理全部摘录（${meta.clips.length}）`
                : "✦ 整理手写笔记"}
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

      {empty && !composing && !hasStagedDraft && (
        <div className="notes-empty card">
          <div className="chat-empty-title">还没有笔记</div>
          <div className="chat-empty-desc">
            到「对话」页提问，点回答下方的「＋ 存入笔记」收集摘录；也可在这里点「＋
            写点自己的想法」手写卡片。之后一键整理，AI 会融合两部分内容，并在章节注明来源。
          </div>
        </div>
      )}

      {meta.digests.length > 0 && (
        <section className="note-group">
          <h3 className="note-group-title digests">✦ 已整理 · {meta.digests.length}</h3>
          {meta.digests.map((d) => (
            <article key={d.id} className="note-card">
              <div className="note-render">
                <Markdown text={d.text} />
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
                <Markdown text={c.answer} />
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

      {/* 手写笔记：常驻入口 —— 无论有无摘录/整理，随时都能写；删光卡片后入口依然在 */}
      <section className="note-group">
        <div className="note-group-head">
          <h3 className="note-group-title manual">
            ✎ 手写笔记{cards.length > 0 ? ` · ${cards.length}` : ""}
          </h3>
          {!composing && cards.length > 0 && (
            <button className="note-act" onClick={startCompose}>
              ＋ 写点自己的想法
            </button>
          )}
        </div>

          {composing && (
            <div className="manual-card">
              <textarea
                className="notes-input"
                value={noteDraft}
                onChange={(e) => changeCompose(e.target.value)}
                placeholder="Markdown 语法可用；草稿会自动暂存，点「保存」生成一张笔记卡片"
                autoFocus
              />
              <div className="manual-foot">
                <span className="notes-state">{statusHint(noteDraft)}</span>
                <span className="note-actions">
                  <button className="bt small" onClick={cancelCompose}>
                    取消
                  </button>
                  <button
                    className="bt small primary"
                    disabled={!noteDraft.trim()}
                    onClick={saveCompose}
                  >
                    保存
                  </button>
                </span>
              </div>
            </div>
          )}

          {!composing && cards.length === 0 && (
            <div className="manual-card manual-ghost">
              <button className="note-act" onClick={startCompose}>
                {hasStagedDraft
                  ? "✎ 继续写暂存的草稿（尚未保存）"
                  : "＋ 写点自己的想法（支持 Markdown，草稿自动暂存）"}
              </button>
            </div>
          )}

          {cards.map((c) =>
            editingId === c.id ? (
              <article key={c.id} className="manual-card">
                <textarea
                  className="notes-input"
                  value={editDraft}
                  onChange={(e) => changeEdit(e.target.value)}
                  autoFocus
                />
                <div className="manual-foot">
                  <span className="notes-state">{statusHint(editDraft)}</span>
                  <span className="note-actions">
                    <button className="bt small" onClick={cancelEdit}>
                      取消
                    </button>
                    <button
                      className="bt small primary"
                      disabled={!editDraft.trim()}
                      onClick={() => saveEdit(c)}
                    >
                      保存
                    </button>
                  </span>
                </div>
              </article>
            ) : (
              <article key={c.id} className="note-card manual">
                <div className="note-render">
                  <Markdown text={c.text} />
                </div>
                <footer className="note-card-foot">
                  <span className="note-ts">{c.ts ? fmtTs(c.ts) : "早期笔记"}</span>
                  <span className="note-actions">
                    <button className="note-act" onClick={() => startEdit(c)}>
                      ✎ 编辑
                    </button>
                    <button className="note-act danger" onClick={() => deleteCard(c)}>
                      删除
                    </button>
                  </span>
                </footer>
              </article>
            )
          )}
      </section>
    </div>
  );
}
