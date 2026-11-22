import { Link } from "@langchain/macaw-components/Link"
import { Text } from "@langchain/macaw-components/Text"
import { usePluginHost } from "@openswe/plugin-sdk/react"
import { useId } from "react"
import Markdown from "react-markdown"
import rehypeSanitize from "rehype-sanitize"
import remarkGfm from "remark-gfm"

export function MarkdownDescription({ body }: { body?: string | null }) {
  const host = usePluginHost()
  const idPrefix = `description-${useId()}-`
  return (
    <div className="min-w-0 rounded-lg border border-default bg-surface-level-1 p-space-4 text-sm text-primary [&_blockquote]:border-l-2 [&_blockquote]:border-default [&_blockquote]:pl-space-3 [&_blockquote]:text-secondary [&_code]:font-mono [&_code]:break-words [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_li+li]:mt-space-1 [&_ol]:list-decimal [&_ol]:pl-space-5 [&_p]:break-words [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-surface-level-1 [&_pre]:p-space-3 [&_ul]:list-disc [&_ul]:pl-space-5 [&>*+*]:mt-space-3">
      <Markdown
        skipHtml
        rehypePlugins={[[rehypeSanitize, { clobberPrefix: idPrefix }]]}
        remarkPlugins={[remarkGfm]}
        urlTransform={(url, _key, node) => {
          if (
            [...url].some(
              (char) => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 92
            )
          )
            return ""
          if (
            url.startsWith("#") &&
            (node.properties.dataFootnoteRef !== undefined ||
              node.properties.dataFootnoteBackref !== undefined)
          )
            return `#${idPrefix}${url.slice(1)}`
          const href =
            url.startsWith("/") && !url.startsWith("//")
              ? new URL(url, "https://github.com").href
              : url
          return /^https?:\/\//i.test(href) ? href : ""
        }}
        components={{
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left [&_td]:border [&_td]:border-default [&_td]:p-space-2 [&_th]:border [&_th]:border-default [&_th]:p-space-2 [&_th]:font-semibold">
                {children}
              </table>
            </div>
          ),
          a: ({
            href,
            id,
            children,
            "aria-label": ariaLabel,
            "aria-describedby": describedBy,
          }) =>
            href ? (
              <Link
                href={href}
                id={id}
                aria-label={ariaLabel}
                aria-describedby={describedBy}
                variant="sm"
                onClick={(event) => {
                  if (href.startsWith("#")) return
                  event.preventDefault()
                  void host.nav.openExternal(href)
                }}
              >
                {children}
              </Link>
            ) : (
              <span>{children}</span>
            ),
          img: ({ alt }) => (
            <Text as="span" variant="sm" color="secondary">
              {alt ? `[Image: ${alt}]` : "[Image omitted]"}
            </Text>
          ),
        }}
      >
        {body?.trim() || "No description provided."}
      </Markdown>
    </div>
  )
}
