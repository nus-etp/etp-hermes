import Link from 'next/link'
import { BiNews, BiPulse } from 'react-icons/bi'

import { meta } from '~/lib/data'

export function Header() {
  return (
    <header className="border-base-divider-subtle bg-base-canvas-default sticky top-0 z-30 border-b backdrop-blur supports-[backdrop-filter]:bg-white/80">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8">
        <Link href="/" className="group flex items-center gap-2.5">
          <span
            aria-hidden
            className="flex h-9 w-9 items-center justify-center rounded-md bg-gradient-to-br from-indigo-500 to-fuchsia-500 text-white shadow-sm transition-transform group-hover:scale-105"
          >
            <BiPulse className="h-5 w-5" />
          </span>
          <div className="flex flex-col leading-tight">
            <span className="prose-headline-base-medium tracking-tight">
              etp-hermes
            </span>
            <span className="prose-label-sm text-base-content-medium">
              {meta.totalCompanies} companies tracked
            </span>
          </div>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <Link
            href="/"
            className="hover:bg-base-canvas-alt-default rounded-md px-3 py-2 font-medium"
          >
            Directory
          </Link>
          <Link
            href="/digests/"
            className="hover:bg-base-canvas-alt-default flex items-center gap-1.5 rounded-md px-3 py-2 font-medium"
          >
            <BiNews className="h-4 w-4" />
            Daily digests
          </Link>
        </nav>
      </div>
    </header>
  )
}
