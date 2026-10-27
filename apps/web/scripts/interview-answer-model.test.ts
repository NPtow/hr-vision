import assert from 'node:assert/strict';
import test from 'node:test';
import { interviewAnswerParts } from '../src/scenes/interview-review/interview-answer-model.ts';
import type { InterviewAnswer } from '../src/scenes/interview-review/interview-assistant-api.ts';

const answer: InterviewAnswer = { mode:'chat', text:'Ответ.', supported:true,
  parts:[{text:'Первая мысль.',sourceIds:['s1','foreign','s1']},{text:'Вторая мысль.',sourceIds:['s2']}],
  evidence:[{id:'s1',quote:'Исходная реплика.',startMs:12000,speakerName:'Кандидат'},
    {id:'s2',quote:'Другая реплика.',startMs:40000,speakerName:'Кандидат'}],
  coverage:{selected:2,omitted:0},model:'fixture',version:'test' };

test('sources remain next to their paragraph and only known IDs can seek', () => {
  const parts = interviewAnswerParts(answer);
  assert.deepEqual(parts.map(part=>part.text),['Первая мысль.','Вторая мысль.']);
  assert.deepEqual(parts.map(part=>part.sources.map(source=>[source.id,source.startMs,source.number])),[[['s1',12000,1]],[['s2',40000,2]]]);
});
test('invalid source times and unknown selections leave the prose intact', () => {
  const parts = interviewAnswerParts({...answer,evidence:answer.evidence.map(source=>({...source,startMs:NaN}))});
  assert.deepEqual(parts.map(part=>part.text),['Первая мысль.','Вторая мысль.']);
  assert.ok(parts.every(part=>part.sources.length===0));
});
test('previous response shapes still display with their supplied sources', () => {
  const parts = interviewAnswerParts({...answer,parts:undefined});
  assert.equal(parts[0].text,'Ответ.'); assert.equal(parts[0].sources.length,2);
});
