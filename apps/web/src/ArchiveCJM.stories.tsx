import type { Meta, StoryObj } from '@storybook/react-vite';
import { CjmLab } from './scenes/cjm-v3/CjmLab';
import type { StrategyId } from './scenes/cjm-v3/data';

function ArchivedCjm({ initialStrategy = 'agency' }: { initialStrategy?: StrategyId }) {
  return <>
    <aside aria-label="Историческая версия CJM" style={{
      background: '#242421', color: '#fffef8', padding: '20px 28px',
      font: '14px/1.6 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      flexWrap: 'wrap', gap: '14px 24px', borderBottom: '4px solid #aaa99c',
    }}>
      <div style={{ flex: '1 1 520px' }}>
        <strong style={{ display: 'block', fontSize: 19, marginBottom: 4 }}>
          Архив CJM v3 · заменён версией v4
        </strong>
        <span style={{ color: '#d2d1c8' }}>
          Сохранён прежний вариант карт и привязки экранов. Его состав участников,
          границы стратегий и формулировки джоб были пересмотрены; это не текущая модель.
        </span>
      </div>
      <a href={`?id=hr-vision-cjm--${initialStrategy}&viewMode=story`} style={{
        color: '#242421', background: '#fffef8', padding: '11px 16px',
        borderRadius: 7, fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap',
      }}>
        Открыть актуальную CJM v4 ↗
      </a>
    </aside>
    <CjmLab key={initialStrategy} initialStrategy={initialStrategy}/>
  </>;
}

const meta = {
  title: 'HR Vision/Архив CJM v3',
  id: 'hr-vision-cjm-archive',
  component: ArchivedCjm,
  parameters: { layout: 'fullscreen', controls: { disable: true } },
  args: { initialStrategy: 'agency' },
} satisfies Meta<typeof ArchivedCjm>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Contacts: Story = { name: '01 База контактов · архив', args: { initialStrategy: 'contacts' } };
export const Agency: Story = { name: '02 HR-агентство · архив', args: { initialStrategy: 'agency' } };
export const Ats: Story = { name: '03 Бесплатная ATS · архив', args: { initialStrategy: 'ats' } };
