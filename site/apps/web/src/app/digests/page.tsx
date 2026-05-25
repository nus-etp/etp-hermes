import Link from 'next/link'
import { BiCalendar } from 'react-icons/bi'

import { digestDates, digests } from '~/lib/data'

function previewOf(md: string) {
  const lines = md.split('\n')
  const companies = lines
    .filter((l) => l.startsWith('### '))
    .map((l) => l.replace(/^###\s+/, '').trim())
  return { count: companies.length, names: companies.slice(0, 6) }
}

export default function DigestsIndex() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Daily digests</h1>
        <p className="text-base-content-medium max-w-2xl">
          Synthesised company updates produced by the agent every day. Each
          digest groups news items by company.
        </p>
      </header>

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {digestDates.map((date) => {
          const { count, names } = previewOf(digests[date] ?? '')
          return (
            <li key={date}>
              <Link
                href={`/digests/${date}/`}
                className="group ring-base-divider-subtle hover:ring-base-divider-default flex h-full flex-col gap-2 rounded-xl bg-white p-5 shadow-sm ring-1 transition hover:-translate-y-0.5 hover:shadow-md"
              >
                <div className="flex items-center justify-between">
                  <span className="prose-headline-base-semibold flex items-center gap-2 text-lg">
                    <BiCalendar className="text-base-content-medium h-4 w-4" />
                    {date}
                  </span>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-xs">
                    {count} update{count === 1 ? '' : 's'}
                  </span>
                </div>
                <p className="text-base-content-medium line-clamp-2 text-sm">
                  {names.length === 0
                    ? 'No company updates'
                    : names.join(' · ')}
                </p>
              </Link>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
