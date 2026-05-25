import Link from 'next/link'

export default function NotFoundPage() {
  return (
    <main className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
      <p className="font-mono text-sm text-slate-500">404</p>
      <h1 className="text-3xl font-semibold tracking-tight">
        Nothing tracked here
      </h1>
      <p className="text-base-content-medium max-w-md">
        The company or digest you’re looking for doesn’t exist in our index.
      </p>
      <Link
        href="/"
        className="text-interaction-main-default mt-2 inline-flex items-center gap-1 text-sm font-medium hover:underline"
      >
        ← Back to directory
      </Link>
    </main>
  )
}
