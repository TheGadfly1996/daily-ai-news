import type { APIRoute } from "astro";
import { getDigests } from "../lib/data.ts";
import { renderFeed } from "../lib/rss.ts";
import { urlFor } from "../lib/urls.ts";

export const GET: APIRoute = ({ site }) => {
  if (!site) throw new Error("Astro site URL is required for the RSS feed");
  return new Response(renderFeed(getDigests(), new URL(urlFor(), site).href), {
    headers: { "Content-Type": "application/rss+xml; charset=utf-8" },
  });
};
