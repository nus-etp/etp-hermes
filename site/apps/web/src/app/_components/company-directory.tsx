'use client'

import { useMemo } from 'react'
import { parseAsArrayOf, parseAsString, parseAsStringEnum, useQueryState } from 'nuqs'
import { BiSearch, BiX } from 'react-icons/bi'

import { cx } from '~/lib/cn'
import type { Company } from '~/lib/data'

import { CompanyCard } from './company-card'

const SORT_OPTIONS = ['relevance', 'name', 'funding', 'signal'] as const
type SortKey = (typeof SORT_OPTIONS)[number]

interface Props {
  companies: Company[]
  sectors: string[]
  regions: string[]
  stages: string[]
  sourceTypes: string[]
}

function normalize(s: string) {
  return s.toLowerCase()
}

export function CompanyDirectory({
  companies,
  sectors,
  regions,
  stages,
  sourceTypes,
}: Props) {
  const [q, setQ] = useQueryState('q', parseAsString.withDefault(''))
  const [briefOnly, setBriefOnly] = useQueryState(
    'brief',
    parseAsString.withDefault(''),
  )
  const [selSectors, setSelSectors] = useQueryState(
    'sector',
    parseAsArrayOf(parseAsString).withDefault([]),
  )
  const [selRegions, setSelRegions] = useQueryState(
    'region',
    parseAsArrayOf(parseAsString).withDefault([]),
  )
  const [selStages, setSelStages] = useQueryState(
    'stage',
    parseAsArrayOf(parseAsString).withDefault([]),
  )
  const [selSources, setSelSources] = useQueryState(
    'source',
    parseAsArrayOf(parseAsString).withDefault([]),
  )
  const [sort, setSort] = useQueryState(
    'sort',
    parseAsStringEnum<SortKey>([...SORT_OPTIONS]).withDefault('relevance'),
  )

  const filtered = useMemo(() => {
    const ql = normalize(q ?? '').trim()
    const out = companies.filter((c) => {
      if (briefOnly === '1' && !c.hasBrief) return false
      if (ql.length >= 2) {
        const hay = normalize(
          `${c.name} ${c.aliases.join(' ')} ${c.description}`,
        )
        if (!hay.includes(ql)) return false
      }
      if (selSectors.length && (!c.sector || !selSectors.includes(c.sector)))
        return false
      if (selRegions.length && (!c.region || !selRegions.includes(c.region)))
        return false
      if (
        selStages.length &&
        !c.fundingStages.some((s) => selStages.includes(s))
      )
        return false
      if (
        selSources.length &&
        !c.sourceTypes.some((s) => selSources.includes(s))
      )
        return false
      return true
    })

    const byName = (a: Company, b: Company) => a.name.localeCompare(b.name)
    const byFunding = (a: Company, b: Company) => {
      const ad = a.latestFundingDate ?? ''
      const bd = b.latestFundingDate ?? ''
      return bd.localeCompare(ad) || byName(a, b)
    }
    const bySignal = (a: Company, b: Company) => {
      const ad = a.latestSignalDate ?? ''
      const bd = b.latestSignalDate ?? ''
      return bd.localeCompare(ad) || byName(a, b)
    }
    const byRelevance = (a: Company, b: Company) => {
      if (a.hasBrief !== b.hasBrief) return a.hasBrief ? -1 : 1
      const sa = bySignal(a, b)
      if (sa !== 0) return sa
      return byFunding(a, b) || byName(a, b)
    }

    const sorter =
      sort === 'name'
        ? byName
        : sort === 'funding'
          ? byFunding
          : sort === 'signal'
            ? bySignal
            : byRelevance
    return [...out].sort(sorter)
  }, [companies, q, briefOnly, selSectors, selRegions, selStages, selSources, sort])

  const totalActiveFilters =
    (briefOnly === '1' ? 1 : 0) +
    selSectors.length +
    selRegions.length +
    selStages.length +
    selSources.length +
    (q && q.length >= 2 ? 1 : 0)

  const clearAll = () => {
    void setQ(null)
    void setBriefOnly(null)
    void setSelSectors(null)
    void setSelRegions(null)
    void setSelStages(null)
    void setSelSources(null)
    void setSort(null)
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="space-y-5 lg:sticky lg:top-20 lg:self-start">
        <div className="relative">
          <BiSearch className="text-base-content-medium pointer-events-none absolute top-3 left-3 h-4 w-4" />
          <input
            type="search"
            value={q ?? ''}
            onChange={(e) => void setQ(e.target.value || null)}
            placeholder="Search companies, aliases, descriptions…"
            className="ring-base-divider-default placeholder:text-base-content-subtle focus:ring-interaction-main-default w-full rounded-lg bg-white py-2.5 pr-3 pl-9 text-sm shadow-sm ring-1 focus:ring-2 focus:outline-none"
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

        <Toggle
          label="Living brief available"
          checked={briefOnly === '1'}
          onChange={(v) => void setBriefOnly(v ? '1' : null)}
        />

        <FilterGroup
          title="Sector"
          options={sectors}
          selected={selSectors}
          onChange={(next) => void setSelSectors(next.length ? next : null)}
        />
        <FilterGroup
          title="Region"
          options={regions}
          selected={selRegions}
          onChange={(next) => void setSelRegions(next.length ? next : null)}
        />
        <FilterGroup
          title="Funding stage"
          options={stages}
          selected={selStages}
          onChange={(next) => void setSelStages(next.length ? next : null)}
        />
        <FilterGroup
          title="Source type"
          options={sourceTypes}
          selected={selSources}
          onChange={(next) => void setSelSources(next.length ? next : null)}
        />

        {totalActiveFilters > 0 && (
          <button
            type="button"
            onClick={clearAll}
            className="text-interaction-main-default hover:text-interaction-main-hover inline-flex items-center gap-1 text-sm font-medium"
          >
            <BiX className="h-4 w-4" /> Clear all filters
          </button>
        )}
      </aside>

      <section className="min-w-0">
        <div className="mb-4 flex items-center justify-between gap-3">
          <p className="text-base-content-medium text-sm">
            <span className="text-base-content-default font-semibold">
              {filtered.length}
            </span>{' '}
            of {companies.length} companies
            {totalActiveFilters > 0 && (
              <span className="text-base-content-subtle">
                {' '}
                · {totalActiveFilters} filter
                {totalActiveFilters === 1 ? '' : 's'} applied
              </span>
            )}
          </p>

          <label className="text-base-content-medium flex items-center gap-2 text-sm">
            Sort
            <select
              value={sort}
              onChange={(e) => void setSort(e.target.value as SortKey)}
              className="ring-base-divider-default focus:ring-interaction-main-default rounded-md bg-white px-2 py-1 text-sm ring-1 focus:ring-2 focus:outline-none"
            >
              <option value="relevance">Relevance</option>
              <option value="name">A–Z</option>
              <option value="funding">Latest funding</option>
              <option value="signal">Latest signal</option>
            </select>
          </label>
        </div>

        {filtered.length === 0 ? (
          <div className="ring-base-divider-subtle flex flex-col items-center justify-center gap-3 rounded-xl bg-white py-16 text-center ring-1">
            <p className="prose-headline-base-semibold">No matches</p>
            <p className="text-base-content-medium max-w-sm text-sm">
              Try removing a filter or broadening your search.
            </p>
            <button
              type="button"
              onClick={clearAll}
              className="text-interaction-main-default text-sm font-medium hover:underline"
            >
              Reset filters
            </button>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((c) => (
              <CompanyCard key={c.slug} company={c} query={q ?? ''} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="ring-base-divider-subtle flex cursor-pointer items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-sm ring-1">
      <span className="font-medium">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cx(
          'relative inline-flex h-5 w-9 flex-shrink-0 rounded-full transition',
          checked ? 'bg-indigo-600' : 'bg-slate-300',
        )}
      >
        <span
          className={cx(
            'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition',
            checked && 'translate-x-4',
          )}
        />
      </button>
    </label>
  )
}

function FilterGroup({
  title,
  options,
  selected,
  onChange,
}: {
  title: string
  options: string[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  if (options.length === 0) return null
  const toggle = (opt: string) => {
    if (selected.includes(opt)) onChange(selected.filter((s) => s !== opt))
    else onChange([...selected, opt])
  }
  return (
    <fieldset>
      <legend className="prose-label-md text-base-content-default mb-2 font-semibold">
        {title}
      </legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((opt) => {
          const active = selected.includes(opt)
          return (
            <button
              key={opt}
              type="button"
              onClick={() => toggle(opt)}
              className={cx(
                'rounded-full px-2.5 py-1 text-xs font-medium ring-1 transition',
                active
                  ? 'bg-indigo-600 text-white ring-indigo-600 hover:bg-indigo-700'
                  : 'ring-base-divider-default text-base-content-medium hover:bg-slate-50',
              )}
              aria-pressed={active}
            >
              {opt}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}
