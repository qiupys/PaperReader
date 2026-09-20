/** 页码引用：把回答/摘要里的 [p.N] 渲染为可点击链接，点击跳转 PDF 对应页。 */
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 把纯文本 [p.N] 转成 markdown 链接 [p.N](#cite-N)。 */
export function linkifyCitations(text: string): string {
  return text.replace(/\[p\.(\d{1,3})\]/g, "[p.$1](#cite-$1)");
}

interface CiteMarkdownProps {
  text: string;
  onCite?: (page: number) => void;
}

/** 带页码引用跳转的 Markdown 渲染（对话 / Summary 章节 / 笔记共用）。 */
export function CiteMarkdown({ text, onCite }: CiteMarkdownProps) {
  const body = onCite ? linkifyCitations(text) : text;
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: (props: {
          href?: string;
          children?: ReactNode;
        }) => {
          const { href, children } = props;
          if (href && href.startsWith("#cite-") && onCite) {
            const page = parseInt(href.slice(6), 10);
            return (
              <a
                className="cite-link"
                title={`跳转到 PDF 第 ${page} 页`}
                onClick={(e) => {
                  e.preventDefault();
                  onCite(page);
                }}
              >
                {children}
              </a>
            );
          }
          return (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          );
        },
      }}
    >
      {body}
    </ReactMarkdown>
  );
}
