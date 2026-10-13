import { useLayoutEffect, useState, type RefObject } from 'react';

export function fitSceneFrame(availableWidth: number, availableHeight: number, compact: boolean, width = 1120, height = 666) {
  // A phone is an interactive layout, not a scaled picture of a 390px canvas.
  // Its content can scroll inside the available frame without shrinking type
  // or touch targets. The desktop scene keeps its existing fit behavior.
  if (compact) return { width: availableWidth, height: availableHeight, scale: 1 };
  return { width, height, scale: Math.min(1, availableWidth / width, availableHeight / height) };
}

export function useSceneFrame(space: RefObject<HTMLDivElement | null>, width = 1120, height = 666) {
  const [layout, setLayout] = useState({ width, height, scale: 1 });
  useLayoutEffect(() => {
    const element = space.current;
    if (!element) return;
    const media = window.matchMedia('(max-width: 600px)');
    const measure = () => {
      if (!element.clientWidth || !element.clientHeight) return;
      const next = fitSceneFrame(element.clientWidth, element.clientHeight, media.matches, width, height);
      setLayout(previous => previous.width === next.width && previous.height === next.height && previous.scale === next.scale ? previous : next);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    media.addEventListener('change', measure);
    measure();
    return () => {
      observer.disconnect();
      media.removeEventListener('change', measure);
    };
  }, [height, space, width]);
  return { width: layout.width, height: layout.height, transform: `translate(-50%, -50%) scale(${layout.scale})` };
}
