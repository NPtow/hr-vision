import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Collapsible } from 'radix-ui';
import { ChevronDown } from 'lucide-react';
import type { InterviewParagraph, InterviewTranscriptSection } from './candidate-player-model';

type Props = {
  list: RefObject<HTMLDivElement | null>;
  sections: InterviewTranscriptSection[];
  renderRow: (row: InterviewParagraph, index: number) => ReactNode;
  empty: ReactNode;
  seekable: boolean;
  seek: (seconds: number) => void;
  pauseFollow: () => void;
};

/** One reading surface: the current heading is also the chapter switcher. */
export function UnifiedInterviewTranscript({ list, sections, renderRow, empty, seekable, seek, pauseFollow }: Props) {
  const reduced = useReducedMotion();
  const [reading, setReading] = useState(0);
  const [open, setOpen] = useState(false);
  const pending = useRef<number | null>(null);
  const focusSection = useRef<number | null>(null);
  const frame = useRef<number | undefined>(undefined);
  const count = sections.filter(section => section.chapters.length).length;
  const transition = { duration: reduced ? 0 : .22, ease: [.22, 1, .36, 1] as const };

  const updateReading = useCallback(() => {
    const container = list.current;
    if (!container || open) return;
    let current = 0;
    container.querySelectorAll<HTMLElement>('[data-transcript-section]').forEach(section => {
      if (section.getBoundingClientRect().top - container.getBoundingClientRect().top <= 12) current = Number(section.dataset.transcriptSection);
    });
    setReading(current);
  }, [list, open]);
  useEffect(() => {
    const container = list.current;
    if (!container) return;
    const observer = new ResizeObserver(() => {
      container.style.setProperty('--iv-transcript-height', `${container.clientHeight}px`);
      updateReading();
    });
    observer.observe(container);
    updateReading();
    return () => observer.disconnect();
  }, [list, sections, updateReading]);
  useEffect(() => () => { if (frame.current !== undefined) cancelAnimationFrame(frame.current); }, []);

  const flush = useCallback(() => {
    if (pending.current === null) return;
    const index = pending.current;
    pending.current = null;
    frame.current = requestAnimationFrame(() => {
      const container = list.current;
      const section = container?.querySelector<HTMLElement>(`[data-transcript-section="${index}"]`);
      if (!container || !section) return;
      container.scrollTo({ top: section.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop, behavior: reduced ? 'instant' : 'smooth' });
      focusSection.current = index;
      updateReading();
    });
  }, [list, reduced, updateReading]);
  useEffect(() => {
    if (open || focusSection.current !== reading) return;
    frame.current = requestAnimationFrame(() => {
      const heading = list.current?.querySelector<HTMLElement>(`[data-transcript-section="${reading}"] .iv-chapter-trigger`);
      if (heading) { heading.focus({ preventScroll: true }); focusSection.current = null; }
    });
  }, [list, reading, open]);
  function choose(index: number) {
    pending.current = index;
    const section = sections[index];
    if (seekable && section.start !== null) seek(section.start);
    pauseFollow(); setOpen(false);
    if (reduced) flush();
  }
  function manualScroll() { pauseFollow(); setOpen(false); pending.current = null; focusSection.current = null; }

  return <div ref={list} className="iv-transcript iv-unified-transcript" onWheel={manualScroll} onTouchMove={manualScroll} onScroll={updateReading}>
    {!sections.some(section => section.rows.length) && empty}
    {sections.map((section, index) => <section key={section.id} data-transcript-section={index} className="iv-transcript-section">
      {!!section.chapters.length && <Collapsible.Root className="iv-transcript-heading" data-current={reading === index} open={open && reading === index} onOpenChange={value => {
        pending.current = null; focusSection.current = null;
        if (value && list.current) list.current.scrollTo({ top: list.current.scrollTop, behavior: 'instant' });
        pauseFollow(); setReading(index); setOpen(value);
      }}
        onKeyDown={event => { if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); event.currentTarget.querySelector<HTMLButtonElement>('.iv-chapter-trigger')?.focus({ preventScroll: true }); } }}>
        <h3>{reading === index && count > 1 ? <Collapsible.Trigger asChild><button type="button" className="iv-chapter-trigger" aria-label={`Переключить главу: ${section.chapters.map(chapter => chapter.title).join(' · ')}`}>
          <span>{section.chapters.map(chapter => chapter.title).join(' · ')}</span><motion.span aria-hidden="true" animate={{ rotate: open ? 180 : 0 }} transition={transition}><ChevronDown size={13}/></motion.span>
        </button></Collapsible.Trigger> : <span className="iv-chapter-heading-title">{section.chapters.map(chapter => chapter.title).join(' · ')}</span>}</h3>
        <AnimatePresence initial={false} onExitComplete={flush}>{open && reading === index && <Collapsible.Content forceMount asChild><motion.nav className="iv-chapter-outline" aria-label="Выбрать главу" onWheel={event => event.stopPropagation()} onTouchMove={event => event.stopPropagation()} initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={transition}>
          {sections.map((other, otherIndex) => otherIndex !== index && other.chapters.length > 0 ? <button type="button" key={other.id} onClick={() => choose(otherIndex)}>{other.chapters.map(chapter => chapter.title).join(' · ')}</button> : null)}
        </motion.nav></Collapsible.Content>}</AnimatePresence>
      </Collapsible.Root>}
      {section.chapters.filter(chapter => !chapter.isQuote && chapter.detail && chapter.detail !== chapter.title).map((chapter, chapterIndex) => <p className="iv-chapter-description" key={chapterIndex}>{chapter.detail}</p>)}
      {section.rows.map(({ row, index: rowIndex }) => renderRow(row, rowIndex))}
    </section>)}
  </div>;
}
