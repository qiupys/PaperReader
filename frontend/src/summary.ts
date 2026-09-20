/** Summary 渲染辅助：剥离思考过程、拆分章节卡片。 */

/** 前端兜底：删除「原始推理过程」整段（后端已剥离，兼容历史数据）。 */
export function stripReasoning(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let skipping = false;
  for (const line of lines) {
    if (/^#{1,6}\s*原始推理过程\s*$/.test(line.trim())) {
      skipping = true;
      continue;
    }
    if (skipping && /^#{1,6}\s/.test(line)) {
      skipping = false;
    }
    if (!skipping) out.push(line);
  }
  return out.join("\n").replace(/\n+$/, "\n");
}

export interface Section {
  heading: string;
  body: string;
  color: string;
  border: string;
  bg: string;
}

interface Palette {
  color: string;
  border: string;
  bg: string;
}

const SECTION_COLORS: Array<{ match: RegExp; palette: Palette }> = [
  { match: /摘要/, palette: { color: "#3b6ef5", border: "#bcd0fb", bg: "#f3f7fe" } },
  { match: /核心技术|技术方案|方法/, palette: { color: "#7048e8", border: "#ddd0f8", bg: "#faf8ff" } },
  { match: /实验|效果/, palette: { color: "#0c8599", border: "#b8e3ea", bg: "#f2fafc" } },
  { match: /结论/, palette: { color: "#d9782a", border: "#f5d4b5", bg: "#fef8f1" } },
  { match: /Agent|思考|评价/, palette: { color: "#d9508f", border: "#f4c6dd", bg: "#fef3f8" } },
];

const FALLBACK_PALETTE: Palette = {
  color: "#495e6f",
  border: "#d7dfe6",
  bg: "#fafbfc",
};

function paletteFor(heading: string): Palette {
  for (const { match, palette } of SECTION_COLORS) {
    if (match.test(heading)) return palette;
  }
  return FALLBACK_PALETTE;
}

export interface MetaPair {
  label: string;
  value: string;
}

export interface ParsedSummary {
  title: string;
  metaPairs: MetaPair[];
  sections: Section[];
}

/** 去掉 YAML frontmatter。 */
function removeFrontmatter(md: string): string {
  const m = md.match(/^\s*---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return m ? md.slice(m[0].length) : md;
}

/** 把 `**label**：value` 引用行解析为键值对。 */
function parseMetaPair(line: string): MetaPair | null {
  const m = line.match(/^\*\*(.+?)\*\*\s*[:：]\s*(.+)$/);
  if (!m) return null;
  const label = m[1].trim();
  const value = m[2].trim().replace(/\s+$/, "");
  if (!label || !value) return null;
  return { label, value };
}

/** 把 markdown 拆为标题 + 引用元信息 + 彩色章节卡片。 */
export function parseSummary(rawMd: string): ParsedSummary {
  const md = stripReasoning(rawMd);
  const lines = removeFrontmatter(md).split("\n");

  let title = "";
  const metaPairs: MetaPair[] = [];
  const collected: Array<{ heading: string; body: string[] }> = [];
  let acc: { heading: string; body: string[] } | null = null;

  for (const line of lines) {
    const h1 = line.match(/^#\s+(.+?)\s*$/);
    const h2 = line.match(/^##\s+(.+?)\s*$/);
    const h3 = h2 ? null : line.match(/^###\s+(.+?)\s*$/);
    const heading = h2 ? h2[1].trim() : h3 ? h3[1].trim() : null;

    if (heading) {
      if (acc) collected.push(acc);
      acc = { heading, body: [] };
      continue;
    }
    if (acc) {
      acc.body.push(line);
      continue;
    }
    // 顶层标题 / 元信息（第一个 ### 之前）
    if (!title && h1) {
      title = h1[1].trim();
    } else if (/^>\s?/.test(line.trim())) {
      const pair = parseMetaPair(line.trim().replace(/^>\s?/, ""));
      if (pair) metaPairs.push(pair);
    }
  }
  if (acc) collected.push(acc);

  const sections: Section[] = [];
  for (const sec of collected) {
    const text = sec.body.join("\n").replace(/^\n+|\n+$/g, "");
    if (!text) continue;
    sections.push({ heading: sec.heading, body: text, ...paletteFor(sec.heading) });
  }

  return { title, metaPairs, sections };
}

export function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 把文本作为 .md 文件触发浏览器下载。 */
export function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
