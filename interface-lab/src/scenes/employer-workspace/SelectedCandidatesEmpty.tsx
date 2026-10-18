import { ArrowRight, Users } from 'lucide-react';
import { Button } from '../../components/ui/button';
import './selected-candidates-empty.css';

export function SelectedCandidatesEmpty({ onChoose, error }: { onChoose: () => void; error?: string }) {
  return <main className="esc-selected-empty">
    <section className="esc-selected-empty-content" aria-labelledby="selected-candidates-empty-title">
      <Users className="esc-selected-empty-icon" size={40} aria-hidden="true"/>
      <h2 id="selected-candidates-empty-title">Пока нет выбранных кандидатов</h2>
      <p>Пригласите кандидата на встречу — здесь появится история его этапов.</p>
      <Button className="esc-selected-empty-action" onClick={onChoose}>
        Выбрать кандидата<ArrowRight size={16} aria-hidden="true"/>
      </Button>
      {error && <p className="ew-inline-error" role="alert">{error}</p>}
    </section>
  </main>;
}
