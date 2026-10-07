import type { Meta, StoryObj } from "@storybook/react-vite";
import { CandidatePanel } from "./scenes/candidate-panel/CandidatePanel";
const meta = {
  title: "HR Vision/Панель · HRDS",
  id: "hr-vision-panel-hrds",
  component: CandidatePanel,
  parameters: { layout: "fullscreen", controls: { disable: true } },
} satisfies Meta<typeof CandidatePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Panel: Story = {
  name: "01 · Панель кандидатов",
  args: { initialScreen: "panel" },
};
export const Add: Story = {
  name: "02 · Добавление кандидатов",
  args: { initialScreen: "add" },
};
export const Schedule: Story = {
  name: "03 · Назначение встречи",
  args: { initialScreen: "schedule" },
};
