import { defineConfig } from "astro/config";
import vue from "@astrojs/vue";
import tailwindcss from "@tailwindcss/vite";

// 部署地址默认取自 GITHUB_REPOSITORY；自定义域名用 SITE_URL，路径用 BASE_PATH 覆盖
const [owner, repository] = (process.env.GITHUB_REPOSITORY || "TheGadfly1996/daily-ai-news").split("/");
const deployment = new URL(process.env.SITE_URL || `https://${owner}.github.io/${repository}`);

export default defineConfig({
  site: deployment.origin,
  base: process.env.BASE_PATH || deployment.pathname,
  output: "static",            // 纯静态站点，新闻页面构建时生成
  trailingSlash: "never",
  build: { format: "file" },   // 输出 digests/YYYY-MM-DD.html，兼容既有链接
  integrations: [vue()],
  vite: { plugins: [tailwindcss()] },
});
