import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'
import './markdown-prose.css'

// NOTE(claude): NO rehype-raw here, deliberately — transcripts are untrusted content. Without
// it, remark-rehype drops raw `html` nodes entirely (mdast-util-to-hast's default), so a
// `<script>` (or any raw tag) in a message can never mount. Tested in MessageTurn.test.tsx.
//
// NOTE(claude): unknown fence languages (```jsonl, ```mermaid, typos) are safe with
// rehype-highlight@7.0.2 as installed: verified in node_modules/rehype-highlight/lib/index.js
// that a lowlight "Unknown language" error is caught internally and downgraded to a vfile
// warning, leaving the block un-highlighted — it does not throw, and react-markdown ignores
// vfile messages. The old `ignoreMissing` option was removed in v6 precisely because this
// tolerance became the default; no extra configuration is needed (or possible). `detect` stays
// off so unfenced tool output isn't noisily mis-colored. Pinned by the ```notalang test.
export interface MarkdownProseProps {
  markdown: string
}

export function MarkdownProse({ markdown }: MarkdownProseProps) {
  return (
    <div className="prose markdown-prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
        {markdown}
      </ReactMarkdown>
    </div>
  )
}
