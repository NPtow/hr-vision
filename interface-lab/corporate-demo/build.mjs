import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
const root=fileURLToPath(new URL('.',import.meta.url));
function run(args){const result=spawnSync(process.execPath,args,{cwd:root,stdio:'inherit'});if(result.status!==0)process.exit(result.status??1);}
run(['../node_modules/typescript/bin/tsc','-p','tsconfig.json']);
run(['../node_modules/vite/bin/vite.js','build','--config','vite.config.ts']);
run(['../node_modules/storybook/dist/bin/dispatcher.js','build','--config-dir','.storybook','--output-dir','dist/storybook','--disable-telemetry','--quiet']);
const index=JSON.parse(readFileSync(resolve(root,'dist/storybook/index.json'),'utf8'));
const exported={cta:'Cta','cta-liquid':'CtaLiquid','cta-perimeter':'CtaPerimeter','cta-ribbon':'CtaRibbon',intro:'Intro',needs:'Needs',people:'People',connections:'Connections',interest:'Interest','interest-letters':'InterestLetters','interest-signal':'InterestSignal','interest-cards':'InterestCards',interview:'Interview','interview-focus':'InterviewFocus','interview-closeup':'InterviewCloseup','review-feedback':'ReviewFeedback',calendar:'Calendar',meeting:'Meeting',feedback:'Feedback','feedback-graph':'FeedbackGraph','feedback-reshuffle':'FeedbackReshuffle','feedback-compare':'FeedbackCompare',offer:'Offer',finish:'Finish'};
const manifest={};
for(const [scene,name] of Object.entries(exported)){
 const entry=Object.values(index.entries).find(item=>item.type==='story'&&item.exportName===name);
 if(!entry)throw new Error(`Missing published story: ${name}`);
 manifest[scene]=entry.id;
}
writeFileSync(resolve(root,'dist/lab-manifest.json'),JSON.stringify(manifest,null,2)+'\n');
if(!existsSync(resolve(root,'dist/lab.html')))throw new Error('Missing lab entry point');
console.log('Verified Storybook scene IDs:',JSON.stringify(manifest));
