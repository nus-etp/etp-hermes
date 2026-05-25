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
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-indigo-600 via-violet-600 to-fuchsia-600 px-6 py-10 text-white shadow-lg sm:px-10 sm:py-14">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-30"
          style={{
            backgroundImage:
              'radial-gradient(circle at 20% 20%, rgba(255,255,255,0.4), transparent 40%), radial-gradient(circle at 80% 0%, rgba(255,255,255,0.3), transparent 35%)',
          }}
        />
        <div className="relative flex flex-col gap-4">
          <span className="prose-label-sm w-fit rounded-full bg-white/15 px-3 py-1 font-mono text-xs uppercase tracking-wider backdrop-blur">
            etp-hermes signals
          </span>
          <h1 className="max-w-3xl text-3xl font-semibold tracking-tight sm:text-5xl">
            Southeast Asia’s climate &amp; deep-tech companies, watched daily.
          </h1>
          <p className="max-w-2xl text-base/relaxed text-white/85 sm:text-lg">
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
          <div className="ring-base-divider-subtle text-base-content-medium flex h-64 items-center justify-center rounded-xl bg-white text-sm ring-1">
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
    <div className="flex items-baseline gap-1.5 rounded-lg bg-white/10 px-3 py-1.5 backdrop-blur">
      <span className="font-mono text-base font-semibold tracking-tight">
        {value}
      </span>
      <span className="text-xs text-white/75">{label}</span>
    </div>
  )
}
