"""Presentation-only work scenarios, anchored to the existing assessment rubric.

No model client, extra requests, scoring, source claims or persistence here.
Legacy proposals remain valid and use the reviewed UI fallback copy.
"""
import re
import copy
import json
from pathlib import Path

LEVELS = ('L2', 'L3', 'L4', 'L5')
TEMPLATES = json.loads((Path(__file__).resolve().parents[2]/'src/scenes/vacancy-studio/task-templates.json').read_text())
SCHEMA = {'type':'object', 'properties':{
    level:{'type':'string','minLength':12,'maxLength':260} for level in LEVELS
}, 'required':list(LEVELS), 'additionalProperties':False}

def task_catalog(rubric):
    """Retain every original anchor; add positive work templates for generation."""
    if TEMPLATES['rubricVersion'] != rubric['version']:
        raise ValueError('Work task templates need a rubric version review')
    catalog=copy.deepcopy(rubric)
    for parameter in catalog['parameters']:
        texts=TEMPLATES['parameters'][parameter['parameter_id']]
        for level in parameter['levels']:
            if level['level_id'] in LEVELS:
                level['work_task_template']=texts[LEVELS.index(level['level_id'])]
    return catalog

def validate_scenarios(skill):
    scenarios = skill.get('task_scenarios')
    if scenarios is None:
        return  # Pre-feature outputs and persisted proposals retain their meaning.
    if set(scenarios) != set(LEVELS):
        raise ValueError('Incomplete work scenarios')
    values = [scenarios[level].strip() for level in LEVELS]
    if len({text.casefold() for text in values}) != len(LEVELS):
        raise ValueError('Duplicate work scenarios')
    if any(re.search(r'\b(?:junior|middle|senior|джуниор|мидл|сеньор)\b|уровень\s*[1-5]|[1-5]\s*/\s*5', text, re.I) for text in values):
        raise ValueError('Scoring labels are not work scenarios')

def prompt_guides(rubric):
    """80 per-skill, per-level instructions; no invented common level scale."""
    guides=[]
    for parameter in rubric['parameters']:
        for level in parameter['levels']:
            selectable=level['level_id'] in LEVELS
            work_template=TEMPLATES['parameters'][parameter['parameter_id']][LEVELS.index(level['level_id'])] if selectable else None
            guides.append({
                'parameter_id':parameter['parameter_id'], 'level_id':level['level_id'],
                'rubric_version':rubric['version'], 'selectable':selectable,
                'anchor':level['description'],
                'work_task_template':work_template,
                'prompt':(
                    f"Навык: {parameter['title']}. Якорь: {level['description']} "
                    + (f"Рабочий шаблон, смысл которого сохраняется: {work_template} " if selectable else '')
                    + ('На основе подтверждённого контекста вакансии сформулируй ожидаемую рабочую задачу. '
                       'Сохрани именно эти самостоятельность, сложность и границы; не добавляй умения соседнего уровня. '
                       'Если требуется помощь, назови её. Одна фраза с глаголом в инфинитиве, до 260 символов. '
                       'Без числа уровня, ранга, оценки кандидата, выдуманных KPI и цитат. Это вариант требования, а не факт.'
                       if selectable else
                       'Это диагностическое проблемное поведение, а не требование к работе. '
                       'Не переименовывай в junior и не предлагай работодателю как допустимый способ работы. '
                       'Сохрани в шкале оценки кандидата без изменения.')
                ),
            })
    return guides
