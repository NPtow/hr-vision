import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidateDecisionLab } from './scenes/candidate-header-lab/CandidateDecisionLab';

const meta = { title: 'HR Vision/Candidate decision', component: CandidateDecisionLab, parameters: { layout: 'fullscreen' } } satisfies Meta<typeof CandidateDecisionLab>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Direct: Story = { name: '01 · Открытый выбор', args: { initialVariant: 'direct' } };
export const Dock: Story = { name: '02 · Плавающий пульт', args: { initialVariant: 'dock' } };
export const Beside: Story = { name: '03 · Решение рядом', args: { initialVariant: 'beside' } };
