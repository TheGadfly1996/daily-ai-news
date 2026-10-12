export const themes = [
  { id: "minimalist-flat", name: "极简扁平" },
  { id: "corporate-clean", name: "专业企业" },
  { id: "neo-brutalist", name: "新野兽派" },
  { id: "natural-organic", name: "自然有机" },
  { id: "hand-drawn-doodle", name: "手绘涂鸦" },
  { id: "memphis", name: "孟菲斯" },
  { id: "comic-style", name: "漫画" },
  { id: "cottagecore", name: "田园乡村" },
  { id: "pixel-art", name: "复古像素" },
] as const;
export type ThemeId = typeof themes[number]["id"];
export const defaultTheme: ThemeId = "minimalist-flat";
export const themeStorageKey = "daily-news.theme";
export function validTheme(value: unknown): ThemeId {
  return themes.some(theme => theme.id === value) ? value as ThemeId : defaultTheme;
}
