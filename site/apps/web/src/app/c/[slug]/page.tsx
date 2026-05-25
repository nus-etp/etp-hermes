import Link from 'next/link'
import { notFound } from 'next/navigation'
import { BiArrowBack, BiLinkExternal } from 'react-icons/bi'

import {
  briefs,
  companies,
  companyBySlug,
  fundingRoundsBySlug,
  sourcesBySlug,
  type FundingRound,
} from '~/lib/data'

import { Markdown } from '../../_components/markdown'

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '/etp-hermes'

interface Params {
  params: Promise<{ slug: string }>
}

export function generateStaticParams() {
  return companies.map((c) => ({ slug: c.slug }))
}

export default async function CompanyPage({ params }: Params) {
  const { slug } = await params
  const company = companyBySlug.get(slug)
  if (!company) notFound()

  const brief = briefs[slug]
  const rounds = fundingRoundsBySlug[slug] ?? []
  const sources = sourcesBySlug[slug] ?? []

  return (
    <article className="flex flex-col gap-8">
      <Link
        href="/"
        className="text-base-content-medium hover:text-base-content-default inline-flex w-fit items-center gap-1 text-sm"
      >
        <BiArrowBack className="h-4 w-4" /> Back to directory
      </Link>

      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-3">
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {company.name}
          </h1>
          {company.hasBrief && (
            <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700 ring-1 ring-indigo-200">
              Living brief
            </span>
          )}
        </div>
        {company.aliases.length > 0 && (
          <p className="text-base-content-medium font-mono text-sm">
            aka {company.aliases.join(', ')}
          </p>
        )}
        <p className="text-base-content-medium max-w-3xl text-base">
          {company.description}
        </p>
        <div className="flex flex-wrap gap-2 pt-1">
          {company.sector && <FactPill label="Sector" value={company.sector} />}
          {company.regionFull && (
            <FactPill label="Region" value={company.regionFull} />
          )}
          {company.latestFundingStage && (
            <FactPill
              label="Latest funding"
              value={`${company.latestFundingStage}${
                company.latestFundingDate
                  ? ` · ${company.latestFundingDate}`
                  : ''
              }`}
            />
          )}
          {company.latestSignalDate && (
            <FactPill label="Latest signal" value={company.latestSignalDate} />
          )}
        </div>
      </header>

      {company.hasBrief && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`${BASE_PATH}/briefs/${slug}/infographic.png`}
          alt={`${company.name} infographic`}
          width={1600}
          height={1200}
          loading="lazy"
          className="border-base-divider-subtle w-full rounded-2xl border shadow-sm"
        />
      )}

      {brief ? (
        <Markdown>{stripBriefHeader(brief)}</Markdown>
      ) : (
        <FallbackBody rounds={rounds} sources={sources} />
      )}
    </article>
  )
}

function stripBriefHeader(md: string) {
  // The brief's first lines duplicate what we already render in the header
  // (title, last-updated, embedded infographic). Drop them so the rendered
  // Markdown starts at "## Thesis".
  return md.replace(/^# .*\n/, '').replace(/^_Last updated:.*_\n/m, '').replace(/^!\[Infographic]\(infographic\.png\)\n/m, '').trim()
}

function FundingTable({ rounds }: { rounds: FundingRound[] }) {
  if (rounds.length === 0) return null
  return (
    <section>
      <h2 className="prose-headline-base-semibold mb-3 text-xl">
        Funding history
      </h2>
      <div className="ring-base-divider-subtle overflow-x-auto rounded-xl bg-white ring-1">
        <table className="min-w-full text-sm">
          <thead className="text-base-content-medium bg-slate-50 text-left text-xs uppercase tracking-wider">
            <tr>
              <th className="px-3 py-2 font-semibold">Date</th>
              <th className="px-3 py-2 font-semibold">Stage</th>
              <th className="px-3 py-2 font-semibold">Amount</th>
              <th className="px-3 py-2 font-semibold">Investors</th>
            </tr>
          </thead>
          <tbody className="divide-base-divider-subtle divide-y">
            {rounds.map((r, i) => (
              <tr key={`${r.date ?? ''}-${i}`}>
                <td className="px-3 py-2 font-mono text-xs">{r.date ?? '—'}</td>
                <td className="px-3 py-2">{r.stage ?? '—'}</td>
                <td className="px-3 py-2">{r.amount ?? '—'}</td>
                <td className="text-base-content-medium px-3 py-2 text-xs">
                  {[
                    ...(r.lead_investors ?? []),
                    ...(r.investors ?? []),
                  ].join(', ') || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function FallbackBody({
  rounds,
  sources,
}: {
  rounds: FundingRound[]
  sources: { type: string; label: string; url: string }[]
}) {
  return (
    <div className="flex flex-col gap-8">
      {sources.length > 0 && (
        <section>
          <h2 className="prose-headline-base-semibold mb-3 text-xl">Sources</h2>
          <ul className="flex flex-wrap gap-2">
            {sources.map((s) => (
              <li key={s.url}>
                <a
                  href={s.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="ring-base-divider-subtle hover:ring-base-divider-default inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1.5 text-xs font-medium ring-1 hover:bg-slate-50"
                >
                  <span className="text-base-content-medium font-mono uppercase">
                    {s.type}
                  </span>
                  <span>{s.label}</span>
                  <BiLinkExternal className="h-3 w-3" />
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}
      <FundingTable rounds={rounds} />
    </div>
  )
}

function FactPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="ring-base-divider-subtle inline-flex items-baseline gap-1.5 rounded-full bg-white px-2.5 py-1 text-xs ring-1">
      <span className="text-base-content-medium font-mono uppercase tracking-wider">
        {label}
      </span>
      <span className="text-base-content-default font-medium">{value}</span>
    </span>
  )
}
