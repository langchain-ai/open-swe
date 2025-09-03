/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
  env: {
    ENABLE_GITHUB: process.env.ENABLE_GITHUB,
  },
};

export default nextConfig;
