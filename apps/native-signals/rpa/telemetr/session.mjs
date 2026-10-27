// Only the collector's own Telemetr login is persisted. Never import another
// browser's profile, or save Yandex/OAuth cookies from the sign-in flow.
export function telemetrSessionCookies(cookies) {
  if (!Array.isArray(cookies)) throw new Error('Некорректная сессия браузера.');
  const now=Date.now()/1000;
  return cookies.filter(c=>c && ['telemetr.me','.telemetr.me','www.telemetr.me'].includes(c.domain))
    .filter(c=>typeof c.name==='string' && typeof c.value==='string' && typeof c.path==='string' && c.path.startsWith('/')
      && Number.isFinite(c.expires) && (c.expires===-1 || c.expires>now))
    .map(c=>({name:c.name,value:c.value,domain:c.domain,path:c.path,expires:c.expires,httpOnly:!!c.httpOnly,
      secure:true,sameSite:['Strict','Lax','None'].includes(c.sameSite)?c.sameSite:'Lax'}));
}
