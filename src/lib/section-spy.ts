export interface SectionPosition { id: string; top: number }

/**
 * The section whose heading most recently crossed the reading line (a fraction of the viewport height).
 * Above the first heading nothing is active; at the very bottom the last section wins, so short final
 * sections that can never reach the line still get highlighted.
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
