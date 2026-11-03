'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import {
  parseAsString,
  parseAsStringEnum,
  useQueryState,
} from 'nuqs'
import { BiDownArrowAlt, BiSearch, BiUpArrowAlt, BiX } from 'react-icons/bi'

import { cx } from '~/lib/cn'
import type { CompanyMetrics } from '~/lib/data'

const SORT_KEYS = [
  'score',
  'name',
  'funding',
  'rounds',
  'latestRound',
  'jobs',
  'stars',
  'hn',
  'signals',
  'age',
  'employees',
  'backers',
] as const
type SortKey = (typeof SORT_KEYS)[number]
type Direction = 'asc' | 'desc'

interface RankedCompany extends CompanyMetrics {
  score: number
  scoreInputs: number
  hnCounted?: number
  rank?: number
}

const SCORE_METRICS: ((c: CompanyMetrics) => number | undefined)[] = [
  (c) => c.fundingUsd,
  (c) => c.jobsOpen,
  (c) => c.githubStars,
  (c) => (c.ambiguousName ? undefined : c.hn30d),
  (c) => c.employeesFloor,
  (c) => c.backers.length,
]

const SORT_VALUE: Record<SortKey, (c: RankedCompany) => number | string | undefined> = {
  score: (c) => (c.scoreInputs ? c.score : undefined),
  name: (c) => c.name.toLowerCase(),
  funding: (c) => c.fundingUsd,
  rounds: (c) => c.fundingRounds || undefined,
  latestRound: (c) => c.latestRoundDate,
  jobs: (c) => c.jobsOpen,
  stars: (c) => c.githubStars,
  hn: (c) => c.hnCounted,
  signals: (c) => c.signals90d || undefined,
  age: (c) => c.ageYears,
  employees: (c) => c.employeesFloor,
  backers: (c) => c.backers.length || undefined,
}

function percentileRanks(values: (number | undefined)[]) {
  const present = values
    .map((value, index) => ({ value, index }))
    .filter((entry): entry is { value: number; index: number } =>
      typeof entry.value === 'number' && entry.value > 0,
    )
    .sort((a, b) => a.value - b.value)
  const ranks = new Array<number>(values.length).fill(0)
  const firstPosition = new Map<number, number>()
  const lastPosition = new Map<number, number>()
  present.forEach((entry, position) => {
    if (!firstPosition.has(entry.value)) firstPosition.set(entry.value, position)
    lastPosition.set(entry.value, position)
  })
  for (const entry of present) {
    const first = firstPosition.get(entry.value) ?? 0
    const last = lastPosition.get(entry.value) ?? first
    ranks[entry.index] = (first + last + 2) / 2 / present.length
  }
  return ranks
}

function withScores(companies: CompanyMetrics[]): RankedCompany[] {
  const perMetric = SCORE_METRICS.map((metric) => percentileRanks(companies.map(metric)))
  return companies.map((c, i) => {
    const parts = perMetric.map((ranks) => ranks[i] ?? 0)
    const total = parts.reduce((a, b) => a + b, 0)
    return {
      ...c,
      score: Math.round((total / SCORE_METRICS.length) * 1000) / 10,
      scoreInputs: parts.filter((p) => p > 0).length,
      hnCounted: c.ambiguousName ? undefined : c.hn30d,
    }
  })
}

function formatUsd(value?: number) {
  if (value === undefined) return undefined
  if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(value >= 1e8 ? 0 : 1)}M`
  if (value >= 1e3) return `$${Math.round(value / 1e3)}K`
  return `$${value}`
}

function formatCount(value?: number) {
  return value === undefined ? undefined : value.toLocaleString('en-US')
}

function median(values: number[]) {
  if (values.length === 0) return undefined
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  const upper = sorted[mid] ?? 0
  return sorted.length % 2 ? upper : ((sorted[mid - 1] ?? upper) + upper) / 2
}

interface Props {
  companies: CompanyMetrics[]
  countries: string[]
  asOf: string
  signalWindowDays: number
}

export function MetricsTable({ companies, countries, asOf, signalWindowDays }: Props) {
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''))
  const [country, setCountry] = useQueryState('country', parseAsString.withDefault(''))
  const [showInactive, setShowInactive] = useQueryState('inactive', parseAsString.withDefault(''))
  const [sort, setSort] = useQueryState(
    'sort',
    parseAsStringEnum<SortKey>([...SORT_KEYS]).withDefault('score'),
  )
  const [direction, setDirection] = useQueryState(
    'dir',
    parseAsStringEnum<Direction>(['asc', 'desc']).withDefault(sort === 'name' ? 'asc' : 'desc'),
  )

  const scored = useMemo(() => withScores(companies), [companies])

  const rows = useMemo(() => {
    const query = q.trim().toLowerCase()
    const visible = scored.filter((c) => {
      if (showInactive !== '1' && c.inactive) return false
      if (country && c.country !== country) return false
      if (query.length >= 2 && !c.name.toLowerCase().includes(query)) return false
      return true
    })
    const value = SORT_VALUE[sort]
    const sign = direction === 'asc' ? 1 : -1
    const sorted = [...visible].sort((a, b) => {
      const av = value(a)
      const bv = value(b)
      if (av === undefined && bv === undefined) return a.name.localeCompare(b.name)
      if (av === undefined) return 1
      if (bv === undefined) return -1
      if (av === bv) return a.name.localeCompare(b.name)
      return (av < bv ? -1 : 1) * sign
    })
    let rank = 0
    return sorted.map((c) => ({ ...c, rank: value(c) === undefined ? undefined : ++rank }))
  }, [scored, q, country, showInactive, sort, direction])

  const summary = useMemo(() => {
    const funded = rows.filter((c) => c.fundingUsd !== undefined)
    const ages = rows.map((c) => c.ageYears).filter((v): v is number => v !== undefined)
    return {
      fundingTotal: funded.reduce((a, c) => a + (c.fundingUsd ?? 0), 0),
      fundedCount: funded.length,
      openRoles: rows.reduce((a, c) => a + (c.jobsOpen ?? 0), 0),
      hiringCount: rows.filter((c) => (c.jobsOpen ?? 0) > 0).length,
      stars: rows.reduce((a, c) => a + (c.githubStars ?? 0), 0),
      githubCount: rows.filter((c) => c.githubStars !== undefined).length,
      medianAge: median(ages),
      agedCount: ages.length,
      backedCount: rows.filter((c) => c.backers.length > 0).length,
      signalCount: rows.reduce((a, c) => a + c.signals90d, 0),
    }
  }, [rows])

  const onSort = (key: SortKey) => {
    if (key === sort) {
      void setDirection(direction === 'asc' ? 'desc' : 'asc')
      return
    }
    void setSort(key === 'score' ? null : key)
    void setDirection(null)
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Tile
          label="Disclosed funding"
          value={formatUsd(summary.fundingTotal) ?? '$0'}
          detail={`${summary.fundedCount} companies`}
        />
        <Tile
          label="Open roles"
          value={formatCount(summary.openRoles) ?? '0'}
          detail={`${summary.hiringCount} hiring`}
        />
        <Tile
          label="GitHub stars"
          value={formatCount(summary.stars) ?? '0'}
          detail={`${summary.githubCount} orgs tracked`}
        />
        <Tile
          label="Median age"
          value={summary.medianAge === undefined ? '—' : `${summary.medianAge.toFixed(1)} yrs`}
          detail={`${summary.agedCount} ACRA-matched`}
        />
        <Tile
          label="Investor-listed"
          value={String(summary.backedCount)}
          detail="in tracked portfolios"
        />
        <Tile
          label={`Signals (${signalWindowDays}d)`}
          value={formatCount(summary.signalCount) ?? '0'}
          detail="dated brief bullets"
        />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-0 flex-1 basis-64">
          <BiSearch className="text-base-content-medium pointer-events-none absolute top-3 left-3 h-4 w-4" />
          <input
            type="search"
            value={q}
            onChange={(e) => void setQ(e.target.value || null)}
            aria-label="Search companies"
            placeholder="Search companies…"
            className="border-base-divider-default placeholder:text-base-content-subtle focus:border-interaction-main-default focus:ring-interaction-main-default w-full rounded-lg border bg-base-canvas-alt py-2.5 pr-9 pl-9 text-sm shadow-sm focus:ring-1 focus:outline-none"
          />
          {q && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => void setQ(null)}
              className="text-base-content-medium hover:text-base-content-default absolute top-2.5 right-2.5"
            >
              <BiX className="h-5 w-5" />
            </button>
          )}
        </div>
        <label className="text-base-content-medium flex items-center gap-2 text-sm">
          Country
          <select
            value={country}
            onChange={(e) => void setCountry(e.target.value || null)}
            className="ring-base-divider-default focus:ring-interaction-main-default rounded-md bg-base-canvas-alt px-2 py-1.5 text-sm ring-1 focus:ring-2 focus:outline-none"
          >
            <option value="">All</option>
            {countries.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="text-base-content-medium flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showInactive === '1'}
            onChange={(e) => void setShowInactive(e.target.checked ? '1' : null)}
            className="accent-indigo-600"
          />
          Include struck-off
        </label>
        <p className="text-base-content-medium ml-auto text-sm">
          <span className="text-base-content-default font-semibold">{rows.length}</span>{' '}
          of {companies.length} · data as of {asOf}
        </p>
      </div>

      <div className="ring-base-divider-subtle overflow-x-auto rounded-xl ring-1">
        <table className="w-full min-w-[1100px] border-collapse text-sm">
          <thead className="bg-base-canvas-alt text-base-content-medium text-left text-xs">
            <tr>
              <th className="px-3 py-2.5 font-medium">#</th>
              <SortHeader label="Company" sortKey="name" align="left" sort={sort} direction={direction} onSort={onSort} sticky />
              <SortHeader label="Composite" sortKey="score" sort={sort} direction={direction} onSort={onSort} title="Mean percentile across disclosed funding, open roles, GitHub stars, HN mentions (unambiguous names only), Startup SG headcount and investor listings. A missing metric counts as 0, so breadth of public evidence raises the score." />
              <SortHeader label="Disclosed funding" sortKey="funding" sort={sort} direction={direction} onSort={onSort} title="Sum of amount_usd over funding rounds in companies.json" />
              <SortHeader label="Rounds" sortKey="rounds" sort={sort} direction={direction} onSort={onSort} />
              <SortHeader label="Latest round" sortKey="latestRound" align="left" sort={sort} direction={direction} onSort={onSort} />
              <SortHeader label="Open roles" sortKey="jobs" sort={sort} direction={direction} onSort={onSort} title="Latest open postings on the company's ATS board, with 30-day change" />
              <SortHeader label="GitHub ★" sortKey="stars" sort={sort} direction={direction} onSort={onSort} />
              <SortHeader label="HN 30d" sortKey="hn" sort={sort} direction={direction} onSort={onSort} title="Hacker News mentions in the last 30 days. Generic names are shown greyed and not ranked: their counts are mostly namesakes." />
              <SortHeader label={`Signals ${signalWindowDays}d`} sortKey="signals" sort={sort} direction={direction} onSort={onSort} />
              <SortHeader label="Age" sortKey="age" sort={sort} direction={direction} onSort={onSort} title="Years since ACRA registration" />
              <SortHeader label="Headcount" sortKey="employees" sort={sort} direction={direction} onSort={onSort} title="Self-reported Startup SG employee range" />
              <SortHeader label="Investor lists" sortKey="backers" align="left" sort={sort} direction={direction} onSort={onSort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.slug} className="border-base-divider-subtle hover:bg-base-canvas-alt border-t">
                <td className="text-base-content-subtle px-3 py-2 font-mono text-xs tabular-nums">{c.rank ?? ''}</td>
                <td className="bg-base-canvas-default sticky left-0 px-3 py-2">
                  <Link href={`/c/${c.slug}/`} className="font-medium hover:underline">
                    {c.name}
                  </Link>
                  <div className="text-base-content-subtle flex flex-wrap gap-x-2 text-xs">
                    {c.country && <span>{c.country}</span>}
                    {c.inactive && <span className="text-red-600">{c.acraStatus ?? 'Inactive'}</span>}
                    {c.masLicences.length > 0 && (
                      <span title={c.masLicences.join(', ')}>MAS licensed</span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2 text-right">
                  {c.scoreInputs > 0 ? <ScoreBar score={c.score} inputs={c.scoreInputs} /> : <Empty />}
                </td>
                <NumberCell value={formatUsd(c.fundingUsd)} />
                <NumberCell value={c.fundingRounds ? String(c.fundingRounds) : undefined} />
                <td className="px-3 py-2 whitespace-nowrap">
                  {c.latestRoundStage || c.latestRoundDate ? (
                    <>
                      {c.latestRoundStage ?? 'Round'}
                      {c.latestRoundDate && (
                        <span className="text-base-content-subtle ml-1.5 font-mono text-xs">{c.latestRoundDate}</span>
                      )}
                    </>
                  ) : c.startupSgStage ? (
                    <span className="text-base-content-medium" title="Self-reported on Startup SG">
                      {c.startupSgStage}*
                    </span>
                  ) : (
                    <Empty />
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums whitespace-nowrap">
                  {c.jobsOpen === undefined ? (
                    <Empty />
                  ) : (
                    <>
                      {c.jobsOpen}
                      {c.jobsDelta30 !== undefined && c.jobsDelta30 !== 0 && (
                        <span className={cx('ml-1 text-xs', c.jobsDelta30 > 0 ? 'text-emerald-600' : 'text-red-600')}>
                          {c.jobsDelta30 > 0 ? `+${c.jobsDelta30}` : c.jobsDelta30}
                        </span>
                      )}
                    </>
                  )}
                </td>
                <NumberCell value={formatCount(c.githubStars)} />
                <td
                  className={cx(
                    'px-3 py-2 text-right font-mono tabular-nums',
                    c.ambiguousName && 'text-base-content-subtle line-through decoration-dotted',
                  )}
                  title={c.ambiguousName ? 'Generic name: count is mostly namesakes, not ranked' : undefined}
                >
                  {c.hn30d ? formatCount(c.hn30d) : <Empty />}
                </td>
                <NumberCell value={c.signals90d ? String(c.signals90d) : undefined} />
                <td className="px-3 py-2 text-right font-mono tabular-nums" title={c.registrationDate}>
                  {c.ageYears === undefined ? <Empty /> : c.ageYears.toFixed(1)}
                </td>
                <NumberCell value={c.employees} />
                <td className="px-3 py-2">
                  {c.backers.length ? (
                    <div className="flex flex-wrap gap-1">
                      {c.backers.map((b) => (
                        <span key={b} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs whitespace-nowrap text-slate-700">
                          {b}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <Empty />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-base-content-subtle max-w-3xl text-xs/relaxed">
        Every figure comes from a public, keyless source collected by the pipeline: funding rounds curated in
        the watchlist, ATS job boards, GitHub, Hacker News (Algolia), ACRA registrations, Startup SG profiles,
        MAS licences and investor portfolio pages. Blank cells mean no data, not zero; they always sort last.
        Ranks count only companies with a value in the sorted column. * marks a self-reported Startup SG stage.
      </p>
    </div>
  )
}

function Tile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="ring-base-divider-subtle bg-base-canvas-alt flex flex-col gap-1 rounded-xl px-4 py-3 ring-1">
      <span className="text-base-content-medium text-xs">{label}</span>
      <span className="text-base-content-strong font-mono text-2xl font-semibold tracking-tight tabular-nums">{value}</span>
      <span className="text-base-content-subtle text-xs">{detail}</span>
    </div>
  )
}

function SortHeader({
  label,
  sortKey,
  sort,
  direction,
  onSort,
  align = 'right',
  title,
  sticky,
}: {
  label: string
  sortKey: SortKey
  sort: SortKey
  direction: Direction
  onSort: (key: SortKey) => void
  align?: 'left' | 'right'
  title?: string
  sticky?: boolean
}) {
  const active = sort === sortKey
  const Arrow = direction === 'asc' ? BiUpArrowAlt : BiDownArrowAlt
  return (
    <th
      aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      className={cx('px-3 py-2.5 font-medium whitespace-nowrap', sticky && 'bg-base-canvas-alt sticky left-0')}
      title={title}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cx(
          'hover:text-base-content-default inline-flex items-center gap-0.5',
          align === 'right' && 'w-full justify-end',
          active && 'text-base-content-default font-semibold',
          title && 'underline decoration-dotted underline-offset-4',
        )}
      >
        {label}
        {active && <Arrow className="h-3.5 w-3.5" />}
      </button>
    </th>
  )
}

function NumberCell({ value }: { value?: string }) {
  return (
    <td className="px-3 py-2 text-right font-mono tabular-nums whitespace-nowrap">
      {value ?? <Empty />}
    </td>
  )
}

function ScoreBar({ score, inputs }: { score: number; inputs: number }) {
  return (
    <div
      className="ml-auto flex w-28 items-center gap-2"
      title={`${score} from ${inputs} of ${SCORE_METRICS.length} metrics`}
    >
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.min(score, 100)}%` }} />
      </div>
      <span className="w-9 text-right font-mono text-xs tabular-nums">{score.toFixed(1)}</span>
    </div>
  )
}

function Empty() {
  return <span className="text-base-content-subtle">–</span>
}
