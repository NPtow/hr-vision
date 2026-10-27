import { useEffect, useState } from 'react';
import { FileText, Link2, Loader2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { request } from '../connected-journey/api';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { Avatar } from './PeoplePanel';
import { downloadResume, type IntakePerson, type PreparedProfile } from './intake';

function webUrl(value: string) {
  if (!value.trim()) return '';
  try {
    const url = new URL(value, window.location.origin);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

function PreparedInterview({ person, profile, index, token }: {
  person: IntakePerson; profile: PreparedProfile; index: number; token: string;
}) {
  const source = profile.recordings[index];
  const [media, setMedia] = useState<{ url: string; mime: string } | null>(null);
  const [loading, setLoading] = useState(Boolean(source));
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setMedia(null); setError(''); setLoading(Boolean(source));
    if (!source) return;
    void request<{ url: string; mime: string }>('employer/intake/recording', token, { id: person.id, index })
      .then(result => {
        if (!current) return;
        const url = webUrl(result.url);
        if (!url) throw new Error('Не удалось получить ссылку на запись.');
        setMedia({ ...result, url });
      })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : 'Не удалось загрузить запись.'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [person.id, index, token, source?.publicUrl, attempt]);
  const originalUrl = webUrl(source?.publicUrl || '');
  const evaluationUrl = webUrl(profile.evaluationUrl || '');
  return <>
    {loading && <p className="ir-muted" role="status"><Loader2 className="cj-spin" size={16} aria-hidden="true"/> Загружаем запись…</p>}
    {error && <div className="ew-inline-error" role="alert"><p>{error}</p><Button type="button" variant="ghost" onClick={() => setAttempt(value => value + 1)}>Повторить загрузку</Button></div>}
    <SourceScreeningReview appearance="panel" sourceId={`${person.id}:prepared:${index}`}
      name={person.name} chapters={[]} fullChapters={[]}
      media={media ? { src: media.url, label: source?.name } : undefined}
      emptyMessage={loading ? 'Загружаем запись…' : error || 'Запись интервью не добавлена.'}
      recruiterContent={<><span className="ir-eyebrow">Мнение рекрутера</span>{profile.evaluationText ? <p className="ew-prewrap">{profile.evaluationText}</p> : evaluationUrl ? <p><a href={evaluationUrl} target="_blank" rel="noreferrer">Открыть оценку рекрутера ↗</a></p> : <p>Оценка рекрутера не добавлена.</p>}</>}/>
    {originalUrl && <a className="ew-file-row" href={originalUrl} target="_blank" rel="noreferrer"><Link2 size={18} aria-hidden="true"/><span>Открыть исходную запись ↗</span></a>}
  </>;
}

export function PreparedIntakeProfile({ person, token }: { person: IntakePerson; token: string }) {
  const [tab, setTab] = useState('interview');
  const [index, setIndex] = useState(0);
  const [error, setError] = useState('');
  const profile = person.preparedProfile;
  if (!profile) return null;
  const resumeUrl = webUrl(person.link);
  return <>
    <header className="ew-person-heading"><Avatar name={person.name}/><div className="ew-person-name"><h2>{person.name}</h2><p>{person.role || 'Должность не указана'}</p>{profile.sourceLabel && <small>{profile.sourceLabel}</small>}</div>{person.archived && <span className="ew-status">В архиве</span>}</header>
    <Tabs value={tab} onValueChange={setTab} className="ew-profile-tabs">
      <div className="ew-profile-toolbar"><TabsList aria-label="Материалы кандидата"><TabsTrigger value="interview">Интервью</TabsTrigger><TabsTrigger value="resume">Резюме</TabsTrigger></TabsList></div>
      <TabsContent value="interview">
        {profile.recordings.length > 1 && <div className="ew-methods" role="group" aria-label="Части интервью">{profile.recordings.map((recording, recordingIndex) => <button type="button" key={`${recordingIndex}:${recording.name}`} aria-pressed={index === recordingIndex} onClick={() => setIndex(recordingIndex)}>Часть {recordingIndex + 1}</button>)}</div>}
        {tab === 'interview' && <PreparedInterview key={`${person.id}:${index}:${token}`} person={person} profile={profile} index={index} token={token}/>}
      </TabsContent>
      <TabsContent value="resume"><section className="ew-detail-card"><h3>Резюме</h3>
        {person.files.map(file => <button type="button" className="ew-file-row" key={file.id} onClick={() => { setError(''); void downloadResume(token, file).catch(cause => setError(cause instanceof Error ? cause.message : 'Не удалось загрузить резюме.')); }}><FileText size={20} aria-hidden="true"/><span><strong>{file.name}</strong><small>{Math.ceil(file.size / 1024)} КБ</small></span><span>Скачать</span></button>)}
        {resumeUrl && <a className="ew-file-row" href={resumeUrl} target="_blank" rel="noreferrer"><Link2 size={18} aria-hidden="true"/>Открыть резюме ↗</a>}
        <p className="ew-prewrap">{profile.cvText || 'Текст резюме не добавлен.'}</p>
        {person.contact && <><h3>Контакт</h3><p className="ew-prewrap">{person.contact}</p></>}
        {error && <p className="ew-inline-error" role="alert">{error}</p>}
      </section></TabsContent>
    </Tabs>
  </>;
}
