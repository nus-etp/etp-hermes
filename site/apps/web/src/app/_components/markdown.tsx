import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { cx } from '~/lib/cn'

interface Props {
  children: string
  className?: string
}

const components: Components = {
  h1: (props) => (
    <h1
      {...props}
      className="text-base-content-default mt-2 mb-4 text-3xl font-semibold tracking-tight"
    />
  ),
  h2: (props) => (
    <h2
      {...props}
      className="text-base-content-default mt-10 mb-3 text-2xl font-semibold tracking-tight"
    />
  ),
  h3: (props) => (
    <h3
      {...props}
      className="border-base-divider-subtle text-base-content-default mt-8 mb-2 border-b pb-1 text-lg font-semibold tracking-tight"
    />
  ),
  p: (props) => (
    <p {...props} className="text-base-content-medium my-3 leading-relaxed" />
  ),
  ul: (props) => (
    <ul {...props} className="my-3 list-disc space-y-1.5 pl-5" />
  ),
  ol: (props) => (
    <ol {...props} className="my-3 list-decimal space-y-1.5 pl-5" />
  ),
  li: (props) => (
    <li
      {...props}
      className="text-base-content-medium pl-1 leading-relaxed marker:text-slate-400"
    />
  ),
  a: (props: ComponentPropsWithoutRef<'a'>) => (
    <a
      {...props}
      target={props.href?.startsWith('http') ? '_blank' : undefined}
      rel={props.href?.startsWith('http') ? 'noreferrer noopener' : undefined}
      className="break-words text-indigo-600 underline-offset-2 hover:underline"
    />
  ),
  strong: (props) => (
    <strong {...props} className="font-semibold text-slate-900" />
  ),
  em: (props) => <em {...props} className="text-base-content-medium italic" />,
  code: (props) => (
    <code
      {...props}
      className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.85em] text-slate-800"
    />
  ),
  hr: () => <hr className="border-base-divider-subtle my-6" />,
  blockquote: (props) => (
    <blockquote
      {...props}
      className="border-base-divider-default text-base-content-medium my-4 border-l-4 pl-4 italic"
    />
  ),
  table: (props) => (
    <div className="ring-base-divider-subtle my-4 overflow-x-auto rounded-lg ring-1">
      <table {...props} className="min-w-full text-sm" />
    </div>
  ),
  thead: (props) => (
    <thead
      {...props}
      className="text-base-content-medium bg-slate-50 text-left text-xs uppercase"
    />
  ),
  th: (props) => <th {...props} className="px-3 py-2 font-semibold" />,
  td: (props) => <td {...props} className="border-base-divider-subtle border-t px-3 py-2" />,
  img: (props) => (
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    <img
      {...props}
      className="border-base-divider-subtle my-6 w-full rounded-xl border shadow-sm"
    />
  ),
}

export function Markdown({ children, className }: Props) {
  return (
    <div className={cx('text-sm', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}
