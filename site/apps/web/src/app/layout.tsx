import type { Metadata } from 'next'
import { NuqsAdapter } from 'nuqs/adapters/next/app'

import '~/app/globals.css'

import { cn } from '@opengovsg/oui-theme'

import { ibmPlexMono, inter } from '~/lib/fonts'
import { Footer } from './_components/footer'
import { Header } from './_components/header'
import { ClientProviders } from './provider'

const SITE_NAME = 'etp-hermes'
const SITE_DESCRIPTION =
  'Daily intelligence on Southeast Asian climate, ag, and deep-tech companies.'

export const metadata: Metadata = {
  title: SITE_NAME,
  description: SITE_DESCRIPTION,
  openGraph: {
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    siteName: SITE_NAME,
  },
}

export default function RootLayout(props: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
          'text-base-content-default bg-base-canvas-default flex min-h-dvh flex-col font-sans antialiased',
          inter.variable,
          ibmPlexMono.variable,
        )}
      >
        <ClientProviders>
          <Header />
          <NuqsAdapter>
            <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
              {props.children}
            </main>
          </NuqsAdapter>
          <Footer />
        </ClientProviders>
      </body>
    </html>
  )
}
