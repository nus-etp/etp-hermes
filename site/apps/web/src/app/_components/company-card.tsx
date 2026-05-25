import Link from 'next/link'
import { BiBookContent, BiLinkExternal } from 'react-icons/bi'

import { cx } from '~/lib/cn'
import type { Company } from '~/lib/data'

interface Props {
  company: Company
  query?: string
}

function highlight(text: string, query?: string) {
  if (!query) return text
  const q = query.trim()
  if (q.length < 2) return text
  const idx = text.toLowerCase().indexOf(q.toLowerCase())
  if (idx < 0) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded bg-yellow-200/70 px-0.5">
        {text.slice(idx, idx + q.length)}
      </mark>
      {text.slice(idx + q.length)}
    </>
  )
}

export function CompanyCard({ company, query }: Props) {
  const detailHref = `/c/${company.slug}/`
  return (
    <article className="group bg-base-canvas-default ring-base-divider-subtle hover:ring-base-divider-default flex flex-col rounded-xl p-5 shadow-sm ring-1 transition hover:-translate-y-0.5 hover:shadow-md">
      <Link href={detailHref} className="flex flex-col gap-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="prose-headline-base-semibold text-base-content-default truncate text-lg">
              {highlight(company.name, query)}
            </h3>
            {company.aliases.length > 0 && (
              <p className="prose-label-sm text-base-content-medium truncate font-mono">
                aka {company.aliases.join(', ')}
              </p>
            )}
          </div>
          {company.hasBrief && (
            <span
              title="Living brief available"
              className="inline-flex flex-shrink-0 items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200"
            >
              <BiBookContent className="h-3.5 w-3.5" /> Brief
            </span>
          )}
        </div>

        <p className="text-base-content-medium line-clamp-3 text-sm">
          {highlight(company.description, query)}
        </p>

        <div className="flex flex-wrap gap-1.5 pt-1">
          {company.sector && (
            <Chip color="indigo">{company.sector}</Chip>
          )}
          {company.region && <Chip color="emerald">{company.region}</Chip>}
          {company.latestFundingStage && (
            <Chip color="amber">
              {company.latestFundingStage}
              {company.latestFundingDate &&
                ` · ${company.latestFundingDate.slice(0, 4)}`}
            </Chip>
          )}
          {company.latestSignalDate && (
            <Chip color="slate">
              Signal {company.latestSignalDate}
            </Chip>
          )}
        </div>
      </Link>
    </article>
  )
}

const chipPalette = {
  indigo: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  emerald: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  amber: 'bg-amber-50 text-amber-800 ring-amber-200',
  slate: 'bg-slate-100 text-slate-700 ring-slate-200',
} as const

function Chip({
  color,
  children,
}: {
  color: keyof typeof chipPalette
  children: React.ReactNode
}) {
  return (
    <span
      className={cx(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1',
        chipPalette[color],
      )}
    >
      {children}
    </span>
  )
}

export function ExternalLinkPill({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="ring-base-divider-subtle hover:ring-base-divider-default inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 hover:bg-slate-50"
    >
      {label}
      <BiLinkExternal className="h-3 w-3" />
    </a>
  )
}
