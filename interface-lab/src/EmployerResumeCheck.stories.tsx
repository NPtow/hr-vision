import type { Meta, StoryObj } from '@storybook/react-vite';
import { EmployerCandidateProfile } from './scenes/employer-workspace/EmployerCandidateProfile';
import { SourceScreeningReview } from './scenes/interview-review/InterviewReview';
import { transcript } from './scenes/candidate-header-lab/demo-data';
import './scenes/agency-product/theme.css';
import './scenes/agency-product/product.css';
import './scenes/connected-journey/connected.css';
import './scenes/employer-workspace/employer.css';

function ResumeCheck({ fallback = false, failure = false }: { fallback?: boolean; failure?: boolean }) {
  const name = 'Анастасия Глухова';
  return <div className="agency-product agency-product-theme cj-app ew-root" style={{ padding: 30, background: '#f4f3f7', minHeight: '100vh' }}><div style={{ padding: 24, borderRadius: 24, background: 'white' }}>
    <EmployerCandidateProfile person={{ id: 'resume-check', name }} source={{ cv: 'Резюме\nМенеджер по продажам B2B\nДемонстрационные данные.', score: 4 }}
      resume={fallback ? undefined : { id: failure ? 'failure' : 'fixture', name: 'Резюме — Анастасия Глухова.pdf', sizeLabel: '556 КБ', load: async signal => {
        if (failure) throw new Error('Не удалось загрузить резюме.');
        return (await fetch('/candidate-header-demo/resume.pdf', { signal })).blob();
      }, onDownload: () => { const a = document.createElement('a'); a.href = '/candidate-header-demo/resume.pdf'; a.download = 'Резюме.pdf'; a.click(); } }}
      workflowRevision={0} onRespond={async () => true}
      interview={header => <SourceScreeningReview name={name} header={header} appearance="candidate" sourceId="resume-check" media={{ src: '/candidate-header-demo/interview.webm' }}
        chapters={transcript.map(t => ({ seconds: t.seconds, title: t.text, detail: t.text, fragment: t.text, source: t.time }))}/>}/>
  </div></div>;
}
const meta = { title: 'HR Vision/Employer resume check', component: ResumeCheck, parameters: { layout: 'fullscreen' } } satisfies Meta<typeof ResumeCheck>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Pdf: Story = {};
export const Text: Story = { args: { fallback: true } };
export const Failure: Story = { args: { failure: true } };
