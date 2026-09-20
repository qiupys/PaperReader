/** 通用 Markdown 渲染（对话 / Summary 章节 / 笔记共用）。 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: (props: { href?: string; children?: React.ReactNode }) => (
          <a href={props.href} target="_blank" rel="noreferrer">
            {props.children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
