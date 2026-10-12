const base = import.meta.env.BASE_URL.replace(/\/?$/, "/");
export function urlFor(path = ""): string {
  const relativePath = path.replace(/^\/+/, "");
  return relativePath ? base + relativePath : base === "/" ? "/" : base.slice(0, -1);
}
export function digestUrl(date: string): string {
  return urlFor("digests/" + date + (import.meta.env.DEV ? "" : ".html"));
}
