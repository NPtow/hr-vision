import { useState } from 'react';
import logo from '../employer-workspace/assets/hr-vision-logo.svg';
import { InterviewList } from './InterviewList';
import { RecruiterCandidateDetails } from './RecruiterCandidateDetails';
import { createAgendaPreview } from './agenda-preview';

export function RecruiterAgendaPreview() {
  const [candidates] = useState(createAgendaPreview);
  const [selectedId, setSelectedId] = useState('');
  const cabinetUrl = new URL(location.href);
  cabinetUrl.searchParams.delete('recruiterPreview');
  return <div className="ew-root rp-root">
    <header className="ew-topbar rp-topbar">
      <a className="ew-logo" href={cabinetUrl.href} aria-label="Вернуться в кабинет"><img src={logo} alt="HR Vision"/></a>
    </header>
    <main className="rp-main" aria-label="Пример расписания на 30 встреч">
      <div className="rp-agenda-preview-note"><p>30 вымышленных встреч · временный просмотр</p><a href={cabinetUrl.href}>В мой кабинет</a></div>
      <InterviewList candidates={candidates} busy={false} videoConfigured={false} readOnly selectedId={selectedId}
        onDetails={id => setSelectedId(current => current === id ? '' : id)} onCall={() => {}}
        renderDetails={candidate => <RecruiterCandidateDetails candidate={candidate} busy={false} readOnly token="" run={async () => false} onNotice={() => {}} onUpdated={() => {}}/>}/>
    </main>
  </div>;
}
