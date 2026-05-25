import Link from 'next/link'
import { notFound } from 'next/navigation'
import { BiArrowBack } from 'react-icons/bi'

import { digestDates, digests } from '~/lib/data'

import { Markdown } from '../../_components/markdown'

interface Params {
  params: Promise<{ date: string }>
}

export function generateStaticParams() {
  return digestDates.map((date) => ({ date }))
}

export default async function DigestPage({ params }: Params) {
  const { date } = await params
  const md = digests[date]
  if (!md) notFound()

  const prev = digestDates.find((d) => d < date)
  const next = [...digestDates].reverse().find((d) => d > date)

  return (
    <article className="flex flex-col gap-6">
      <Link
        href="/digests/"
        className="text-base-content-medium hover:text-base-content-default inline-flex w-fit items-center gap-1 text-sm"
      >
        <BiArrowBack className="h-4 w-4" /> All digests
      </Link>
      <Markdown>{md}</Markdown>
      <nav className="border-base-divider-subtle flex items-center justify-between border-t pt-4 text-sm">
        {prev ? (
          <Link
            href={`/digests/${prev}/`}
            className="text-interaction-main-default hover:underline"
          >
            ← {prev}
          </Link>
        ) : (
          <span />
        )}
        {next ? (
          <Link
            href={`/digests/${next}/`}
            className="text-interaction-main-default hover:underline"
          >
            {next} →
          </Link>
        ) : (
          <span />
        )}
      </nav>
    </article>
  )
}
