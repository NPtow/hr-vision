import type { Meta, StoryObj } from '@storybook/react-vite';
import { CandidatePublicProfileLab } from './scenes/candidate-public-profile/CandidatePublicProfileLab';

const meta={title:'HR Vision/Публичный профиль кандидата',id:'hr-vision-public-profile',component:CandidatePublicProfileLab,parameters:{layout:'fullscreen',controls:{disable:true}}} satisfies Meta<typeof CandidatePublicProfileLab>;
export default meta;
type Story=StoryObj<typeof meta>;
export const Gallery:Story={name:'Три варианта'};
export const Cinema:Story={name:'01 · Сначала история',args:{variant:'cinema'}};
export const Context:Story={name:'02 · Сразу человек',args:{variant:'context'}};
export const Cover:Story={name:'03 · Личная обложка',args:{variant:'cover'}};
