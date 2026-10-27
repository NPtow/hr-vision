import React from 'react';
import { createRoot } from 'react-dom/client';
import { CompanySite } from './site';
import { CandidateSite } from './candidate-site';

const candidateLanding = window.location.pathname.replace(/\/+$/, '') === '/candidate';
createRoot(document.getElementById('root')!).render(candidateLanding ? <CandidateSite/> : <CompanySite/>);
