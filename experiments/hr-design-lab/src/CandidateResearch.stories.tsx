import type {Meta,StoryObj} from '@storybook/react-vite';
import {CandidateProfileResearch} from './scenes/candidate-profile-research/CandidateProfileResearch';
const meta={title:'HR Vision/Профиль кандидата',id:'hr-vision-candidate-research',component:CandidateProfileResearch,parameters:{layout:'fullscreen',controls:{disable:true}}} satisfies Meta<typeof CandidateProfileResearch>;
export default meta;
type Story=StoryObj<typeof meta>;
export const Gallery:Story={name:'Референсы · профили, навыки и интервью'};

export const Selected:Story={name:"Выбранные продукты · больше экранов",args:{collection:"selected"}};
