import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ArrowLeft, Check, FileText, Link2, Loader2, Upload, X } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import { importEmployerVacancy, type EmployerVacancyDetails, type EmployerVacancyImport, type EmployerVacancyImportInput, type EmployerVacancyValues } from '../connected-journey/api';
import './vacancy-create.css';

export type VacancyCreateValues = EmployerVacancyValues;
type SourceKind = 'file' | 'hh' | 'superjob' | 'other' | 'text';
type SourceInfo = EmployerVacancyImport['source'];
type Draft = { values: VacancyCreateValues; source: SourceInfo | null; warnings: string[]; sourceKind: SourceKind; text: string; url: string; importId: string; dirty: boolean };
type VacancyCreateProps = {
  active: boolean;
  token: string;
  draftKey: string;
  onBack: () => void;
  busy: boolean;
  error: string;
  onCreate: (values: VacancyCreateValues) => Promise<boolean>;
  company?: string;
  initialDescription?: string;
};

const sourceKinds: { id: SourceKind; label: string }[] = [{ id: 'file', label: 'Файл' }, { id: 'hh', label: 'HH' }, { id: 'superjob', label: 'SuperJob' }, { id: 'other', label: 'Другая ссылка' }, { id: 'text', label: 'Текст' }];
const fileTypes = '.pdf,.docx,.txt,.md,.csv,.rtf,.doc,.odt,.png,.jpg,.jpeg,.webp';
const filePattern = /\.(pdf|docx|txt|md|csv|rtf|doc|odt|png|jpe?g|webp)$/i;
const maxFileSize = 5 * 1024 * 1024;
const emptyDetails = (): EmployerVacancyDetails => ({ address: '', employmentType: '', schedule: '', salaryMin: null, salaryMax: null, currency: '', salaryPeriod: '', salaryTax: '', responsibilities: '', requirements: '', niceToHave: '', benefits: '', experience: '' });
const emptyValues = (company: string): VacancyCreateValues => ({ title: '', company, city: '', remote: false, format: '', salary: '', description: '', details: emptyDetails() });
const asText = (value: unknown, limit: number) => typeof value === 'string' ? value.slice(0, limit) : '';
const asChoice = <T extends string>(value: unknown, choices: readonly T[]): T | '' => typeof value === 'string' && choices.includes(value as T) ? value as T : '';
const asAmount = (value: unknown) => typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 100000000 ? value : null;

function normalizeValues(input: Partial<VacancyCreateValues> | null | undefined, company: string): VacancyCreateValues {
  const data = input || {};
  const details = data.details || emptyDetails();
  return {
    title: asText(data.title, 200), company: asText(data.company, 200) || company,
    city: asText(data.city, 120), remote: data.remote === true,
    format: asText(data.format, 300) || (data.remote ? 'Удалённо' : ''),
    salary: asText(data.salary, 300), description: asText(data.description, 10000),
    details: {
      address: asText(details.address, 300), schedule: asText(details.schedule, 500),
      employmentType: asChoice(details.employmentType, ['full-time', 'part-time', 'project', 'internship', 'temporary']),
      salaryMin: asAmount(details.salaryMin), salaryMax: asAmount(details.salaryMax),
      currency: asChoice(details.currency, ['RUB', 'USD', 'EUR', 'KZT', 'BYN']),
      salaryPeriod: asChoice(details.salaryPeriod, ['month', 'year', 'hour', 'shift']), salaryTax: asChoice(details.salaryTax, ['gross', 'net']),
      responsibilities: asText(details.responsibilities, 4000), requirements: asText(details.requirements, 4000),
      niceToHave: asText(details.niceToHave, 4000), benefits: asText(details.benefits, 4000), experience: asText(details.experience, 500),
    },
  };
}

function loadDraft(key: string, company: string, initialDescription: string): Draft {
  const fresh: Draft = { values: { ...emptyValues(company), description: initialDescription }, source: null, warnings: [], sourceKind: initialDescription ? 'text' : 'file', text: initialDescription, url: '', importId: '', dirty: false };
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || 'null');
    if (!saved || saved.version !== 1 || typeof saved.expiresAt !== 'number' || saved.expiresAt < Date.now()) return fresh;
    return {
      values: normalizeValues(saved.values, company), source: saved.source && typeof saved.source.label === 'string' ? saved.source : null,
      warnings: Array.isArray(saved.warnings) ? saved.warnings.filter((item: unknown) => typeof item === 'string') : [],
      sourceKind: sourceKinds.some(item => item.id === saved.sourceKind) ? saved.sourceKind : 'file',
      text: asText(saved.text, 24000), url: asText(saved.url, 2048), importId: asText(saved.importId, 100), dirty: saved.dirty === true,
    };
  } catch { return fresh; }
}

function salaryText(values: VacancyCreateValues): string {
  const { salaryMin, salaryMax, currency, salaryPeriod, salaryTax } = values.details;
  if (salaryMin === null && salaryMax === null) return '';
  const number = (value: number) => new Intl.NumberFormat('ru-RU').format(value);
  const range = salaryMin !== null && salaryMax !== null ? `${number(salaryMin)}–${number(salaryMax)}` : salaryMin !== null ? `от ${number(salaryMin)}` : `до ${number(salaryMax!)}`;
  const currencyLabel = { '': '', RUB: '₽', USD: '$', EUR: '€', KZT: '₸', BYN: 'BYN' }[currency];
  const periodLabel = { '': '', month: 'в месяц', year: 'в год', hour: 'в час', shift: 'за смену' }[salaryPeriod];
  const taxLabel = { '': '', gross: 'до вычета налогов', net: 'на руки' }[salaryTax];
  return [range, currencyLabel, periodLabel, taxLabel].filter(Boolean).join(' ');
}

const fileBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(',')[1]);
  reader.onerror = () => reject(new Error('Не удалось прочитать файл. Выберите его ещё раз.'));
  reader.readAsDataURL(file);
});
const hasExtraConditions = (value: VacancyCreateValues) => ['address', 'employmentType', 'schedule', 'salaryPeriod', 'salaryTax', 'experience'].some(key => Boolean(value.details[key as keyof EmployerVacancyDetails]));
const hasExtraDescription = (value: VacancyCreateValues) => Boolean(value.details.niceToHave || value.details.benefits || value.description);

export function VacancyCreate({ active, token, draftKey, onBack, busy, error, onCreate, company = '', initialDescription = '' }: VacancyCreateProps) {
  const id = useId();
  const storageKey = `hr-vision-vacancy-draft:${draftKey}`;
  const [draft, setDraft] = useState(() => loadDraft(storageKey, company, initialDescription));
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState('');
  const [importError, setImportError] = useState('');
  const [pendingImport, setPendingImport] = useState<EmployerVacancyImport | null>(null);
  const [conditionsOpen, setConditionsOpen] = useState(() => hasExtraConditions(draft.values));
  const [descriptionOpen, setDescriptionOpen] = useState(() => hasExtraDescription(draft.values));
  const locked = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const titleInput = useRef<HTMLInputElement>(null);
  const companyInput = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const saving = busy || submitting;
  const disabled = saving || importBusy;
  const values = draft.values;
  const details = values.details;

  useEffect(() => {
    if (active) heading.current?.focus({ preventScroll: true });
  }, [active]);
  useEffect(() => {
    try {
      if (!draft.dirty && !draft.source && !draft.text && !draft.url && !draft.values.description) sessionStorage.removeItem(storageKey);
      else sessionStorage.setItem(storageKey, JSON.stringify({ ...draft, version: 1, expiresAt: Date.now() + 86400000 }));
    } catch { /* Keep the in-memory draft when browser storage is unavailable. */ }
  }, [draft, storageKey]);

  function edit(key: keyof Omit<VacancyCreateValues, 'remote' | 'details' | 'importId'>, value: string) {
    setDraft(previous => ({ ...previous, dirty: true, values: { ...previous.values, [key]: value, ...(key === 'format' ? { remote: value === 'Удалённо' } : {}) } }));
    setLocalError('');
  }
  function editDetail<K extends keyof EmployerVacancyDetails>(key: K, value: EmployerVacancyDetails[K]) {
    setDraft(previous => ({ ...previous, dirty: true, values: { ...previous.values, ...(key === 'salaryMin' || key === 'salaryMax' ? { salary: '' } : {}), details: { ...previous.values.details, [key]: value } } }));
    setLocalError('');
  }
  function pickFile(next: File | undefined) {
    if (!next || disabled) return;
    if (!filePattern.test(next.name) || !next.size || next.size > maxFileSize) { setImportError('Выберите документ или изображение из списка форматов, не больше 5 МБ.'); return; }
    setFile(next); setImportError(''); setPendingImport(null);
  }
  function applyImport(result: EmployerVacancyImport) {
    const imported = normalizeValues(result.fields, values.company || company);
    setDraft(previous => ({ ...previous, dirty: true, values: imported, source: result.source, warnings: result.warnings || [], importId: result.importId }));
    setConditionsOpen(hasExtraConditions(imported)); setDescriptionOpen(hasExtraDescription(imported));
    setPendingImport(null); setImportError(''); setLocalError('');
  }
  async function runImport() {
    if (disabled || locked.current || pendingImport) return;
    setImportError('');
    let input: EmployerVacancyImportInput;
    if (draft.sourceKind === 'file') {
      if (!file) { setImportError('Выберите файл вакансии.'); fileInput.current?.focus(); return; }
      input = { kind: 'file', filename: file.name, contentBase64: '' };
    } else if (draft.sourceKind === 'text') {
      if (!draft.text.trim()) { setImportError('Вставьте текст вакансии.'); return; }
      input = { kind: 'text', text: draft.text.trim() };
    } else {
      try {
        const url = new URL(draft.url.trim());
        if (!['https:', 'http:'].includes(url.protocol)) throw new Error();
        if (draft.sourceKind === 'hh' && !/(^|\.)hh\.ru$/i.test(url.hostname)) { setImportError('Добавьте ссылку на вакансию HH или выберите «Другая ссылка».'); return; }
        if (draft.sourceKind === 'superjob' && !/(^|\.)superjob\.ru$/i.test(url.hostname)) { setImportError('Добавьте ссылку на вакансию SuperJob или выберите «Другая ссылка».'); return; }
        input = { kind: 'url', url: url.href, source: draft.sourceKind };
      } catch { setImportError('Проверьте ссылку: она должна начинаться с https:// или http://.'); return; }
    }
    locked.current = true; setImportBusy(true);
    try {
      if (input.kind === 'file') input.contentBase64 = await fileBase64(file!);
      const result = await importEmployerVacancy(token, input);
      if (draft.dirty) setPendingImport(result);
      else applyImport(result);
    } catch (cause) {
      setImportError(cause instanceof TypeError ? 'Не удалось связаться с сервисом. Данные формы сохранены — попробуйте ещё раз.' : cause instanceof Error && cause.name !== 'TimeoutError' ? cause.message : 'Не дождались ответа. Данные формы сохранены — попробуйте импорт позже.');
    } finally { locked.current = false; setImportBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || locked.current || pendingImport) return;
    if (!values.title.trim() || !values.company.trim()) {
      const missingTitle = !values.title.trim();
      setLocalError(missingTitle ? 'Укажите название вакансии.' : 'Укажите компанию.');
      (missingTitle ? titleInput : companyInput).current?.focus(); return;
    }
    if ([details.salaryMin, details.salaryMax].some(value => value !== null && asAmount(value) === null)) { setLocalError('Укажите зарплату целым числом от 1 до 100 000 000 или оставьте поле пустым.'); return; }
    if (details.salaryMin !== null && details.salaryMax !== null && details.salaryMin > details.salaryMax) { setLocalError('Верхняя граница зарплаты должна быть не меньше нижней.'); return; }
    locked.current = true; setSubmitting(true); setLocalError('');
    try {
      const prepared = normalizeValues(values, company);
      for (const key of ['title', 'company', 'city', 'format', 'description'] as const) prepared[key] = prepared[key].trim();
      prepared.salary = salaryText(prepared);
      if (draft.importId) prepared.importId = draft.importId;
      if (await onCreate(prepared)) {
        setDraft({ values: emptyValues(company), source: null, warnings: [], sourceKind: 'file', text: '', url: '', importId: '', dirty: false });
        setFile(null); setPendingImport(null); setConditionsOpen(false); setDescriptionOpen(false);
        try { sessionStorage.removeItem(storageKey); } catch { /* The form also resets in memory. */ }
      }
    } catch (cause) { setLocalError(cause instanceof Error ? cause.message : 'Не удалось создать вакансию. Попробуйте ещё раз.'); }
    finally { locked.current = false; setSubmitting(false); }
  }

  if (!active) return null;
  const sourceLabel = sourceKinds.find(item => item.id === draft.sourceKind)!.label;
  const hasStructuredSalary = details.salaryMin !== null || details.salaryMax !== null;
  return <main className="ew-vacancy-create">
    <button type="button" className="ew-text-back" disabled={disabled} onClick={onBack}><ArrowLeft size={16}/>К вакансиям</button>
    <header className="ew-vacancy-create-heading"><h1 ref={heading} tabIndex={-1}>Новая вакансия</h1></header>
    <section className="ew-vacancy-source" aria-labelledby={`${id}-source-title`} aria-busy={importBusy}>
      <h2 id={`${id}-source-title`}>Заполнить из источника</h2>
      <div className="ew-vacancy-source-tabs" aria-label="Источник вакансии">{sourceKinds.map(kind => <button key={kind.id} type="button" aria-pressed={draft.sourceKind === kind.id} disabled={disabled || !!pendingImport} onClick={() => { setDraft(previous => ({ ...previous, sourceKind: kind.id })); setImportError(''); }}>{kind.label}</button>)}</div>
      {draft.sourceKind === 'file' ? <>
        <input ref={fileInput} type="file" accept={fileTypes} className="ew-vacancy-file-input" aria-label="Файл вакансии" disabled={disabled || !!pendingImport} onChange={event => { pickFile(event.target.files?.[0]); event.target.value = ''; }}/>
        <div className={`ew-vacancy-dropzone ${dragging ? 'is-dragging' : ''}`} onDragOver={event => { event.preventDefault(); if (!disabled) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); if (!pendingImport) pickFile(event.dataTransfer.files[0]); }}>
          {file ? <><FileText size={24}/><div><strong>{file.name}</strong><span>{Math.ceil(file.size / 1024)} КБ</span></div><Button type="button" variant="ghost" size="icon" aria-label="Убрать файл" disabled={disabled || !!pendingImport} onClick={() => setFile(null)}><X size={17}/></Button></> : <><Upload size={24}/><div><strong>Перетащите файл вакансии</strong><span>Документ или изображение · до 5 МБ</span></div><Button type="button" variant="outline" disabled={disabled || !!pendingImport} onClick={() => fileInput.current?.click()}>Выбрать файл</Button></>}
        </div>
        <p className="ew-vacancy-source-note">PDF, DOCX, TXT, MD, CSV, RTF, DOC, ODT, PNG, JPG, WEBP</p>
      </> : draft.sourceKind === 'text' ? <div className="ew-vacancy-source-label"><label htmlFor={`${id}-source-text`}>Текст вакансии</label><Textarea id={`${id}-source-text`} aria-describedby={`${id}-source-limit`} value={draft.text} rows={5} maxLength={24000} disabled={disabled || !!pendingImport} onChange={event => setDraft(previous => ({ ...previous, text: event.target.value }))} placeholder="Вставьте описание вакансии целиком"/><span id={`${id}-source-limit`} className="ew-vacancy-source-note">До 24 000 символов · {draft.text.length.toLocaleString('ru-RU')} / 24 000</span></div> : <label className="ew-vacancy-source-label" htmlFor={`${id}-source-url`}>Ссылка{draft.sourceKind !== 'other' ? ` на ${sourceLabel}` : ' на вакансию'}<div className="ew-vacancy-url"><Link2 size={18}/><Input id={`${id}-source-url`} type="url" maxLength={2048} value={draft.url} disabled={disabled || !!pendingImport} onChange={event => setDraft(previous => ({ ...previous, url: event.target.value }))} placeholder={draft.sourceKind === 'hh' ? 'https://hh.ru/vacancy/…' : draft.sourceKind === 'superjob' ? 'https://www.superjob.ru/vakansii/…' : 'https://…'}/></div></label>}
      <div className="ew-vacancy-import-action"><Button type="button" variant="secondary" disabled={disabled || !!pendingImport || (draft.sourceKind === 'file' ? !file : draft.sourceKind === 'text' ? !draft.text.trim() : !draft.url.trim())} onClick={() => { void runImport(); }}>{importBusy ? <><Loader2 size={16} className="ew-vacancy-spinner"/>Разбираем вакансию…</> : draft.sourceKind === 'text' ? 'Разобрать текст' : draft.sourceKind === 'file' ? 'Заполнить из файла' : 'Заполнить по ссылке'}</Button><span>Или заполните поля ниже вручную</span></div>
      {importBusy && <p className="ew-vacancy-source-note" role="status">Разбираем источник. Это может занять до двух минут.</p>}
      {importError && <p className="ew-vacancy-create-error" role="alert">{importError}</p>}
      {pendingImport && <div className="ew-vacancy-import-confirm" role="group" aria-label="Замена заполненных полей"><p>В форме уже есть данные. Заменить их данными из нового источника?</p><div><Button type="button" variant="outline" onClick={() => setPendingImport(null)}>Оставить текущие</Button><Button type="button" onClick={() => applyImport(pendingImport)}>Заменить поля</Button></div></div>}
      {draft.source && !pendingImport && <div className="ew-vacancy-imported"><Check size={16}/><p>Данные из «{draft.source.label}» добавлены. Проверьте их перед созданием вакансии.</p></div>}
      {!!draft.warnings.length && !pendingImport && <div className="ew-vacancy-import-warnings" role="status">{draft.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div>}
    </section>
    <form onSubmit={submit} aria-busy={saving}>
      <fieldset disabled={disabled || !!pendingImport}>
        <section className="ew-vacancy-section" aria-labelledby={`${id}-role-title`}><h2 id={`${id}-role-title`}>Вакансия</h2><div className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-title`}>Название вакансии<Input ref={titleInput} id={`${id}-title`} required maxLength={200} value={values.title} onChange={event => edit('title', event.target.value)} placeholder="Менеджер по работе с клиентами" autoComplete="off"/></label>
          <label htmlFor={`${id}-company`}>Компания<Input ref={companyInput} id={`${id}-company`} required maxLength={200} value={values.company} onChange={event => edit('company', event.target.value)} autoComplete="organization"/></label>
        </div></section>
        <section className="ew-vacancy-section" aria-labelledby={`${id}-conditions-title`}><h2 id={`${id}-conditions-title`}>Условия</h2><div className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-city`}>Город<Input id={`${id}-city`} maxLength={120} value={values.city} onChange={event => edit('city', event.target.value)} autoComplete="address-level2" placeholder="Необязательно"/></label>
          <label htmlFor={`${id}-format`}>Формат работы<select id={`${id}-format`} value={values.format} onChange={event => edit('format', event.target.value)}><option value="">Не указан</option>{!!values.format && !['В офисе', 'Гибрид', 'Удалённо'].includes(values.format) && <option value={values.format}>{values.format}</option>}<option>В офисе</option><option>Гибрид</option><option>Удалённо</option></select></label>
          <div className="ew-vacancy-create-wide ew-vacancy-salary"><label htmlFor={`${id}-salary-min`}>Зарплата от<Input id={`${id}-salary-min`} type="number" inputMode="numeric" min={1} max={100000000} step={1} value={details.salaryMin ?? ''} onChange={event => editDetail('salaryMin', event.target.value === '' ? null : Number(event.target.value))} placeholder="От"/></label><label htmlFor={`${id}-salary-max`}>До<Input id={`${id}-salary-max`} type="number" inputMode="numeric" min={1} max={100000000} step={1} value={details.salaryMax ?? ''} onChange={event => editDetail('salaryMax', event.target.value === '' ? null : Number(event.target.value))} placeholder="До"/></label><label htmlFor={`${id}-currency`}>Валюта<select id={`${id}-currency`} value={details.currency} onChange={event => editDetail('currency', event.target.value as EmployerVacancyDetails['currency'])}><option value="">Не указана</option><option value="RUB">₽ · RUB</option><option value="USD">$ · USD</option><option value="EUR">€ · EUR</option><option value="KZT">₸ · KZT</option><option value="BYN">BYN</option></select></label></div>
          {!hasStructuredSalary && !!values.salary && <label htmlFor={`${id}-salary-text`} className="ew-vacancy-create-wide">Условия оплаты<Input id={`${id}-salary-text`} maxLength={300} value={values.salary} onChange={event => edit('salary', event.target.value)}/></label>}
        </div><details className="ew-vacancy-more" open={conditionsOpen} onToggle={event => setConditionsOpen(event.currentTarget.open)}><summary>Дополнительные условия</summary><div className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-address`} className="ew-vacancy-create-wide">Адрес работы<Input id={`${id}-address`} maxLength={300} value={details.address} onChange={event => editDetail('address', event.target.value)} placeholder="Улица, район или ближайшее метро"/></label>
          <label htmlFor={`${id}-employment`}>Занятость<select id={`${id}-employment`} value={details.employmentType} onChange={event => editDetail('employmentType', event.target.value as EmployerVacancyDetails['employmentType'])}><option value="">Не указана</option><option value="full-time">Полная</option><option value="part-time">Частичная</option><option value="project">Проектная работа</option><option value="internship">Стажировка</option><option value="temporary">Временная работа</option></select></label>
          <label htmlFor={`${id}-schedule`}>График<Input id={`${id}-schedule`} maxLength={500} value={details.schedule} onChange={event => editDetail('schedule', event.target.value)} placeholder="Например, 5/2 с 9 до 18"/></label>
          <label htmlFor={`${id}-salary-period`}>Период оплаты<select id={`${id}-salary-period`} value={details.salaryPeriod} onChange={event => editDetail('salaryPeriod', event.target.value as EmployerVacancyDetails['salaryPeriod'])}><option value="">Не указан</option><option value="month">В месяц</option><option value="year">В год</option><option value="hour">В час</option><option value="shift">За смену</option></select></label>
          <label htmlFor={`${id}-salary-tax`}>Налоги<select id={`${id}-salary-tax`} value={details.salaryTax} onChange={event => editDetail('salaryTax', event.target.value as EmployerVacancyDetails['salaryTax'])}><option value="">Не указаны</option><option value="gross">До вычета налогов</option><option value="net">На руки</option></select></label>
          <label htmlFor={`${id}-experience`} className="ew-vacancy-create-wide">Опыт<Input id={`${id}-experience`} maxLength={500} value={details.experience} onChange={event => editDetail('experience', event.target.value)} placeholder="Например, от 3 лет"/></label>
        </div></details></section>
        <section className="ew-vacancy-section" aria-labelledby={`${id}-requirements-title`}><h2 id={`${id}-requirements-title`}>Задачи и требования</h2><div className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-responsibilities`}>Чем предстоит заниматься<Textarea id={`${id}-responsibilities`} rows={5} maxLength={4000} value={details.responsibilities} onChange={event => editDetail('responsibilities', event.target.value)} placeholder="Основные задачи сотрудника"/></label>
          <label htmlFor={`${id}-requirements`}>Что важно для этой роли<Textarea id={`${id}-requirements`} rows={5} maxLength={4000} value={details.requirements} onChange={event => editDetail('requirements', event.target.value)} placeholder="Навыки и обязательные требования"/></label>
        </div><details className="ew-vacancy-more" open={descriptionOpen} onToggle={event => setDescriptionOpen(event.currentTarget.open)}><summary>Дополнительно</summary><div className="ew-vacancy-create-fields">
          <label htmlFor={`${id}-nice`}>Будет плюсом<Textarea id={`${id}-nice`} rows={3} maxLength={4000} value={details.niceToHave} onChange={event => editDetail('niceToHave', event.target.value)}/></label>
          <label htmlFor={`${id}-benefits`}>Что предлагаете<Textarea id={`${id}-benefits`} rows={3} maxLength={4000} value={details.benefits} onChange={event => editDetail('benefits', event.target.value)} placeholder="Бонусы, обучение, ДМС"/></label>
          <label htmlFor={`${id}-description`} className="ew-vacancy-create-wide">Общее описание<Textarea id={`${id}-description`} rows={4} maxLength={10000} value={values.description} onChange={event => edit('description', event.target.value)}/></label>
        </div></details></section>
      </fieldset>
      {(localError || error) && <p className="ew-vacancy-create-error" role="alert">{localError || error}</p>}
      <footer className="ew-vacancy-create-footer"><Button type="button" variant="ghost" disabled={disabled} onClick={onBack}>Отмена</Button><Button type="submit" disabled={disabled || !!pendingImport}>{saving ? 'Создаём…' : 'Создать вакансию'}</Button></footer>
    </form>
  </main>;
}
