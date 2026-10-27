import test from 'node:test';
import assert from 'node:assert/strict';
import {choiceDistribution,requirementDistributions,levelLabel,probability,activeRequiredLevel} from '../src/scenes/selection-workbench/distribution-data.ts';
const defs=[{id:'L1',label:'Первый'},{id:'L2',label:'Второй'},{id:'insufficient_data',label:'Недостаточно данных',unknown:true}];
test('raw rounded mass is not normalised, and input remains untouched',()=>{
 const raw={choice:'L2',confidence:.48,probabilities:{L1:.2,L2:.6,insufficient_data:.19}};
 const before=structuredClone(raw), parsed=choiceDistribution(raw,defs);
 assert.deepEqual(parsed.options.map(x=>x.probability),[.2,.6,.19]);assert.deepEqual(raw,before);
});
test('server choice remains authoritative instead of frontend argmax',()=>{
 const d=choiceDistribution({choice:'L1',probabilities:{L1:.3,L2:.7,insufficient_data:0}},defs);
 assert.equal(d.choice,'L1');
});
test('missing and invalid probabilities remain unknown; zero remains zero',()=>{
 const d=choiceDistribution({probabilities:{L1:0,L2:'0.4'}},defs);
 assert.deepEqual(d.options.map(x=>x.probability),[0,null,null]);assert.equal(d.confidence,null);
 for(const v of [-1,1.1,NaN,Infinity,undefined,null,'0']) assert.equal(probability(v),null);
});
test('missing raw response cannot become a fabricated distribution',()=>{
 const d=choiceDistribution(null,defs);assert.equal(d.choice,null);assert.ok(d.options.every(x=>x.probability===null));
});
test('manual override is separate from the saved model answer',()=>{
 const p={parameter_id:'A',title:'Навык',levels:[{level_id:'L1',label:'Первый',description:'A'},{level_id:'L2',label:'Второй',description:'B'}],required_level_id:'L1',importance:2,status:'ready',raw_answers:{level:{choice:'L2',confidence:.5,probabilities:{L1:.2,L2:.7,insufficient_data:.1}},importance:{choice:'I3',probabilities:{I0:0,I1:.1,I2:.2,I3:.7,insufficient_data:0}}}};
 const before=structuredClone(p), d=requirementDistributions(p);
 assert.equal(d.level.choice,'L2');assert.equal(d.importance.choice,'I3');assert.equal(levelLabel(p),'Первый');assert.deepEqual(p,before);
});
test('zero importance does not ask the operator to clarify a level',()=>{
 assert.equal(levelLabel({importance:0,required_level_id:null,levels:[]}), 'Не требуется');
 assert.equal(levelLabel({importance:3,required_level_id:null,levels:[]}), 'Не определён');
});
test('insufficient data stays an independent outcome, not a numeric level',()=>{
 const d=choiceDistribution({choice:'insufficient_data',probabilities:{L1:.05,L2:.15,insufficient_data:.8}},defs);
 assert.equal(d.choice,'insufficient_data');assert.equal(d.options.at(-1).unknown,true);assert.equal(d.options.at(-1).probability,.8);
});

test('a zero-weight skill retains its raw level without presenting it as required',()=>{
 const p={parameter_id:'A',excluded:true,exclusion_reason:'zero_importance',weight:0,required_level_id:'L3'};
 const requirement={importance:0,required_level_id:'L3'};
 const before=structuredClone(p);
 assert.equal(activeRequiredLevel(p,requirement),undefined);
 assert.equal(activeRequiredLevel(p),undefined);
 assert.equal(activeRequiredLevel({...p,weight:2},requirement),undefined);
 assert.equal(activeRequiredLevel({...p,weight:2},{...requirement,importance:2}),'L3');
 assert.deepEqual(p,before);
});


test('unknown importance is preserved as a source answer while no target is marked required',()=>{
 const parameter={importance:'insufficient_data',required_level_id:'L2',levels:[{level_id:'L2',label:'Второй'}],raw_answers:{
   level:{choice:'L2',probabilities:{L2:.9,insufficient_data:.1}},
   importance:{choice:'insufficient_data',probabilities:{I0:0,I1:.1,I2:0,I3:0,insufficient_data:.9}}}};
 const before=structuredClone(parameter), distributions=requirementDistributions(parameter);
 assert.equal(distributions.importance.choice,'insufficient_data');
 assert.equal(distributions.importance.options.find(p=>p.id==='insufficient_data').probability,.9);
 assert.equal(distributions.importance.options.find(p=>p.id==='I0').probability,0);
 assert.equal(activeRequiredLevel({weight:0,required_level_id:'L2'},parameter),undefined);
 assert.equal(activeRequiredLevel({required_level_id:'L2'},parameter),undefined);
 assert.equal(levelLabel(parameter),'Не участвует в расчёте');assert.deepEqual(parameter,before);
});
