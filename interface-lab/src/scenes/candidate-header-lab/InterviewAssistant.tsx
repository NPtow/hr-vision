import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AlignLeft, ArrowUp, CheckCheck, ChevronRight, MessageSquare, Play, RotateCcw, Sparkles, Square, X } from 'lucide-react';
import { transcript } from './demo-data';
import './interview-assistant.css';

type Fragment = typeof transcript[number];
type SearchTurn = { id: number; query: string; status: 'loading' | 'ready' | 'error' | 'cancelled' | 'example_unknown' | 'access_denied'; fragments: Fragment[] };
type PanelTab = 'transcript' | 'assistant' | 'comments' | 'decision';
const prompts = ['Как выясняет потребности?', 'Как работает с ценой?', 'Есть опыт холодных продаж?'];

export function InterviewAssistant({ current, onSeek, decision, searchMode }: { current: number; onSeek: (seconds: number) => void; decision?: ReactNode; searchMode: 'example' | 'jev' }) {
  const [tab, setTab] = useState<PanelTab>(decision ? 'decision' : 'transcript');
  const [input, setInput] = useState('');
  const [turns, setTurns] = useState<SearchTurn[]>([]);
  const [comment, setComment] = useState('');
  const [comments, setComments] = useState<{ text: string; seconds: number }[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [follow, setFollow] = useState(true);
  const request = useRef<AbortController | null>(null);
  const counter = useRef(0);
  const chat = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const inputId = useId();
  const busy = turns.some(turn => turn.status === 'loading');
  const active = transcript.reduce((last, row, index) => row.seconds <= current ? index : last, 0);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => { if (tab === 'assistant' && chat.current) chat.current.scrollTop = chat.current.scrollHeight; }, [turns, tab]);
  useEffect(() => {
    if (!follow || tab !== 'transcript' || !list.current) return;
    const row = list.current.querySelector<HTMLElement>('[aria-current="true"]');
    if (row) list.current.scrollTop = Math.max(0, row.offsetTop - list.current.offsetTop - 60);
  }, [active, follow, tab]);

  async function ask(query: string) {
    query = query.trim();
    if (query.length < 2 || query.length > 300 || request.current) return;
    setTab('assistant'); setInput('');
    const id = ++counter.current;
    if (searchMode === 'example') {
      // Explicit preview fixtures; no fuzzy matching masquerading as a model.
      const examples: Record<string, string[]> = {
        [prompts[0]]: ['fragment-3', 'fragment-4'],
        [prompts[1]]: ['fragment-6'],
        [prompts[2]]: [],
      };
      const ids = examples[query];
      setTurns(previous => [...previous, { id, query, status: ids ? 'ready' : 'example_unknown', fragments: (ids || []).map(ident => transcript.find(row => row.id === ident)!) }]);
      return;
    }
    const controller = new AbortController(); request.current = controller;
    setTurns(previous => [...previous, { id, query, status: 'loading', fragments: [] }]);
    const timeout = window.setTimeout(() => controller.abort('timeout'), 22_000);
    try {
      const response = await fetch('/__demo/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }), signal: controller.signal });
      const data = await response.json();
      if (data.error === 'model_access_denied') {
        setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, status: 'access_denied' } : turn));
        return;
      }
      if (!response.ok) throw new Error('search_unavailable');
      if (!Array.isArray(data.fragments)) throw new Error('invalid_result');
      // Resolve IDs back to source text. A provider can never invent the quote.
      const fragments = data.fragments.map((item: Fragment) => transcript.find(row => row.id === item.id && row.text === item.text)).filter(Boolean) as Fragment[];
      if (fragments.length !== data.fragments.length) throw new Error('unknown_fragment');
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, status: 'ready', fragments } : turn));
    } catch {
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, status: controller.signal.reason === 'cancelled' ? 'cancelled' : 'error' } : turn));
    } finally { window.clearTimeout(timeout); request.current = null; }
  }
  function seek(seconds: number) { onSeek(seconds); }
  function stamp(seconds: number) { return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }
  const titles = { transcript: 'Расшифровка', assistant: 'Об интервью', comments: 'Комментарии', decision: 'Решение' };

  if (collapsed) return <aside className="cda-panel cda-collapsed"><button type="button" onClick={() => setCollapsed(false)}><Sparkles size={19}/>Поиск по интервью<ChevronRight size={18}/></button></aside>;
  return <aside className="cda-panel" aria-label="Материалы интервью">
    <header className="cda-toolbar"><div className="cda-tabs" role="group" aria-label="Материалы">
      <button type="button" aria-label="Расшифровка" title="Расшифровка" aria-pressed={tab === 'transcript'} onClick={() => setTab('transcript')}><AlignLeft size={20}/></button>
      <button type="button" aria-label="Помощник" title="Помощник" aria-pressed={tab === 'assistant'} onClick={() => setTab('assistant')}><Sparkles size={19}/></button>
      <button type="button" aria-label="Комментарии" title="Комментарии" aria-pressed={tab === 'comments'} onClick={() => setTab('comments')}><MessageSquare size={20}/></button>
      {decision && <button type="button" aria-label="Решение" title="Решение" aria-pressed={tab === 'decision'} onClick={() => setTab('decision')}><CheckCheck size={20}/></button>}
    </div><span>{titles[tab]}</span><button type="button" className="cda-close" aria-label="Свернуть панель" title="Свернуть панель" onClick={() => setCollapsed(true)}><X size={17}/></button></header>

    <div className="cda-body">
      {tab === 'transcript' && <div ref={list} className="cda-transcript" onWheel={() => setFollow(false)} onTouchMove={() => setFollow(false)}>
        {transcript.map((row, index) => <button type="button" key={row.id} aria-current={active === index ? 'true' : undefined} onClick={() => { seek(row.seconds); setFollow(true); }}><time>{row.time}</time><span>{row.text}</span></button>)}
      </div>}
      {tab === 'transcript' && !follow && <button type="button" className="cda-follow" onClick={() => setFollow(true)}>К текущему моменту</button>}
      {tab === 'assistant' && <div className="cda-chat" ref={chat}>
        {!turns.length && <div className="cda-welcome"><Sparkles size={26}/><h3>Что хотите узнать<br/>об Анастасии?</h3><p>Найду нужный момент в интервью.<br/>Можно спросить своими словами.</p><div className="cda-prompts">{prompts.map(prompt => <button key={prompt} type="button" onClick={() => void ask(prompt)}>{prompt}<ArrowUp size={15}/></button>)}</div></div>}
        <div className="cda-turns" aria-live="polite" aria-relevant="additions text">{turns.map(turn => <section className="cda-turn" key={turn.id}>
          <p className="cda-question">{turn.query}</p>
          {turn.status === 'loading' ? <p className="cda-searching" role="status"><span/>Ищу подходящие моменты…</p>
            : turn.status === 'access_denied' ? <div className="cda-answer cda-error"><p>Поиск пока не подключён: провайдер не открыл доступ к модели. Расшифровку можно читать и смотреть вручную.</p></div>
            : turn.status === 'example_unknown' ? <div className="cda-answer"><p>Здесь пока можно посмотреть три примера поиска:</p><div className="cda-prompts">{prompts.map(prompt => <button key={prompt} type="button" onClick={() => void ask(prompt)}>{prompt}<ArrowUp size={15}/></button>)}</div></div>
            : turn.status === 'error' ? <div className="cda-answer cda-error"><p>Сейчас не удалось найти фрагменты. Расшифровка по-прежнему доступна.</p><button type="button" onClick={() => void ask(turn.query)}><RotateCcw size={15}/>Попробовать ещё раз</button></div>
            : turn.status === 'cancelled' ? <p className="cda-cancelled">Поиск остановлен.</p>
            : <div className="cda-answer"><p>{turn.fragments.length ? 'Вот что она рассказывает об этом:' : 'В этой записи не нашлось ответа. Это можно уточнить на встрече.'}</p>
              {turn.fragments.map(fragment => <button type="button" className="cda-quote" key={fragment.id} onClick={() => seek(fragment.seconds)} aria-label={`Посмотреть ${fragment.time}: ${fragment.text}`}><span className="cda-quote-time"><Play size={11} fill="currentColor"/>{fragment.time}<span>В записи</span></span><q>{fragment.text}</q></button>)}
            </div>}
        </section>)}</div>
      </div>}
      {tab === 'comments' && <div className="cda-chat">{!comments.length ? <div className="cda-comments-empty"><MessageSquare size={25}/><h3>Заметили важное?</h3><p>Комментарий сохранится у текущего момента записи.</p></div> : comments.map((item, i) => <div key={i} className="cda-comment"><button type="button" onClick={() => seek(item.seconds)}>{stamp(item.seconds)}</button><p>{item.text}</p></div>)}</div>}
      {tab === 'decision' && decision}
    </div>
    {tab === 'comments' ? <form className="cda-composer" onSubmit={event => { event.preventDefault(); if (comment.trim()) { setComments(previous => [...previous, { text: comment.trim(), seconds: current }]); setComment(''); } }}>
      <input aria-label="Комментарий к записи" value={comment} maxLength={2000} onChange={event => setComment(event.target.value)} placeholder={`Комментарий к ${stamp(current)}`}/><button type="submit" disabled={!comment.trim()} aria-label="Добавить комментарий"><ArrowUp size={19}/></button>
    </form> : tab !== 'decision' && <form className="cda-composer" aria-label="Поиск по интервью" onSubmit={event => { event.preventDefault(); void ask(input); }}>
      <label htmlFor={inputId}><Sparkles size={19}/><span className="cdl-sr">Найти момент в интервью</span></label><input id={inputId} value={input} maxLength={300} onChange={event => setInput(event.target.value)} placeholder={turns.length && tab === 'assistant' ? 'Что ещё найти?' : 'Спросите об интервью…'}/>
      {busy ? <button type="button" aria-label="Остановить поиск" onClick={() => request.current?.abort('cancelled')}><Square size={12} fill="currentColor"/></button> : <button type="submit" disabled={input.trim().length < 2} aria-label="Найти фрагменты"><ArrowUp size={19}/></button>}
    </form>}
  </aside>;
}
