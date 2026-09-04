import { Suspense } from 'react'

import {
  companies,
  meta,
  REGIONS,
  SECTORS,
  SOURCE_TYPES,
  STAGES,
} from '~/lib/data'

import { CompanyDirectory } from './_components/company-directory'

export default function HomePage() {
  return (
    <div className="flex flex-col gap-8">
      <section className="ring-base-divider-medium bg-base-canvas-alt rounded-2xl px-6 py-10 ring-1 sm:px-10 sm:py-14">
        <div className="flex flex-col gap-4">
          <span className="prose-label-sm text-base-content-brand bg-base-canvas-brand-subtle w-fit rounded-full px-3 py-1 font-mono text-xs tracking-wider uppercase">
            etp-hermes signals
          </span>
          <h1 className="text-base-content-strong max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl">
            Southeast Asia’s climate &amp; deep-tech companies, watched daily.
          </h1>
          <p className="text-base-content-medium max-w-2xl text-base/relaxed sm:text-lg">
            A live directory of {meta.totalCompanies} companies, refreshed by an
            autonomous agent that triages news feeds, fetches per-company
            sources, and writes living briefs.
          </p>
          <div className="mt-2 flex flex-wrap gap-3 text-sm">
            <Stat label="Companies" value={String(meta.totalCompanies)} />
            <Stat label="Living briefs" value={String(meta.totalBriefs)} />
            <Stat label="Daily digests" value={String(meta.totalDigests)} />
          </div>
        </div>
      </section>

      <Suspense
        fallback={
          <div className="ring-base-divider-subtle text-base-content-medium flex h-64 items-center justify-center rounded-xl bg-base-canvas-alt text-sm ring-1">
            Loading directory…
          </div>
        }
      >
        <CompanyDirectory
          companies={companies}
          sectors={SECTORS}
          regions={REGIONS}
          stages={STAGES}
          sourceTypes={SOURCE_TYPES}
        />
      </Suspense>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="ring-base-divider-medium bg-base-canvas-default flex items-baseline gap-1.5 rounded-lg px-3 py-1.5 ring-1">
      <span className="text-base-content-brand font-mono text-base font-semibold tracking-tight">
        {value}
      </span>
      <span className="text-base-content-medium text-xs">{label}</span>
    </div>
  )
}
