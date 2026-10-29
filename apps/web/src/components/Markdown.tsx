import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeSanitize];
// Compact labels must stay phrasing content (and may live inside a button/link).
const inlineElements = ["strong", "em", "del", "code", "br"];

function CopyCode({ value }: { value: string }) {
  const [status, setStatus] = useState("Copy");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <button type="button" className="small ghost" aria-label="Copy code" onClick={() => {
      if (!navigator.clipboard) { setStatus("Unavailable"); return; }
      void navigator.clipboard.writeText(value).then(() => {
        setStatus("Copied");
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setStatus("Copy"), 1200);
      }, () => setStatus("Copy failed"));
    }}>{status}</button>
  );
}

// Keep renderer identities stable: extending a streamed fence updates its existing DOM,
// without remounting the code block or losing selection, scroll or copy-button state.
const components: Components = {
  a: ({ children, href, title }) => href
    ? <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
  // Remote images in untrusted output can track readers and shift the transcript on load.
  img: ({ alt }) => <span className="muted">{alt ? `[Image: ${alt}]` : "[Image]"}</span>,
  table: ({ children }) => (
    <div className="markdown-table" role="region" aria-label="Markdown table" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  pre: ({ children, node }) => {
    const code = node?.children.find((child) => child.type === "element" && child.tagName === "code");
    const value = code?.type === "element" ? code.children.map((child) => child.type === "text" ? child.value : "").join("") : "";
    return (
      <div className="markdown-code">
        <div className="markdown-code-head"><span>Code</span><CopyCode value={value} /></div>
        <pre>{children}</pre>
      </div>
    );
  },
};

/** Shared, synchronous Markdown for untrusted prose. No raw HTML parser or injection.
 * CommonMark accepts unfinished fences/emphasis while streaming; memoization avoids
 * parsing unchanged messages when later events arrive. `inline` is for compact labels.
 */
export const Markdown = memo(function Markdown({ children, className = "", inline = false }: {
  children: string | null | undefined; className?: string; inline?: boolean;
}) {
  const Tag = inline ? "span" : "div";
  return (
    <Tag className={`markdown${inline ? " markdown-inline" : ""}${className ? ` ${className}` : ""}`}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={components}
                     skipHtml allowedElements={inline ? inlineElements : undefined} unwrapDisallowed>
        {children ?? ""}
      </ReactMarkdown>
    </Tag>
  );
});
