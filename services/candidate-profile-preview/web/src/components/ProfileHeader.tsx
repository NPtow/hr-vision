const text = (value: unknown): string => typeof value === 'string' ? value.trim() : '';

export function ProfileHeader({ candidate }: { candidate: Record<string, any>; readiness?: unknown }) {
  const name = text(candidate.name) || 'Имя не указано';
  const role = text(candidate.desiredRole);
  const city = text(candidate.city);
  const initials = text(candidate.name).split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toLocaleUpperCase('ru') || '—';
  return <section className="cp-profile-header" aria-label="Профиль кандидата">
    <div className="cp-person-row">
      <div className="cp-avatar" aria-hidden="true">{initials}</div>
      <div className="cp-person-heading">
        <h1>{name}</h1>
        <p className={role ? 'cp-person-role' : 'cp-person-role cp-muted'}>{role || 'Желаемая роль не указана'}</p>
        <div className="cp-person-meta"><span><svg aria-hidden="true" width="15" height="15" viewBox="0 0 20 20" fill="none"><path d="M15.5 8.5c0 4-5.5 8-5.5 8s-5.5-4-5.5-8a5.5 5.5 0 0 1 11 0Z" stroke="currentColor" strokeWidth="1.4"/><circle cx="10" cy="8" r="1.8" stroke="currentColor" strokeWidth="1.4"/></svg>{city || 'Город не указан'}</span></div>
      </div>
    </div>
  </section>;
}
export default ProfileHeader;
