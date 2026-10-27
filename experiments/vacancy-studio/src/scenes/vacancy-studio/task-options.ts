import {parameterById} from '../vacancy-research/model';
import type {Proposal} from './api';
import templates from './task-templates.json';

// Work expectations, not candidate scores. The assessment's L1 describes failure
// and cannot honestly become a junior job requirement. Its anchor remains in core.
export const taskFallbacks:Record<string,string[]>=templates.parameters;

export function taskChoices(p:Proposal){
  const parameter=parameterById(p.parameter_id!);
  const fallback=taskFallbacks[parameter.parameter_id];
  const generated=p.task_scenarios;
  // A partial or duplicated model set cannot quietly redefine a scale.
  const valid=generated&&['L2','L3','L4','L5'].every(id=>typeof generated[id]==='string'&&generated[id].trim().length>=12&&generated[id].length<=260)&&new Set(Object.values(generated).map(text=>text.trim().toLowerCase())).size===4
    &&!Object.values(generated).some(text=>/но\s+не(?:\s|$)|не\s+(?:связыва|проверя|уточня|обновля)|пропуска(?:ть|я)\s+существен/iu.test(text))
    &&!(p.parameter_id==='A03'&&/решени[ея]\s+(?:переда[а-яё]*\s+)?руководител/iu.test(generated.L3));
  return parameter.levels.slice(1).map((anchor,index)=>({
    level:index+2,levelId:anchor.level_id,
    text:valid?generated[anchor.level_id]:fallback[index],
  }));
}
