/** @type {import("next").NextConfig} */
const config = {
  output: 'export',
  basePath: '/etp-hermes',
  assetPrefix: '/etp-hermes/',
  trailingSlash: true,
  images: { unoptimized: true },
  reactCompiler: true,
  transpilePackages: ['@acme/ui', '@acme/common'],
  typescript: { ignoreBuildErrors: true },
}

export default config
