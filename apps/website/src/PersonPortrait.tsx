import React from 'react';
import type { Candidate } from './candidate-data';

// Small, authored illustrations for the fictional people in this demonstration.
export function PersonPortrait({ person, className = '' }: { person: Candidate; className?: string }) {
  const seed = Array.from(person.name).reduce((sum, letter) => sum + letter.charCodeAt(0), 0);
  const skin = ['#EAC2A7', '#D5A17C', '#B87E60', '#F0CEB5'][seed % 4];
  const hair = ['#433B38', '#775449', '#C29463', '#292E35', '#77655D'][seed % 5];
  const shirt = ['#657EAA', '#788F7D', '#8B7B9D', '#B28472'][seed % 4];
  const longHair = seed % 3 === 0;
  return <span className={`person-portrait ${person.color} ${className}`} aria-hidden="true">
    <svg viewBox="0 0 64 64" fill="none">
      {longHair && <path d="M16 52V27C16 7 48 7 48 27V52Z" fill={hair}/>}
      <path d="M7 66C9 45 22 44 32 44C44 44 56 48 58 66" fill={shirt}/>
      <path d="M26 39H38V47C36 54 28 54 26 47Z" fill={skin}/>
      <path d="M19 27C19 11 45 11 45 27V32C45 42 39 47 32 47C25 47 19 42 19 32Z" fill={skin}/>
      {seed % 2 === 0 ? <path d="M17 29C12 8 44 5 47 24L45 31L41 21C30 26 27 16 22 25L19 31Z" fill={hair}/> : <path d="M17 28C14 7 50 7 47 29L43 24L38 19C34 26 25 21 20 30Z" fill={hair}/>}
      <path d="M25 31H26M38 31H39" stroke="#40352E" strokeWidth="2" strokeLinecap="round"/>
      <path d="M29 39Q32 41 35 39" stroke="#925E51" strokeWidth="1.4" strokeLinecap="round"/>
      {seed % 4 === 1 && <g stroke="#535158" strokeWidth="1.5"><rect x="21" y="28" width="10" height="7" rx="3"/><rect x="34" y="28" width="10" height="7" rx="3"/><path d="M31 31H34"/></g>}
    </svg>
  </span>;
}
