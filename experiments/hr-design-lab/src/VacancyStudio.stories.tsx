import type {Meta,StoryObj} from '@storybook/react-vite';
import {VacancyStudio} from './scenes/vacancy-studio/VacancyStudio';
const meta:Meta<typeof VacancyStudio>={title:'HR Vision/Вакансия · живой макет',id:'hr-vision-vacancy-studio',component:VacancyStudio,parameters:{layout:'fullscreen',controls:{disable:true},docs:{description:{component:'Рабочий эксперимент: настоящая модель, отдельное хранилище черновиков. Публикация в Верстак отключена.'}}}};
export default meta;
type Story=StoryObj<typeof meta>;
export const Live:Story={name:'Создать вакансию'};
