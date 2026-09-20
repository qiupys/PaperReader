import { useEffect, useRef, useState } from "react";
import { relatedQuestions, streamChat } from "./api";
import { Markdown } from "./Markdown";
import type { ChatTurn } from "./types";

const SUGGESTIONS = [
  "这篇论文要解决的核心问题是什么？",
  "用通俗的语言解释核心技术方案",
  "这个方法的局限性有哪些？",
];

function chatKey(stem: string): string {
  return `paperreader-chat:${stem}`;
}

export function loadChat(stem: string): ChatTurn[] {
  try {
    const raw = localStorage.getItem(chatKey(stem));
    if (!raw) return [];
    const data = JSON.parse(raw) as ChatTurn[];
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function saveChat(stem: string, messages: ChatTurn[]): void {
  try {
    localStorage.setItem(chatKey(stem), JSON.stringify(messages.slice(-100)));
  } catch {
    /* localStorage 不可用时静默降级 */
  }
}

/** 找到某条 assistant 回复前最近的用户提问。 */
function precedingQuestion(messages: ChatTurn[], index: number): string {
  for (let i = index - 1; i >= 0; i--) {
    if (messages[i].role === "user" && !messages[i].error) return messages[i].content;
  }
  return "";
}

interface ChatPanelProps {
  stem: string;
  hasSummary: boolean;
  /** 本篇论文是否有进行中的 LLM 请求（由 App 按论文跟踪，切页不丢）。 */
  pending: boolean;
  /** 上报请求开始/结束；App 据此在侧栏显示「后台回答中」指示。 */
  onPending: (stem: string, on: boolean) => void;
  /** 把一条问答作为摘录存入论文笔记。 */
  onSaveClip: (question: string, answer: string) => void;
  /** 论文未读时先自动生成 Summary 再回答（由 App 提供，含并发防护）。 */
  onEnsureSummary: (stem: string) => Promise<void>;
}

export default function ChatPanel({
  stem,
  hasSummary,
  pending,
  onPending,
  onSaveClip,
  onEnsureSummary,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatTurn[]>(() => loadChat(stem));
  const [input, setInput] = useState("");
  const [savedIdx, setSavedIdx] = useState<number | null>(null);
  /** 先摘要后回答：正在为未读论文生成 Summary。 */
  const [summarizing, setSummarizing] = useState(false);
  /** 最近一轮回答的关联追问（点击即发送）。 */
  const [followups, setFollowups] = useState<string[]>([]);
  /** 关联追问生成中：回答结束时立即占位，生成后平滑替换，避免突兀。 */
  const [followupsLoading, setFollowupsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const stemRef = useRef(stem);
  const busy = pending; // 本篇论文是否有进行中的请求

  useEffect(() => {
    stemRef.current = stem;
    setMessages(loadChat(stem));
    setInput("");
    setSavedIdx(null);
    setFollowups([]);
    setFollowupsLoading(false);
    setSummarizing(false);
    // 注意：这里刻意不清除进行中请求 —— 请求属于论文本身，
    // 切走再切回，思考框与提问都还在；完成后结果自动落到对话里
  }, [stem]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  /** 流式发送：回答逐段到达，实时渲染；期间切论文/页签状态不丢。 */
  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy || summarizing) return;
    const myStem = stem;

    // 先摘要后回答：论文未读时先生成 Summary（几分钟），完成后自动继续回答
    if (!hasSummary) {
      setSummarizing(true);
      setFollowups([]);
      try {
        await onEnsureSummary(myStem);
      } catch (e) {
        const detail = e instanceof Error ? e.message : String(e);
        const failed: ChatTurn[] = [
          ...messages,
          { role: "user", content: message },
          {
            role: "assistant",
            content: `Summary 自动生成失败：${detail}\n\n可稍后在「对照阅读」页点工具栏的生成按钮重试，或直接重新发送这个问题。`,
            error: true,
          },
        ];
        saveChat(myStem, failed);
        if (stemRef.current === myStem) setMessages(failed);
        setSummarizing(false);
        return;
      }
      setSummarizing(false);
    }

    const withQ: ChatTurn[] = [...messages, { role: "user", content: message }];
    const withA: ChatTurn[] = [...withQ, { role: "assistant", content: "" }];
    // 先摘要在生成期间切走了论文：只落存储不覆盖当前视图（回答后台照常进行）
    if (stemRef.current === myStem) setMessages(withA);
    // 立即持久化：等回答期间切换页签/刷新，提问也不会丢
    saveChat(myStem, withA);
    onPending(myStem, true);
    setInput("");
    setFollowups([]);
    setFollowupsLoading(false);

    let acc = "";
    let lastSave = Date.now();
    const flush = (content: string) => {
      if (stemRef.current !== myStem) return; // 期间切换了论文：不覆盖当前视图
      setMessages((prev) => {
        if (prev.length === 0) return prev;
        const next = prev.slice();
        next[next.length - 1] = { role: "assistant", content };
        return next;
      });
    };

    try {
      await streamChat(myStem, message, messages, (t) => {
        acc += t;
        flush(acc);
        const now = Date.now();
        if (now - lastSave > 1200) {
          lastSave = now;
          saveChat(myStem, [...withQ, { role: "assistant", content: acc }]);
        }
      });
      if (!acc.trim()) throw new Error("模型返回内容为空，请重试");
      const next: ChatTurn[] = [...withQ, { role: "assistant", content: acc.trim() }];
      saveChat(myStem, next);
      if (stemRef.current === myStem) setMessages(next);
      // 关联追问：回答结束时立即占位，生成完毕后与回答区域平滑衔接
      setFollowupsLoading(true);
      void relatedQuestions(myStem, message, acc.trim()).then((qs) => {
        setFollowupsLoading(false);
        if (qs.length && stemRef.current === myStem) setFollowups(qs);
      });
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      const next: ChatTurn[] = acc.trim()
        ? // 已有部分内容：保留并附加中断提示（不打断 Markdown 渲染）
          [...withQ, { role: "assistant", content: `${acc.trim()}\n\n> ⚠️ 生成中断：${detail}` }]
        : [...withQ, { role: "assistant", content: `对话失败：${detail}`, error: true }];
      saveChat(myStem, next);
      if (stemRef.current === myStem) setMessages(next);
    } finally {
      onPending(myStem, false);
      if (stemRef.current === myStem) textareaRef.current?.focus();
    }
  };

  const clear = () => {
    if (messages.length === 0 || window.confirm("清空与这篇论文的对话记录？")) {
      setMessages([]);
      saveChat(stem, []);
    }
  };

  /** 把某条回答（连同提问）作为摘录存入笔记。 */
  const saveTurnToNote = (index: number) => {
    const turn = messages[index];
    if (!turn || turn.role !== "assistant" || turn.error || !turn.content.trim()) return;
    onSaveClip(precedingQuestion(messages, index), turn.content.trim());
    setSavedIdx(index);
    window.setTimeout(() => setSavedIdx(null), 1800);
  };

  const empty = messages.length === 0;

  return (
    <div className="chat-panel">
      <div className="chat-toolbar">
        <span className="chat-hint">
          基于论文全文与 Summary 的多轮问答 · 回答实时流式显示 · 回答后附「继续追问」建议 ·
          记录保存在本机浏览器
        </span>
        {!empty && (
          <button className="bt small" onClick={clear} disabled={busy || summarizing}>
            清空对话
          </button>
        )}
      </div>

      {summarizing && (
        <div className="chat-status-banner">
          <span className="spinner" />
          这篇论文还没有 Summary：正在先自动生成（思考型模型，需要几分钟），完成后自动回答你的问题。
        </div>
      )}

      <div className="chat-scroll" ref={scrollRef}>
        {empty ? (
          <div className="chat-empty">
            <div className="chat-empty-title">和 AI 聊聊这篇论文</div>
            <div className="chat-empty-desc">
              {hasSummary
                ? "AI 已读过这篇论文，可以就任何细节继续追问；有价值的回答可「＋ 存入笔记」。"
                : "这篇论文还没有 Summary：直接提问即可，AI 会先生成 Summary 再回答（需几分钟）。"}
            </div>
            <div className="chat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="chat-chip"
                  disabled={busy || summarizing}
                  onClick={() => void send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((m, i) => {
            // 流式进行中：最后一条尚未有内容的 assistant 消息显示等待圆点
            const isStreamingPlaceholder =
              m.role === "assistant" && !m.error && busy && i === messages.length - 1 && !m.content;
            return (
              <div key={i} className={`chat-msg ${m.role}${m.error ? " error" : ""}`}>
                <div className="chat-avatar">{m.role === "user" ? "我" : "AI"}</div>
                <div className="chat-bubble">
                  {m.role === "assistant" && !m.error ? (
                    m.content ? (
                      <>
                        <Markdown text={m.content} />
                        <button
                          className={`save-note-btn${savedIdx === i ? " done" : ""}`}
                          onClick={() => saveTurnToNote(i)}
                          title="把这条回答（连同提问）追加到笔记的摘录区"
                        >
                          {savedIdx === i ? "已存入笔记 ✓" : "＋ 存入笔记"}
                        </button>
                      </>
                    ) : isStreamingPlaceholder ? (
                      <span className="dots">
                        <i />
                        <i />
                        <i />
                      </span>
                    ) : null
                  ) : (
                    m.content
                  )}
                </div>
              </div>
            );
          })}
            {followupsLoading && (
              <div className="chat-followups">
                <span className="chat-followups-label chat-followups-pending">
                  <span className="spinner" /> 正在生成关联问题…
                </span>
              </div>
            )}
            {followups.length > 0 && (
              <div className="chat-followups">
                <span className="chat-followups-label">继续追问：</span>
                {followups.map((q) => (
                  <button
                    key={q}
                    className="chat-chip"
                    disabled={busy || summarizing}
                    onClick={() => void send(q)}
                    title={q}
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <div className="chat-input-row">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          value={input}
          rows={2}
          disabled={busy || summarizing}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send(input);
            }
          }}
        />
        <button
          className="bt primary chat-send"
          disabled={busy || summarizing || !input.trim()}
          onClick={() => void send(input)}
        >
          发送
        </button>
      </div>
    </div>
  );
}
