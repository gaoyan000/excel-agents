/** @type {import('next').NextConfig} */
// `output: 'export'` -> fully static site in web/out for Cloudflare Pages.
// Safe here: the whole UI is a client component that fetches the API at
// runtime (no SSR / server actions). NEXT_PUBLIC_API_BASE is inlined at
// build time, so it must be set in the Pages build environment.
const nextConfig = {
  reactStrictMode: true,
  output: "export",
  images: { unoptimized: true },
};
export default nextConfig;
