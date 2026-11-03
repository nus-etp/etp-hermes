import { Suspense } from 'react'
import type { Metadata } from 'next'

import { metrics } from '~/lib/data'

import { MetricsTable } from '../_components/metrics-table'

export const metadata: Metadata = {
  title: 'Metrics · hermenaut',
  description: 'Hard, public metrics for every tracked company, sortable and ranked.',
}

const COUNTRIES = [
  ...new Set(metrics.companies.map((c) => c.country).filter((c): c is string => !!c)),
].sort()

export default function MetricsPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Metrics</h1>
        <p className="text-base-content-medium max-w-2xl">
          Hard, publicly available numbers for every tracked company. Click a
          column to rank by it; the composite score blends them into one stack
          rank.
        </p>
      </header>
      <Suspense
        fallback={
          <div className="ring-base-divider-subtle text-base-content-medium flex h-64 items-center justify-center rounded-xl bg-base-canvas-alt text-sm ring-1">
            Loading metrics…
          </div>
        }
      >
        <MetricsTable
          companies={metrics.companies}
          countries={COUNTRIES}
          asOf={metrics.asOf}
          signalWindowDays={metrics.signalWindowDays}
        />
      </Suspense>
    </div>
  )
}
