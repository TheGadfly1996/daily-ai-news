import { defineConfig } from "astro/config";
import vue from "@astrojs/vue";
import tailwindcss from "@tailwindcss/vite";

const [owner, repository] = (process.env.GITHUB_REPOSITORY || "TheGadfly1996/daily-ai-news").split("/");
const deployment = new URL(process.env.SITE_URL || `https://${owner}.github.io/${repository}`);

export default defineConfig({
  site: deployment.origin,
  base: process.env.BASE_PATH || deployment.pathname,
  output: "static",
  trailingSlash: "never",
  build: { format: "file" },
  integrations: [vue()],
  vite: { plugins: [tailwindcss()] },
});
