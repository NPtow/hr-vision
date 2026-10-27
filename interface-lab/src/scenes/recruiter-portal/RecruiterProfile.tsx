import { Button } from '../../components/ui/button';
import { AvailabilitySchedule } from './AvailabilitySchedule';
import type { AvailabilityBlock, AvailabilityDate, RecruiterSlot } from './api';
import './recruiter-account.css';

export function RecruiterProfile({ recruiter, blocks, dates, slots, busy, run, onLogout }: {
  recruiter: { id: string; name: string; email: string };
  blocks: AvailabilityBlock[];
  dates: AvailabilityDate[];
  slots: RecruiterSlot[];
  busy: boolean;
  run: (path: string, body: unknown) => Promise<boolean>;
  onLogout: () => void;
}) {
  return <div className="rp-page-content rp-profile">
    <section className="rp-profile-identity" aria-label="Профиль рекрутера">
      <div><h2>{recruiter.name}</h2><p>{recruiter.email}</p></div>
      <div className="rp-profile-actions"><Button type="button" variant="ghost" disabled={busy} onClick={onLogout}>Выйти</Button></div>
    </section>
    <AvailabilitySchedule key={recruiter.id} recruiterName={recruiter.name} blocks={blocks} dates={dates} slots={slots} busy={busy} run={run}/>
  </div>;
}
