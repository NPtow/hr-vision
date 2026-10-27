import type { Meta, StoryObj } from '@storybook/react-vite';
import { AssessmentInterpretationPreview } from './AssessmentInterpretationPreview';
import { DsaAssessmentPreview } from './DsaAssessmentPreview';
const meta = {
  id: 'hr-vision-assessment-interpretation',
  title: 'HR Vision/Оценка в работе работодателя',
  component: AssessmentInterpretationPreview,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
} satisfies Meta<typeof AssessmentInterpretationPreview>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Inspector: Story = { name: 'Карточка · Карта навыков', args: { initialScene: 'inspector' } };
export const Decision: Story = { name: 'Карточка · Вопрос к решению', args: { initialScene: 'decision' } };
export const Lens: Story = { name: 'Карточка · Вывод рядом с репликой', args: { initialScene: 'lens' } };
export const Brief: Story = { name: 'Профиль · Краткое досье', args: { initialScene: 'brief' } };
export const Evidence: Story = { name: 'Профиль · Качества и основания', args: { initialScene: 'evidence' } };
export const Plan: Story = { name: 'Профиль · Следующая встреча', args: { initialScene: 'plan' } };

export const Radar: Story = { name: 'Карта навыков · Видео и источники', args: { initialScene: 'inspector' } };
export const Dsa: Story = { name: 'ДСА · Реальные интервью', render: () => <DsaAssessmentPreview/> };
