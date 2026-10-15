export interface SectionPosition { id: string; top: number }

/**
 * 当前所在栏目：标题最近一次越过“阅读线”（视口高度的一定比例，默认 30%）的那个栏目。
 * 第一个标题之前没有高亮；滚到页面最底部时高亮最后一个栏目，
 * 这样内容很短、永远到不了阅读线的末栏也能被高亮。
 */
export function activeSectionId(
  sections: SectionPosition[], viewportHeight: number, atBottom: boolean, line = 0.3,
): string | undefined {
  if (!sections.length) return undefined;
  if (atBottom) return sections[sections.length - 1].id;
  let active: string | undefined;
  for (const section of sections) {
    if (section.top <= viewportHeight * line) active = section.id;
    else break;
  }
  return active;
}
