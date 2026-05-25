'use client'

import type { PropsWithChildren } from 'react'
import { useRouter } from 'next/navigation'
import { RouterProvider } from 'react-aria-components'

declare module 'react-aria-components' {
  interface RouterConfig {
    routerOptions: NonNullable<
      Parameters<ReturnType<typeof useRouter>['push']>[1]
    >
  }
}

export function ClientProviders({ children }: PropsWithChildren) {
  const router = useRouter()

  return (
    // eslint-disable-next-line @typescript-eslint/unbound-method
    <RouterProvider navigate={router.push}>{children}</RouterProvider>
  )
}
