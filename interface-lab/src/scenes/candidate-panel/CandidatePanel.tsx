import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { Dialog, DropdownMenu, Popover, Tabs } from "radix-ui";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileText,
  FolderOpen,
  Info,
  Link2,
  ListVideo,
  Maximize2,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Search,
  Star,
  Upload,
  UsersRound,
  Video,
  X,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  chapters,
  dayLabel,
  freeSlots,
  initialCandidates,
  initialMeetings,
  mmss,
  position,
  weekday,
  weekDays,
  type Candidate,
  type Meeting,
  type Screen,
} from "./model";
import "./panel.css";

const screenNames: Record<Screen, string> = {
  panel: "Панель",
  add: "Добавление",
  schedule: "Встреча",
};
function Action({
  children,
  quiet,
  ...props
}: React.ComponentProps<typeof Button> & { quiet?: boolean }) {
  return (
    <Button
      {...props}
      variant={quiet ? "ghost" : props.variant}
      className={`hp-button ${quiet ? "hp-quiet" : ""} ${props.className || ""}`}
    >
      {children}
    </Button>
  );
}
function Avatar({ person, large }: { person: Candidate; large?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`hp-avatar ${large ? "hp-avatar-large" : ""}`}
      style={{ background: person.color }}
    >
      {person.initials}
    </span>
  );
}

export function CandidatePanel({
  initialScreen = "panel",
}: {
  initialScreen?: Screen;
}) {
  const [screen, setScreen] = useState<Screen>(initialScreen);
  const [people, setPeople] = useState<Candidate[]>(initialCandidates);
  const [selectedId, setSelectedId] = useState("anna");
  const [meetings, setMeetings] = useState<Meeting[]>(initialMeetings);
  const [favorites, setFavorites] = useState<string[]>([]);
  const [archived, setArchived] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [message, setMessage] = useState("");
  const [newIds, setNewIds] = useState<string[]>([]);
  const [media, setMedia] = useState<Record<string, string>>({});
  const mediaRef = useRef(media);
  mediaRef.current = media;
  const heading = useRef<HTMLElement>(null);
  useEffect(
    () => () => Object.values(mediaRef.current).forEach(URL.revokeObjectURL),
    [],
  );
  useEffect(() => {
    if (!message) return;
    const timeout = setTimeout(() => setMessage(""), 5000);
    return () => clearTimeout(timeout);
  }, [message]);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [screen]);
  const person = people.find((c) => c.id === selectedId) || people[0];
  const meeting = meetings.find((m) => m.candidateId === person.id);
  const visible = people.filter(
    (c) =>
      (!query ||
        `${c.name} ${c.role}`.toLowerCase().includes(query.toLowerCase())) &&
      (filter === "favorites"
        ? favorites.includes(c.id)
        : filter === "meetings"
          ? meetings.some((m) => m.candidateId === c.id)
          : filter === "archived"
            ? archived.includes(c.id)
            : !archived.includes(c.id)),
  );
  function navigate(next: Screen) {
    setScreen(next);
    setMessage("");
  }
  function select(id: string) {
    setSelectedId(id);
    setMobileDetail(true);
  }
  function schedule(id = person.id) {
    setSelectedId(id);
    navigate("schedule");
  }
  function add(added: Candidate[]) {
    setPeople((prev) => [...added, ...prev]);
    setNewIds(added.map((c) => c.id));
    setSelectedId(added[0].id);
    setFilter("all");
    setQuery("");
    navigate("panel");
    setMobileDetail(true);
    setMessage(
      added.length === 1
        ? "Кандидат добавлен"
        : `Добавлено кандидатов: ${added.length}`,
    );
  }
  function reset() {
    setPeople(initialCandidates);
    setSelectedId("anna");
    setMeetings(initialMeetings);
    setFavorites([]);
    setArchived([]);
    setQuery("");
    setFilter("all");
    setNewIds([]);
    setMobileDetail(false);
    Object.values(mediaRef.current).forEach(URL.revokeObjectURL);
    setMedia({});
    navigate(initialScreen);
  }
  return (
    <MotionConfig reducedMotion="user">
      <div className="hp-root">
        <div className="hp-app">
          <header className="hp-header">
            <button
              className="hp-brand"
              onClick={() => navigate("panel")}
              aria-label="HR Vision · панель кандидатов"
            >
              <svg
                width="27"
                height="24"
                viewBox="0 0 27 24"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M3 6h6l4 6-4 6H3l4-6-4-6Zm11 0h6l4 6-4 6h-6l4-6-4-6Z"
                  fill="currentColor"
                />
              </svg>
              <strong>HR Vision</strong>
            </button>
            <span className="hp-header-divider" />
            <span className="hp-company">Сфера</span>
            <Popover.Root>
              <Popover.Trigger
                className="hp-account"
                aria-label="Открыть аккаунт Ивана Петрова"
              >
                <span className="hp-account-avatar">ИП</span>
                <span>Иван Петров</span>
                <ChevronDown size={14} />
              </Popover.Trigger>
              <Popover.Portal>
                <Popover.Content
                  className="hp-popover hp-portal"
                  align="end"
                  sideOffset={12}
                >
                  <strong>Иван Петров</strong>
                  <p>Нанимающий менеджер · Сфера</p>
                  <a
                    href="https://hr-vision.158-160-179-53.sslip.io/iframe.html?id=hr-vision-product--start&viewMode=story"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Рабочий сервис <ArrowUpRight size={16} />
                  </a>
                </Popover.Content>
              </Popover.Portal>
            </Popover.Root>
          </header>
          <div className="hp-position-bar">
            <div>
              <span className="hp-context">Клиентский сервис</span>
              <h1
                ref={heading as React.RefObject<HTMLHeadingElement>}
                tabIndex={-1}
              >
                {position}
              </h1>
            </div>
            <Action variant="secondary" onClick={() => navigate("add")}>
              <Plus size={17} />
              Добавить кандидатов
            </Action>
          </div>
          <nav className="hp-product-nav" aria-label="Кандидаты и встречи">
            <button
              onClick={() => {
                navigate("panel");
                setMobileDetail(false);
              }}
              aria-current={
                screen === "panel" || screen === "add" ? "page" : undefined
              }
            >
              Кандидаты{" "}
              <span>
                {people.filter((c) => !archived.includes(c.id)).length}
              </span>
            </button>
            <button
              onClick={() => navigate("schedule")}
              aria-current={screen === "schedule" ? "page" : undefined}
            >
              Встречи <span>{meetings.length}</span>
            </button>
          </nav>
          <AnimatePresence mode="wait" initial={false}>
            <motion.main
              key={screen}
              initial={{ opacity: 0, y: 5 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -3 }}
              transition={{ duration: 0.15 }}
            >
              {screen === "panel" && (
                <div
                  className={`hp-panel ${mobileDetail ? "hp-show-detail" : ""}`}
                >
                  <aside
                    className="hp-candidates"
                    aria-label="Список кандидатов"
                  >
                    <div className="hp-search">
                      <Search size={17} />
                      <input
                        aria-label="Найти кандидата"
                        placeholder="Найти кандидата"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                      {query && (
                        <button
                          aria-label="Очистить поиск"
                          onClick={() => setQuery("")}
                        >
                          <X size={14} />
                        </button>
                      )}
                    </div>
                    <div className="hp-list-filter">
                      <select
                        aria-label="Фильтр кандидатов"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      >
                        <option value="all">Все кандидаты</option>
                        <option value="favorites">Избранные</option>
                        <option value="meetings">Со встречами</option>
                        <option value="archived">Отложенные</option>
                      </select>
                      <span>{visible.length}</span>
                    </div>
                    <div className="hp-people-list">
                      {visible.map((c) => {
                        const m = meetings.find((m) => m.candidateId === c.id);
                        return (
                          <button
                            key={c.id}
                            className={`hp-person-row ${c.id === person.id ? "is-selected" : ""}`}
                            aria-pressed={c.id === person.id}
                            onClick={() => select(c.id)}
                          >
                            <Avatar person={c} />
                            <span className="hp-person-copy">
                              <strong>{c.name}</strong>
                              <span>
                                {c.experience} · {c.city}
                              </span>
                              <span
                                className={`hp-row-status ${m ? "has-meeting" : ""}`}
                              >
                                {m ? (
                                  <>
                                    <CalendarDays size={12} />
                                    {dayLabel(m.day, true)} · {m.time}
                                  </>
                                ) : newIds.includes(c.id) ? (
                                  "Добавлен вами"
                                ) : (
                                  c.role
                                )}
                              </span>
                            </span>
                            {favorites.includes(c.id) && (
                              <Star
                                size={13}
                                fill="currentColor"
                                className="hp-star"
                              />
                            )}
                          </button>
                        );
                      })}
                      {!visible.length && (
                        <div className="hp-list-empty">
                          <Search size={23} />
                          <strong>Никого не нашли</strong>
                          <button
                            onClick={() => {
                              setQuery("");
                              setFilter("all");
                            }}
                          >
                            Сбросить фильтры
                          </button>
                        </div>
                      )}
                    </div>
                    <button
                      className="hp-add-row"
                      onClick={() => navigate("add")}
                    >
                      <Plus size={17} />
                      Добавить кандидатов
                    </button>
                  </aside>
                  <section
                    className="hp-candidate-detail"
                    aria-label={`Карточка: ${person.name}`}
                  >
                    <button
                      className="hp-mobile-back hp-text-button"
                      onClick={() => setMobileDetail(false)}
                    >
                      <ArrowLeft size={16} />
                      Все кандидаты
                    </button>
                    <div className="hp-person-heading">
                      <div className="hp-person-identity">
                        <Avatar person={person} large />
                        <div>
                          <h2>{person.name}</h2>
                          <p>{person.role}</p>
                          <div className="hp-meta">
                            <span>{person.city}</span>
                            <span>{person.experience} опыта</span>
                            <span>{person.salary}</span>
                          </div>
                        </div>
                      </div>
                      <div className="hp-heading-actions">
                        <button
                          className={`hp-icon-button ${favorites.includes(person.id) ? "is-favorite" : ""}`}
                          aria-label={
                            favorites.includes(person.id)
                              ? "Убрать из избранного"
                              : "В избранное"
                          }
                          aria-pressed={favorites.includes(person.id)}
                          onClick={() =>
                            setFavorites((a) =>
                              a.includes(person.id)
                                ? a.filter((id) => id !== person.id)
                                : [...a, person.id],
                            )
                          }
                        >
                          <Star
                            size={20}
                            fill={
                              favorites.includes(person.id)
                                ? "currentColor"
                                : "none"
                            }
                          />
                        </button>
                        <DropdownMenu.Root>
                          <DropdownMenu.Trigger
                            className="hp-icon-button"
                            aria-label="Действия с кандидатом"
                          >
                            <MoreHorizontal size={21} />
                          </DropdownMenu.Trigger>
                          <DropdownMenu.Portal>
                            <DropdownMenu.Content
                              className="hp-menu hp-portal"
                              align="end"
                              sideOffset={8}
                            >
                              <DropdownMenu.Item
                                onSelect={() => {
                                  setArchived((prev) =>
                                    prev.includes(person.id)
                                      ? prev.filter((id) => id !== person.id)
                                      : [...prev, person.id],
                                  );
                                  setMessage(
                                    archived.includes(person.id)
                                      ? "Кандидат возвращён в работу"
                                      : "Кандидат в отложенных",
                                  );
                                }}
                              >
                                {archived.includes(person.id)
                                  ? "Вернуть в работу"
                                  : "Отложить кандидата"}
                              </DropdownMenu.Item>
                            </DropdownMenu.Content>
                          </DropdownMenu.Portal>
                        </DropdownMenu.Root>
                      </div>
                    </div>
                    <Tabs.Root
                      defaultValue="interview"
                      key={person.id}
                      className="hp-detail-tabs"
                    >
                      <Tabs.List
                        className="hp-tab-list"
                        aria-label="Материалы кандидата"
                      >
                        <Tabs.Trigger value="interview">Интервью</Tabs.Trigger>
                        <Tabs.Trigger value="profile">Резюме</Tabs.Trigger>
                      </Tabs.List>
                      <Tabs.Content
                        value="interview"
                        className="hp-tab-content"
                      >
                        <Interview
                          person={person}
                          media={media[person.id]}
                          setMedia={(url) =>
                            setMedia((prev) => {
                              if (prev[person.id])
                                URL.revokeObjectURL(prev[person.id]);
                              return { ...prev, [person.id]: url };
                            })
                          }
                        />
                      </Tabs.Content>
                      <Tabs.Content value="profile" className="hp-resume">
                        <div>
                          <h3>{person.role}</h3>
                          <p>{person.summary}</p>
                          <dl>
                            <div>
                              <dt>Опыт</dt>
                              <dd>{person.experience}</dd>
                            </div>
                            <div>
                              <dt>Город</dt>
                              <dd>{person.city}</dd>
                            </div>
                            <div>
                              <dt>Ожидания</dt>
                              <dd>{person.salary}</dd>
                            </div>
                            {person.email && (
                              <div>
                                <dt>Почта</dt>
                                <dd>{person.email}</dd>
                              </div>
                            )}
                          </dl>
                          {person.source && (
                            <p className="hp-attached">
                              <FileText size={16} />
                              {person.source}
                            </p>
                          )}
                          {person.note && <p>{person.note}</p>}
                        </div>
                        <div className="hp-resume-note">
                          <h3>На встрече</h3>
                          <p>{person.question}</p>
                        </div>
                      </Tabs.Content>
                    </Tabs.Root>
                    <footer className="hp-decision">
                      <div>
                        {meeting ? (
                          <>
                            <span
                              className={`hp-state-dot ${meeting.status}`}
                            />
                            <strong>
                              {dayLabel(meeting.day)} · {meeting.time}
                            </strong>
                            <span>
                              {meeting.status === "confirmed"
                                ? "Встреча подтверждена"
                                : "Ждём подтверждения"}
                            </span>
                          </>
                        ) : (
                          <>
                            <span className="hp-state-dot" />
                            <span>
                              {archived.includes(person.id)
                                ? "Кандидат отложен"
                                : "На рассмотрении"}
                            </span>
                          </>
                        )}
                      </div>
                      <Action onClick={() => schedule()}>
                        {meeting ? "Открыть встречу" : "Назначить встречу"}
                        <ArrowRight size={17} />
                      </Action>
                    </footer>
                  </section>
                </div>
              )}
              {screen === "add" && (
                <AddCandidates onBack={() => navigate("panel")} onAdd={add} />
              )}
              {screen === "schedule" && (
                <Schedule
                  key={selectedId}
                  people={people}
                  person={person}
                  meetings={meetings}
                  onPerson={(id) => setSelectedId(id)}
                  onBack={() => navigate("panel")}
                  onBook={(m) => {
                    setMeetings((prev) => [
                      ...prev.filter((x) => x.candidateId !== m.candidateId),
                      m,
                    ]);
                  }}
                  onCancel={(id) => {
                    setMeetings((prev) =>
                      prev.filter((m) => m.candidateId !== id),
                    );
                    setMessage("Встреча отменена. Время снова свободно.");
                  }}
                />
              )}
            </motion.main>
          </AnimatePresence>
          <AnimatePresence>
            {message && (
              <motion.div
                className="hp-toast"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 6 }}
              >
                <Check size={17} />
                <span role="status">{message}</span>
                <button
                  aria-label="Закрыть уведомление"
                  onClick={() => setMessage("")}
                >
                  <X size={15} />
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        <div className="hp-lab" aria-label="Просмотр макетов">
          <span className="hp-lab-label">Макет</span>
          {(["panel", "add", "schedule"] as Screen[]).map((s, i) => (
            <button
              key={s}
              aria-pressed={screen === s}
              onClick={() => navigate(s)}
            >
              <span>0{i + 1}</span>
              {screenNames[s]}
            </button>
          ))}
          <Popover.Root>
            <Popover.Trigger className="hp-lab-icon" aria-label="О макетах">
              <Info size={16} />
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content
                className="hp-popover hp-portal"
                side="top"
                align="end"
                sideOffset={14}
              >
                <strong>Три связанных макета</strong>
                <p>
                  Вымышленные кандидаты. Изменения действуют в этом просмотре.
                  Импорт, приглашения и календарь не связаны с рабочим сервисом.
                </p>
                <Action quiet onClick={reset}>
                  <RotateCcw size={14} />
                  Начать заново
                </Action>
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
        </div>
      </div>
    </MotionConfig>
  );
}

function Interview({
  person,
  media,
  setMedia,
}: {
  person: Candidate;
  media?: string;
  setMedia: (src: string) => void;
}) {
  const [chapter, setChapter] = useState(0);
  const [short, setShort] = useState(true);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(1720);
  const [transcript, setTranscript] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const timeName = useId();
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    if (expanded) window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [expanded]);
  function seek(seconds: number, i?: number) {
    setPosition(seconds);
    if (i !== undefined) setChapter(i);
    if (video.current && media && seconds < video.current.duration)
      video.current.currentTime = seconds;
  }
  function chooseFile(f?: File) {
    if (!f) return;
    if (!f.type.startsWith("video/")) {
      setMediaError("Выберите видео в формате MP4, MOV или WebM.");
      return;
    }
    setMediaError("");
    setMedia(URL.createObjectURL(f));
    setPlaying(false);
    setPosition(0);
  }
  function play() {
    if (!media) {
      file.current?.click();
      return;
    }
    if (video.current?.paused)
      video.current
        .play()
        .catch(() =>
          setMediaError("Не удалось открыть запись. Попробуйте MP4 или WebM."),
        );
    else video.current?.pause();
  }
  if (!initialCandidates.some((c) => c.id === person.id))
    return (
      <div className="hp-new-interview">
        <span className="hp-folder">
          <Video size={24} />
        </span>
        <h3>Интервью ещё не проведено</h3>
        <p>Начните со встречи. Запись и разбор будут здесь после интервью.</p>
      </div>
    );
  const active = chapters[chapter];
  return (
    <div className="hp-interview-grid">
      <div className="hp-video-column">
        <div className="hp-section-line">
          <h3>Интервью с рекрутером</h3>
          <div className="hp-segment" role="group" aria-label="Длина интервью">
            <button
              aria-pressed={short}
              onClick={() => {
                setShort(true);
                seek(chapters[0].time, 0);
              }}
            >
              Коротко
            </button>
            <button
              aria-pressed={!short}
              onClick={() => {
                setShort(false);
                seek(0);
              }}
            >
              Целиком
            </button>
          </div>
        </div>
        <div
          className={`hp-video-wrap ${expanded ? "hp-video-expanded" : ""}`}
          ref={player}
        >
          <div className="hp-video-stage">
            {media ? (
              <video
                ref={video}
                src={media}
                playsInline
                onLoadedMetadata={(e) => {
                  setDuration(e.currentTarget.duration);
                  setPosition(0);
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onTimeUpdate={(e) => {
                  setPosition(e.currentTarget.currentTime);
                  if (
                    short &&
                    e.currentTarget.currentTime >= chapters[chapter].end
                  ) {
                    if (
                      chapter < chapters.length - 1 &&
                      chapters[chapter + 1].time < e.currentTarget.duration
                    )
                      seek(chapters[chapter + 1].time, chapter + 1);
                    else e.currentTarget.pause();
                  }
                }}
                onError={() =>
                  setMediaError(
                    "Формат записи не поддерживается. Выберите другое видео.",
                  )
                }
                aria-label={`Запись интервью: ${person.name}`}
              />
            ) : (
              <div className="hp-video-placeholder">
                <span className="hp-recording-label">
                  <span />
                  Видеозапись интервью
                </span>
                <span className="hp-video-initials">{person.initials}</span>
                <button
                  className="hp-player-upload"
                  onClick={() => file.current?.click()}
                >
                  <Upload size={18} />
                  Выбрать запись
                </button>
                <span className="hp-video-caption">
                  Для просмотра макета можно загрузить своё видео
                </span>
              </div>
            )}
            {media && !playing && (
              <button
                className="hp-big-play"
                aria-label="Воспроизвести запись"
                onClick={play}
              >
                <Play size={25} fill="currentColor" />
              </button>
            )}
            {expanded && (
              <button
                className="hp-close-player"
                aria-label="Свернуть видео"
                onClick={() => setExpanded(false)}
              >
                <X size={20} />
              </button>
            )}
          </div>
          <div className="hp-player-controls">
            <button
              className="hp-icon-button"
              aria-label={
                playing
                  ? "Приостановить видео"
                  : media
                    ? "Воспроизвести видео"
                    : "Выбрать видеозапись"
              }
              onClick={play}
            >
              {playing ? <Pause size={17} /> : <Play size={17} />}
            </button>
            <span id={timeName}>{mmss(position)}</span>
            <div className="hp-timeline">
              <input
                type="range"
                aria-label="Позиция видео"
                aria-describedby={timeName}
                min={0}
                max={duration}
                step={1}
                value={position}
                onChange={(e) => seek(Number(e.target.value))}
              />
              {chapters
                .filter((c) => c.time < duration)
                .map((c, i) => (
                  <button
                    className={`hp-timeline-mark ${chapter === i ? "is-active" : ""}`}
                    key={c.time}
                    style={{ left: `${(c.time / duration) * 100}%` }}
                    aria-label={`${c.title}, ${mmss(c.time)}`}
                    onClick={() => seek(c.time, i)}
                  />
                ))}
            </div>
            <span className="hp-duration">{mmss(duration)}</span>
            <button
              className="hp-icon-button"
              aria-label={expanded ? "Свернуть видео" : "Развернуть видео"}
              onClick={() => setExpanded((v) => !v)}
            >
              <Maximize2 size={17} />
            </button>
          </div>
        </div>
        <input
          ref={file}
          type="file"
          accept="video/*"
          className="hp-sr-only"
          tabIndex={-1}
          aria-label="Файл видеозаписи"
          onChange={(e) => chooseFile(e.target.files?.[0])}
        />
        {mediaError && (
          <p className="hp-error" role="alert">
            {mediaError}
          </p>
        )}
        <div className="hp-transcript-line">
          <span>{short ? "4 ключевых фрагмента" : "Полное интервью"}</span>
          <button
            onClick={() => setTranscript((v) => !v)}
            aria-expanded={transcript}
          >
            <FileText size={14} />
            {transcript ? "Скрыть расшифровку" : "Расшифровка"}
          </button>
          {media && (
            <button onClick={() => file.current?.click()}>Другая запись</button>
          )}
        </div>
        <div className="hp-excerpt">
          <button className="hp-timestamp" onClick={() => seek(active.time)}>
            {mmss(active.time)}
            <Play size={11} />
          </button>
          <blockquote>{active.quote}</blockquote>
        </div>
        {transcript && (
          <div className="hp-full-transcript">
            {chapters.map((c, i) => (
              <button key={c.time} onClick={() => seek(c.time, i)}>
                <span>{mmss(c.time)}</span>
                <p>{c.quote}</p>
              </button>
            ))}
          </div>
        )}
      </div>
      <aside className="hp-evidence-column">
        <div className="hp-section-line">
          <h3>Ключевые моменты</h3>
          <ListVideo size={17} />
        </div>
        <div className="hp-chapters">
          {chapters.map((c, i) => (
            <button
              key={c.time}
              aria-pressed={chapter === i}
              onClick={() => seek(c.time, i)}
            >
              <span className="hp-chapter-index">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span>
                <strong>{c.title}</strong>
                <small>{mmss(c.time)}</small>
              </span>
              <Play size={13} fill={chapter === i ? "currentColor" : "none"} />
            </button>
          ))}
        </div>
        <div className="hp-recruiter">
          <span className="hp-recruiter-label">Мнение рекрутера</span>
          <h3>{person.strength}</h3>
          <p>{person.summary}</p>
          <div>
            <span>На встрече</span>
            <p>{person.question}</p>
          </div>
        </div>
      </aside>
    </div>
  );
}

type Draft = {
  key: string;
  name: string;
  file?: File;
  source: string;
  email: string;
  role: string;
  note: string;
};
function draft(): Draft {
  return {
    key: crypto.randomUUID(),
    name: "",
    source: "",
    email: "",
    role: "",
    note: "",
  };
}
function AddCandidates({
  onBack,
  onAdd,
}: {
  onBack: () => void;
  onAdd: (people: Candidate[]) => void;
}) {
  const [mode, setMode] = useState<"files" | "link" | "manual">("files");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [manual, setManual] = useState<Draft>(draft);
  const [link, setLink] = useState("");
  const [error, setError] = useState("");
  const [drag, setDrag] = useState(false);
  const upload = useRef<HTMLInputElement>(null);
  function change(key: string, field: keyof Draft, value: string) {
    setDrafts((list) =>
      list.map((d) => (d.key === key ? { ...d, [field]: value } : d)),
    );
  }
  function files(list: File[]) {
    const supported = list.filter((f) => /\.(pdf|docx?|rtf)$/i.test(f.name));
    setError(
      supported.length !== list.length
        ? "Добавьте резюме в PDF, DOC, DOCX или RTF."
        : "",
    );
    setDrafts((prev) => [
      ...prev,
      ...supported
        .filter(
          (f) =>
            !prev.some(
              (d) => d.file?.name === f.name && d.file.size === f.size,
            ),
        )
        .map((file) => ({
          ...draft(),
          file,
          source: file.name,
          name: file.name
            .replace(/\.(pdf|docx?|rtf)$/i, "")
            .replace(/[_-]/g, " "),
        })),
    ]);
  }
  function addLink() {
    try {
      const url = new URL(link);
      if (!["http:", "https:"].includes(url.protocol)) throw Error();
      setDrafts((prev) => [...prev, { ...draft(), source: url.href }]);
      setLink("");
      setError("");
    } catch {
      setError("Вставьте полную ссылку на резюме, начиная с https://");
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    const list = mode === "manual" ? [manual] : drafts;
    if (!list.length) {
      setError("Добавьте резюме или заполните имя вручную.");
      return;
    }
    if (list.some((d) => !d.name.trim())) {
      setError("Укажите имя каждого кандидата.");
      return;
    }
    if (
      list.some((d) => d.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(d.email))
    ) {
      setError("Проверьте адрес электронной почты.");
      return;
    }
    onAdd(
      list.map((d) => ({
        id: d.key,
        name: d.name.trim(),
        initials: d.name
          .trim()
          .split(/\s+/)
          .slice(0, 2)
          .map((p) => p[0])
          .join("")
          .toUpperCase(),
        role: d.role || position,
        city: "Город не указан",
        experience: "Не указан",
        salary: "По договорённости",
        email: d.email || undefined,
        source: d.source || undefined,
        note: d.note || undefined,
        color: "#e9eafb",
        summary:
          d.note || "Резюме добавлено. Интервью и заключение пока не готовы.",
        strength: "Новый кандидат",
        question: "Обсудить опыт и ожидания от новой работы.",
        new: true,
      })),
    );
  }
  const count =
    mode === "manual" ? (manual.name.trim() ? 1 : 0) : drafts.length;
  return (
    <section className="hp-add-screen">
      <button className="hp-text-button" onClick={onBack}>
        <ArrowLeft size={16} />К кандидатам
      </button>
      <div className="hp-screen-heading">
        <h2>Добавить кандидатов</h2>
        <span>{position}</span>
      </div>
      <form onSubmit={submit} noValidate>
        <div className="hp-add-layout">
          <div className="hp-add-main">
            <div
              className="hp-methods"
              role="group"
              aria-label="Способ добавления"
            >
              {(
                [
                  ["files", "Резюме", Upload],
                  ["link", "По ссылке", Link2],
                  ["manual", "Вручную", UsersRound],
                ] as const
              ).map(([id, label, Icon]) => (
                <button
                  type="button"
                  key={id}
                  aria-pressed={mode === id}
                  onClick={() => {
                    setMode(id);
                    setError("");
                  }}
                >
                  <Icon size={17} />
                  {label}
                </button>
              ))}
            </div>
            {mode === "files" && (
              <div
                className={`hp-dropzone ${drag ? "is-dragging" : ""}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDrag(true);
                }}
                onDragLeave={() => setDrag(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDrag(false);
                  files(Array.from(e.dataTransfer.files));
                }}
              >
                <span className="hp-document-icon">
                  <FileText size={29} strokeWidth={1.5} />
                  <span>
                    <Plus size={12} />
                  </span>
                </span>
                <h3>Перетащите резюме сюда</h3>
                <p>Можно сразу несколько · PDF, DOCX, DOC</p>
                <Action
                  type="button"
                  variant="outline"
                  onClick={() => upload.current?.click()}
                >
                  Выбрать файлы
                </Action>
                <input
                  type="file"
                  ref={upload}
                  multiple
                  accept=".pdf,.doc,.docx,.rtf"
                  className="hp-sr-only"
                  tabIndex={-1}
                  aria-label="Загрузить резюме"
                  onChange={(e) => {
                    files(Array.from(e.target.files || []));
                    e.target.value = "";
                  }}
                />
              </div>
            )}
            {mode === "link" && (
              <div className="hp-link-entry">
                <label htmlFor="hp-resume-link">Ссылка на резюме</label>
                <div>
                  <input
                    id="hp-resume-link"
                    type="url"
                    placeholder="https://hh.ru/resume/…"
                    value={link}
                    onChange={(e) => setLink(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addLink();
                      }
                    }}
                  />
                  <Action
                    type="button"
                    variant="secondary"
                    onClick={addLink}
                    aria-label="Добавить ссылку"
                  >
                    <Plus size={18} />
                  </Action>
                </div>
                <p>Сохраним ссылку в карточке. Имя можно указать ниже.</p>
              </div>
            )}
            {mode === "manual" ? (
              <div className="hp-manual-fields">
                <label>
                  Имя и фамилия
                  <input
                    autoComplete="off"
                    placeholder="Например, Ольга Смирнова"
                    value={manual.name}
                    onChange={(e) =>
                      setManual({ ...manual, name: e.target.value })
                    }
                    required
                  />
                </label>
                <label>
                  Почта <span>необязательно</span>
                  <input
                    type="email"
                    placeholder="name@example.com"
                    value={manual.email}
                    onChange={(e) =>
                      setManual({ ...manual, email: e.target.value })
                    }
                  />
                </label>
                <details>
                  <summary>
                    Должность и комментарий
                    <ChevronDown size={15} />
                  </summary>
                  <label>
                    Текущая должность
                    <input
                      placeholder="Менеджер по работе с клиентами"
                      value={manual.role}
                      onChange={(e) =>
                        setManual({ ...manual, role: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Комментарий
                    <textarea
                      rows={3}
                      placeholder="Что важно знать о кандидате"
                      value={manual.note}
                      onChange={(e) =>
                        setManual({ ...manual, note: e.target.value })
                      }
                    />
                  </label>
                </details>
              </div>
            ) : (
              drafts.length > 0 && (
                <div className="hp-drafts">
                  <div className="hp-draft-heading">
                    <h3>К добавлению</h3>
                    <span>{drafts.length}</span>
                  </div>
                  {drafts.map((d) => (
                    <div key={d.key} className="hp-draft-row">
                      <span className="hp-draft-file">
                        <FileText size={21} />
                      </span>
                      <div>
                        <input
                          aria-label={`Имя кандидата для ${d.source}`}
                          placeholder="Имя и фамилия"
                          value={d.name}
                          onChange={(e) =>
                            change(d.key, "name", e.target.value)
                          }
                          required
                        />
                        <span>{d.file ? d.file.name : d.source}</span>
                      </div>
                      <button
                        type="button"
                        className="hp-icon-button"
                        aria-label={`Убрать ${d.source}`}
                        onClick={() =>
                          setDrafts((a) => a.filter((x) => x.key !== d.key))
                        }
                      >
                        <X size={17} />
                      </button>
                    </div>
                  ))}
                  <p className="hp-import-note">
                    Проверьте имена перед добавлением.
                  </p>
                </div>
              )
            )}
          </div>
          <aside className="hp-add-side">
            <span className="hp-folder">
              <FolderOpen size={24} strokeWidth={1.5} />
            </span>
            <span>В эту подборку</span>
            <h3>{position}</h3>
            <p>Сфера · Клиентский сервис</p>
            <div className="hp-add-side-bottom">
              <UsersRound size={17} />
              <span>
                {count
                  ? `Кандидатов к добавлению: ${count}`
                  : "Кандидаты появятся в панели"}
              </span>
            </div>
          </aside>
        </div>
        {error && (
          <p className="hp-error" role="alert">
            {error}
          </p>
        )}
        <div className="hp-form-footer">
          <Action type="button" quiet onClick={onBack}>
            Отмена
          </Action>
          <Action type="submit">
            {count > 1 ? `Добавить ${count} кандидатов` : "Добавить кандидата"}
            <ArrowRight size={17} />
          </Action>
        </div>
      </form>
    </section>
  );
}

function Schedule({
  people,
  person,
  meetings,
  onPerson,
  onBack,
  onBook,
  onCancel,
}: {
  people: Candidate[];
  person: Candidate;
  meetings: Meeting[];
  onPerson: (id: string) => void;
  onBack: () => void;
  onBook: (m: Meeting) => void;
  onCancel: (id: string) => void;
}) {
  const existing = meetings.find((m) => m.candidateId === person.id);
  const [editing, setEditing] = useState(false);
  const [offset, setOffset] = useState(0);
  const [day, setDay] = useState("2026-10-08");
  const [time, setTime] = useState("");
  const [duration, setDuration] = useState(30);
  const [error, setError] = useState("");
  const [cancelOpen, setCancelOpen] = useState(false);
  const days = weekDays(offset);
  const slots = freeSlots(
    person.id,
    day,
    duration,
    meetings,
    editing ? person.id : undefined,
  );
  const active = existing && !editing;
  function resetSlot() {
    setTime("");
    setError("");
  }
  function book() {
    if (!time || !slots.includes(time)) {
      setError("Выберите свободное время.");
      return;
    }
    onBook({ candidateId: person.id, day, time, duration, status: "pending" });
    setEditing(false);
  }
  return (
    <section className="hp-schedule-screen">
      <button className="hp-text-button" onClick={onBack}>
        <ArrowLeft size={16} />К кандидатам
      </button>
      <div className="hp-screen-heading">
        <h2>{active ? "Встреча" : "Назначить встречу"}</h2>
      </div>
      <div className="hp-schedule-layout">
        <aside className="hp-meeting-person">
          <div className="hp-meeting-person-info">
            <Avatar person={person} large />
            <h3>{person.name}</h3>
            <p>{person.role}</p>
            <select
              aria-label="Выбрать кандидата для встречи"
              value={person.id}
              onChange={(e) => onPerson(e.target.value)}
            >
              {people.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div className="hp-meeting-facts">
            <span>
              <Video size={18} />
              На нашей платформе
            </span>
            <span>
              <Clock3 size={18} />
              {active ? existing.duration : duration} минут
            </span>
            <span>MSK · Москва, UTC+3</span>
          </div>
          <div className="hp-other-meetings">
            <div className="hp-section-line">
              <h3>Другие встречи</h3>
              <span>
                {meetings.filter((m) => m.candidateId !== person.id).length}
              </span>
            </div>
            {meetings
              .filter((m) => m.candidateId !== person.id)
              .map((m) => {
                const c = people.find((c) => c.id === m.candidateId)!;
                return (
                  <button key={c.id} onClick={() => onPerson(c.id)}>
                    <span className="hp-meeting-day">
                      <strong>{m.day.slice(-2)}</strong>
                      <small>окт</small>
                    </span>
                    <span>
                      <strong>{c.name}</strong>
                      <small>
                        {m.time} ·{" "}
                        {m.status === "confirmed"
                          ? "Подтверждена"
                          : "Ждём ответа"}
                      </small>
                    </span>
                    <ChevronRight size={15} />
                  </button>
                );
              })}
            {!meetings.some((m) => m.candidateId !== person.id) && (
              <p>Пока нет</p>
            )}
          </div>
        </aside>
        <div className="hp-calendar-content">
          {active ? (
            <div className="hp-meeting-result">
              <span
                className={`hp-result-icon ${existing.status === "confirmed" ? "is-confirmed" : ""}`}
              >
                {existing.status === "confirmed" ? (
                  <Check size={27} />
                ) : (
                  <Clock3 size={27} />
                )}
              </span>
              <h3>
                {existing.status === "confirmed"
                  ? "Встреча подтверждена"
                  : "Время предложено"}
              </h3>
              <p>
                {existing.status === "confirmed"
                  ? "Кандидат подтвердил время встречи."
                  : "Встреча состоится после подтверждения кандидатом."}
              </p>
              <div className="hp-date-ticket">
                <CalendarDays size={23} />
                <div>
                  <strong>
                    {dayLabel(existing.day)} в {existing.time}
                  </strong>
                  <span>{existing.duration} минут · МСК</span>
                </div>
                <span className="hp-status-pill">
                  {existing.status === "confirmed"
                    ? "Подтверждена"
                    : "Ждём ответа"}
                </span>
              </div>
              <div className="hp-result-actions">
                <Action
                  variant="secondary"
                  onClick={() => {
                    setEditing(true);
                    setDay(existing.day);
                    setDuration(existing.duration);
                    setTime("");
                    const d = Math.floor(
                      (new Date(existing.day + "T12:00:00Z").getTime() -
                        new Date("2026-10-08T12:00:00Z").getTime()) /
                        86400000,
                    );
                    setOffset(Math.max(0, Math.floor(d / 7)));
                  }}
                >
                  Изменить время
                </Action>
                <button
                  className="hp-text-button hp-muted"
                  onClick={() => setCancelOpen(true)}
                >
                  Отменить встречу
                </button>
              </div>
              <div className="hp-next-hint">
                <span className="hp-state-dot" />
                <p>
                  {existing.status === "confirmed"
                    ? "В рабочем сервисе здесь появится вход в видеовстречу."
                    : "После подтверждения здесь появится вход в видеовстречу."}
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="hp-calendar-toolbar">
                <div>
                  <h3>Свободное время</h3>
                  <span>Октябрь 2026</span>
                </div>
                <div className="hp-week-arrows">
                  <button
                    className="hp-icon-button"
                    aria-label="Предыдущая неделя"
                    disabled={offset === 0}
                    onClick={() => {
                      setOffset((n) => n - 1);
                      setDay(weekDays(offset - 1)[0]);
                      resetSlot();
                    }}
                  >
                    <ChevronLeft size={19} />
                  </button>
                  <button
                    className="hp-icon-button"
                    aria-label="Следующая неделя"
                    disabled={offset === 3}
                    onClick={() => {
                      setOffset((n) => n + 1);
                      setDay(weekDays(offset + 1)[0]);
                      resetSlot();
                    }}
                  >
                    <ChevronRight size={19} />
                  </button>
                </div>
              </div>
              <div className="hp-days" role="group" aria-label="День встречи">
                {days.map((d) => {
                  const available =
                    freeSlots(
                      person.id,
                      d,
                      duration,
                      meetings,
                      editing ? person.id : undefined,
                    ).length > 0;
                  return (
                    <button
                      key={d}
                      aria-label={dayLabel(d)}
                      aria-pressed={day === d}
                      disabled={!available}
                      onClick={() => {
                        setDay(d);
                        resetSlot();
                      }}
                    >
                      <span>{weekday(d)}</span>
                      <strong>{Number(d.slice(-2))}</strong>
                      <i />
                    </button>
                  );
                })}
              </div>
              <div className="hp-time-heading">
                <h3>{dayLabel(day)}</h3>
                <div
                  className="hp-segment"
                  role="group"
                  aria-label="Длительность встречи"
                >
                  {[30, 45, 60].map((d) => (
                    <button
                      key={d}
                      aria-pressed={duration === d}
                      onClick={() => {
                        setDuration(d);
                        resetSlot();
                      }}
                    >
                      {d} мин
                    </button>
                  ))}
                </div>
              </div>
              <div
                className="hp-time-grid"
                role="group"
                aria-label="Свободные слоты"
              >
                {slots.map((t) => (
                  <button
                    key={t}
                    aria-pressed={time === t}
                    onClick={() => {
                      setTime(t);
                      setError("");
                    }}
                  >
                    {t}
                    {time === t && <Check size={14} />}
                  </button>
                ))}
                {slots.length === 0 && (
                  <p>
                    В этот день нет свободного времени. Выберите другой день.
                  </p>
                )}
              </div>
              <div className="hp-slot-summary">
                <span>
                  {time ? (
                    <>
                      <CalendarDays size={17} />
                      {dayLabel(day)} · {time} МСК
                    </>
                  ) : (
                    "Выберите удобное время"
                  )}
                </span>
                {time && <small>{duration} минут</small>}
              </div>
              <p className="hp-confirm-note">
                Кандидат подтвердит время перед встречей.
              </p>
              {error && (
                <p className="hp-error" role="alert">
                  {error}
                </p>
              )}
              <div className="hp-book-footer">
                {editing && (
                  <Action quiet onClick={() => setEditing(false)}>
                    Отмена
                  </Action>
                )}
                <Action disabled={!time} onClick={book}>
                  Предложить время
                  <ArrowRight size={17} />
                </Action>
              </div>
            </>
          )}
        </div>
      </div>
      <Dialog.Root open={cancelOpen} onOpenChange={setCancelOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="hp-dialog-overlay" />
          <Dialog.Content className="hp-dialog hp-portal">
            <Dialog.Title>Отменить встречу?</Dialog.Title>
            <Dialog.Description>
              Время освободится. Позже можно будет предложить новый слот.
            </Dialog.Description>
            <div>
              <Dialog.Close asChild>
                <Action variant="outline">Оставить встречу</Action>
              </Dialog.Close>
              <Action
                onClick={() => {
                  onCancel(person.id);
                  setCancelOpen(false);
                  resetSlot();
                }}
              >
                Отменить встречу
              </Action>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
