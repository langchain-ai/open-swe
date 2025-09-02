/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
  env: {
    NEXT_PUBLIC_GITHUB_DISABLED: process.env.NEXT_PUBLIC_GITHUB_DISABLED,
  },
};

export default nextConfig;
