import { useEffect, useState } from 'react';
import type { CallMaterials } from '../candidate-portal/api';
import { SourceScreeningReview } from '../interview-review/InterviewReview';
import { parseMeetingTranscript } from './transcript';
import { request, type Journey, type Person, type SourceProfile } from './api';

/** Bind the existing interview view to this application's authorised source media. */
export function EmployerScreening({ journey, person, source }: { journey: Journey; person: Person; source: SourceProfile }) {
  return <Screening key={`${person.vacancyId}:${person.id}:${source.id}`} journey={journey} person={person} source={source}/>;
}

function Screening({ journey, person, source }: { journey: Journey; person: Person; source: SourceProfile }) {
  const [materials, setMaterials] = useState<CallMaterials | null>(null);
  const [error, setError] = useState('');
  const vacancyId = person.vacancyId || journey.state!.vacancy.id;
  const generation = journey.state!.generation;
  const available = journey.state!.videoConfigured;
  useEffect(() => {
    if (!available) return;
    let mounted = true;
    void request<CallMaterials>('employer/screening/materials', journey.token, {
      candidate: person.id, applicationId: person.applicationId || person.id, vacancyId, generation,
    }).then(result => { if (mounted) setMaterials(result); }).catch(cause => { if (mounted) setError((cause as Error).message); });
    return () => { mounted = false; };
  }, [available, journey.token, person.id, person.applicationId, vacancyId, generation]);

  const transcript = materials?.transcripts.map(item => item.text).join('\n\n') || '';
  const recordings = materials?.recordings || (source.video ? [{ id: source.id, url: source.video }] : []);
  const transcriptChapters = recordings.length <= 1 ? parseMeetingTranscript(transcript) : [];
  const chapters = source.shortChapters.length ? source.shortChapters : transcriptChapters;
  const fullChapters = source.fullChapters.length ? source.fullChapters : transcriptChapters;
  const emptyMessage = error || (!available ? 'Видеосервис ещё не подключён.'
    : materials?.video.status === 'not_recorded' ? 'Запись первичного интервью отсутствует.'
    : materials?.video.status === 'error' ? 'Сейчас не удалось получить запись первичного интервью.'
    : 'Запись первичного интервью обрабатывается.');
  return <>{(recordings.length ? recordings : [null]).map((recording, index) => <SourceScreeningReview
    key={recording?.id || 'pending'} appearance="panel" sourceId={`${source.id}:${recording?.id || 'pending'}`}
    name={person.name} chapters={recordings.length <= 1 ? chapters : []} fullChapters={recordings.length <= 1 ? fullChapters : []}
    shortPlaylist={source.shortChapters.length > 0} media={recording ? { src: recording.url } : undefined}
    transcriptText={index === 0 ? transcript : undefined} summary={index === 0 ? source.shortSummary : undefined}
    emptyMessage={emptyMessage} recruiterContent={index === 0 && (source.conclusion.length || source.questions.length) ? <>
      <span className="ir-eyebrow">Мнение рекрутера{source.score !== null ? ` · ${source.score} / 5` : ''}</span>
      {source.conclusion.map((text, item) => <p key={item}>{text}</p>)}
      {source.questions.map((question, item) => <p key={item}>{question}</p>)}
    </> : undefined}/>)}</>;
}
