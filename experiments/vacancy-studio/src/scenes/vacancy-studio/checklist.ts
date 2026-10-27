// Mention hints only. These never set requirements or hard filters in the profile.
export const hintTopics = [
  {id:'role',label:'Должность',pattern:/(?:\bsdr\b|\bbdr\b|account\s+(?:manager|executive)|sales\s+(?:manager|director)|менеджер|руководител|директор|продавец|лидогенератор|аккаунт)/iu},
  {id:'location',label:'Локация',pattern:/(?:москв|санкт[-\s]петербург|петербург|\bmoscow\b|\bremote\b|удал[её]н|гибрид|казан|алматы|астан|екатеринбург|новосибирск|сочи|локаци[яи]|город\s+\p{L}+)/iu},
  {id:'experience',label:'Опыт',pattern:/(?:\d+\s*(?:\+|–|-|—|до)?\s*\d*\s*(?:лет|год|years?)|(?:одного|двух|тр[её]х|пяти)\s+(?:лет|год)|без\s+опыта|опыт\s+(?:не\s+)?(?:обязател|нужен|важен)|стажиров|junior|senior)/iu},
  {id:'industry',label:'Отрасль',pattern:/(?:логист|logistics|industrial|real estate|insurance|saas|финтех|fintech|недвижим|промышлен|энергет|медицин|фарма|ритейл|retail|e[-\s]?commerce|строитель|образован|телеком|страхован|производств|\bit\b|айти|отрасл[ьи])/iu},
  {id:'skills',label:'Навыки',pattern:/(?:квалифиц|переговор|возражен|воронк|\bcrm\b|битрикс|bitrix|amo(?:crm)?|холодн|лидогенерац|искать\s+клиент|находить\s+клиент|назначать\s+встреч|уточнять\s+задач|презентац|анализ\s+продаж)/iu},
] as const;
export function detectMentions(text:string){
  // URL fragments and quoted job-board paths are not evidence of completed topics.
  const prose=text.replace(/https?:\/\/\S+/giu,'');
  return hintTopics.map(t=>({...t,mentioned:t.pattern.test(prose)}));
}
