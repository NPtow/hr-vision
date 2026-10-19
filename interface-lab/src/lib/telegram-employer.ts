/** The SDK is loaded only from the explicit public employer Mini App entry. */
type Insets = { top?: number; right?: number; bottom?: number; left?: number };
export type TelegramApp = {
  initData: string; platform?: string; ready(): void; expand(): void; viewportStableHeight?: number;
  safeAreaInset?: Insets; contentSafeAreaInset?: Insets;
  onEvent?(event: string, handler: () => void): void;
  offEvent?(event: string, handler: () => void): void;
  openTelegramLink?(url: string): void;
};
declare global { interface Window { Telegram?: { WebApp?: TelegramApp } } }
let sdk: Promise<TelegramApp | undefined> | undefined;
export function isEmployerMiniApp(search = location.search) {
  return new URLSearchParams(search).get('miniapp') === 'employer';
}
export function loadEmployerTelegram(): Promise<TelegramApp | undefined> {
  if (!isEmployerMiniApp()) return Promise.resolve(undefined);
  if (window.Telegram?.WebApp) return Promise.resolve(window.Telegram.WebApp);
  if (!sdk) sdk = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    const timer = window.setTimeout(() => { script.remove(); sdk = undefined; reject(new Error('Telegram не загрузился. Можно войти по почте или открыть кабинет кнопкой бота ещё раз.')); }, 12000);
    script.src = 'https://telegram.org/js/telegram-web-app.js'; script.async = true;
    script.onload = () => { clearTimeout(timer); resolve(window.Telegram?.WebApp); };
    script.onerror = () => { clearTimeout(timer); script.remove(); sdk = undefined; reject(new Error('Не удалось загрузить Telegram. Войдите по почте или откройте кабинет ещё раз.')); };
    document.head.appendChild(script);
  });
  return sdk;
}
export function prepareEmployerTelegram(app: TelegramApp): () => void {
  app.ready(); app.expand();
  // Telegram's launch fragment is SDK input only, never application persistence.
  const url = new URL(location.href);
  const hash = new URLSearchParams(url.hash.slice(1));
  for (const key of [...hash.keys()]) if (key.startsWith('tgWebApp')) hash.delete(key);
  url.hash = hash.toString(); history.replaceState(history.state, '', url);
  const root = document.documentElement;
  const events = ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged'];
  const update = () => {
    root.dataset.employerMiniapp = 'true';
    root.style.setProperty('--employer-viewport', `${app.viewportStableHeight || window.innerHeight}px`);
    for (const side of ['top', 'right', 'bottom', 'left'] as const) {
      const value = Math.max(app.safeAreaInset?.[side] || 0, app.contentSafeAreaInset?.[side] || 0);
      root.style.setProperty(`--employer-safe-${side}`, `${value}px`);
    }
  };
  update(); events.forEach(event => app.onEvent?.(event, update));
  return () => {
    events.forEach(event => app.offEvent?.(event, update));
    delete root.dataset.employerMiniapp;
    root.style.removeProperty('--employer-viewport');
    for (const side of ['top', 'right', 'bottom', 'left']) root.style.removeProperty(`--employer-safe-${side}`);
  };
}

/** A failed Telegram exchange must never fall through to a different cookie identity. */
export function restoreEmployerIdentity<T>(app: TelegramApp | undefined, browser: () => Promise<T>, exchange: (initData: string) => Promise<T>): Promise<T> {
  if (app?.platform && app.platform !== 'unknown' && !app.initData) return Promise.reject(new Error('Нет данных для входа из Telegram. Откройте кабинет кнопкой бота ещё раз или войдите по почте.'));
  return app?.initData ? exchange(app.initData) : browser();
}
