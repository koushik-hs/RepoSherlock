import type { NextConfig } from "next";

const config: NextConfig = {
  agentRules: false,
  devIndicators: false,
  ...(process.env.REPOSHERLOCK_DEV_LAUNCHER === "1" && {
    distDir: ".next/launcher",
    logging: false,
  }),
};

export default config;
