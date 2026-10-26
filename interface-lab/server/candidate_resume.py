"""Private, bounded CV extraction. No network calls or external model receives a CV."""
import base64
import binascii
import datetime as dt
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from domain import Problem, now, require

MAX_BYTES = 5 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_ENCODED = 4 * ((MAX_BYTES + 2) // 3)
MIMES = {'pdf': 'application/pdf', 'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'txt': 'text/plain'}


def salary_value(value):
    require(value is None or (isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 100_000_000),
            'Укажите зарплату целым числом в рублях.', 400)
    return value


def start_date_value(value, *, future=False):
    if value in ('', None):
        return ''
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value),
            'Укажите дату выхода на работу.', 400)
    try:
        date = dt.date.fromisoformat(value)
    except ValueError:
        raise Problem('Укажите существующую дату выхода на работу.', 400)
    # The product uses Moscow dates; UTC midnight must not invalidate today's choice.
    today = now().astimezone(dt.timezone(dt.timedelta(hours=3))).date()
    if future:
        require(date >= today, 'Дата выхода не должна быть в прошлом.', 400)
    require(date <= today + dt.timedelta(days=730), 'Укажите дату выхода в пределах двух лет.', 400)
    return date.isoformat()


def _text_file(content):
    try:
        if content.startswith((b'\xff\xfe', b'\xfe\xff')):
            value = content.decode('utf-16')
        else:
            try:
                value = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                value = content.decode('cp1251')
    except UnicodeDecodeError:
        raise Problem('Не удалось прочитать текст. Сохраните резюме в UTF-8 или загрузите PDF/DOCX.', 422)
    require('\x00' not in value and sum(ord(char) < 32 and char not in '\n\r\t\f' for char in value) < 3,
            'Этот файл не похож на текстовое резюме. Загрузите PDF, DOCX или TXT.', 422)
    return value


def _docx(content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            require(len(entries) <= 1000 and sum(item.file_size for item in entries) <= 24 * 1024 * 1024,
                    'Файл слишком сложный. Сохраните резюме в PDF или TXT.', 422)
            require(all(not item.flag_bits & 1 for item in entries), 'Снимите пароль с резюме и загрузите его снова.', 422)
            require('[Content_Types].xml' in archive.namelist() and 'word/document.xml' in archive.namelist(),
                    'Не удалось прочитать DOCX. Пересохраните резюме и попробуйте снова.', 422)
            info = archive.getinfo('word/document.xml')
            require(info.file_size <= 4 * 1024 * 1024, 'Слишком большой текст резюме.', 422)
            xml = archive.read(info)
            markup = xml.replace(b'\x00', b'').upper()  # UTF-16/32 must not bypass the declaration check.
            require(b'<!DOCTYPE' not in markup and b'<!ENTITY' not in markup,
                    'Не удалось безопасно прочитать DOCX. Сохраните резюме в PDF или TXT.', 422)
            root = ET.fromstring(xml)
            namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            return '\n'.join(''.join(paragraph.itertext()) for paragraph in root.iter(namespace + 'p'))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, OSError):
        raise Problem('Не удалось прочитать DOCX. Пересохраните резюме и попробуйте снова.', 422)


def _pdf(content):
    require(content.lstrip().startswith(b'%PDF-'), 'Файл не похож на PDF. Проверьте формат резюме.', 422)
    require(shutil.which('pdftotext'), 'Чтение PDF временно недоступно. Загрузите DOCX или TXT.', 503)
    # Run the parser in a separately limited process: no preexec_fn in a threaded server.
    with tempfile.TemporaryDirectory(prefix='hr-cv-') as directory:
        source, target = Path(directory) / 'resume.pdf', Path(directory) / 'resume.txt'
        source.write_bytes(content)
        try:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--pdf-worker', str(source), str(target)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise Problem('Не удалось прочитать PDF. Попробуйте пересохранить его или загрузить DOCX.', 422)
        require(result.returncode == 0 and target.is_file(),
                'Не удалось прочитать PDF. Если файл защищён паролем, снимите его и загрузите снова.', 422)
        require(target.stat().st_size <= MAX_TEXT_BYTES, 'Слишком большой текст резюме. Загрузите короткую версию.', 422)
        return target.read_text(encoding='utf-8', errors='replace')


def _number(value, multiplier=1):
    raw = re.sub(r'[\s\u00a0\u202f]', '', value).replace(',', '.')
    try:
        amount = float(raw) * multiplier
        return int(amount) if amount.is_integer() and 1 <= amount <= 100_000_000 else None
    except ValueError:
        return None


def extract_preferences(value):
    """Conservative labelled facts only; historical earnings are never expectations."""
    lines = [re.sub(r'[\t \u00a0\u202f]+', ' ', line).strip() for line in value.splitlines() if line.strip()]
    fields = {}
    for line in lines:
        for key, pattern in (
            ('name', r'^(?:фио|имя|full name|name)\s*[:—–-]\s*(.{3,140})$'),
            ('city', r'^(?:город(?: проживания)?|city|location)\s*[:—–-]\s*(.{2,120})$'),
            ('residence', r'^(?:место проживания|район(?: проживания)?|метро|district|neighbou?rhood)\s*[:—–-]\s*(.{2,200})$'),
            ('desiredRole', r'^(?:желаемая (?:должность|позиция)|desired (?:role|position)|position sought)\s*[:—–-]\s*(.{2,200})$'),
        ):
            match = re.match(pattern, line, re.I)
            if match and key not in fields:
                fields[key] = match[1].strip()
        employment = re.match(r'^(?:тип занятости|занятость|employment(?: type)?)\s*[:—–-]\s*(.+)$', line, re.I)
        if employment:
            matches = [key for key, pattern in (
                ('full-time', r'\bполная(?: занятость)?\b|\bfull[ -]?time\b'),
                ('part-time', r'\b(?:частичная|неполная)(?: занятость)?\b|\bpart[ -]?time\b'),
                ('project', r'проектная(?: работа)?|project(?: work)?|contract'),
                ('internship', r'стажировка|internship')) if re.search(pattern, employment[1], re.I)]
            if len(matches) == 1:
                fields['employmentType'] = matches[0]
    if 'name' not in fields and lines:
        # A plain first-line human name is common in HH exports; headings fail this shape.
        if re.fullmatch(r'[А-ЯЁA-Z][а-яёa-z]+(?:[- ][А-ЯЁA-Z][а-яёa-z]+){1,3}', lines[0]):
            fields['name'] = lines[0]
    about = basic_about(lines)
    if about:
        fields['about'] = about
    for index, line in enumerate(lines):
        expectation = re.match(r'^(?:желаем(?:ая|ый)\s+(?:зарплата|доход)|ожидаем(?:ая|ый)\s+(?:зарплата|доход)|зарплатные ожидания|ожидания по (?:зарплате|доходу)|desired salary|expected salary|salary expectations)\s*[:—–-]?\s*(.*)$', line, re.I)
        if not expectation:
            continue
        amount = expectation[1] or (lines[index + 1] if index + 1 < len(lines) else '')
        # Currency/period ambiguity stays a question. Do not turn dollars or annual pay into monthly RUB.
        if re.search(r'[$€£]|\b(?:usd|eur|gbp|доллар\w*|евро|год\w*|annual\w*|year\w*|час\w*|hour\w*|недел\w*|week\w*|день|дня|дней|суточ\w*|сутки|суток|смен\w*|daily|day\w*)\b', amount, re.I):
            continue
        if not re.search(r'₽|руб|rub|rur|тыс', amount, re.I):
            continue
        number = r'(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)'
        unit = r'\s*(тыс\.?|[kк])?'
        pair = re.search(r'(?:от\s*)?' + number + unit + r'\s*(?:руб(?:лей|ля)?\.?|₽)?\s*(?:[—–-]|до|to)\s*' + number + unit, amount, re.I)
        if pair:
            low_scale = 1000 if pair[2] or (pair[4] and _number(pair[1]) and _number(pair[1]) < 1000) else 1
            high_scale = 1000 if pair[4] else 1
            low, high = _number(pair[1], low_scale), _number(pair[3], high_scale)
            if low and high and low <= high:
                fields.update(salaryMin=low, salaryMax=high)
                break
        single = re.match(r'\s*(?:от\s*)?' + number + unit, amount, re.I)
        if single and not re.match(r'\s*до\b', amount, re.I):
            minimum = _number(single[1], 1000 if single[2] else 1)
            if minimum:
                fields['salaryMin'] = minimum
        upper_only = re.match(r'\s*до\s*' + number + unit, amount, re.I)
        if upper_only:
            maximum = _number(upper_only[1], 1000 if upper_only[2] else 1)
            if maximum:
                fields['salaryMax'] = maximum
    for line in lines:
        match = re.match(r'^(?:выйти на работу не позднее|выйду на работу не позднее|готов[а]? (?:выйти|приступить)(?: к работе)? не позднее|дата выхода(?: на работу)?|выход на работу не позднее|latest start date|available by)\s*[:—–-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})\s*$', line, re.I)
        if match:
            try:
                date = match[1] if '-' in match[1] else dt.datetime.strptime(match[1], '%d.%m.%Y').date().isoformat()
                fields['latestStartDate'] = start_date_value(date, future=True)
                break
            except (Problem, ValueError):
                pass
    return fields


def basic_about(lines):
    """Copy a short self-description/experience passage; do not invent a summary."""
    stop = r'^(?:образование|education|навыки|skills|контакты|contacts|языки|languages|желаем\w*|зарплат\w*|дата выхода|город|(?:тип )?занятости|занятость|место проживания|район|метро|выйду|выйти|выход на работу|готова? (?:выйти|приступить)|employment|expected salary|desired salary|salary expectations|latest start|available by)\b'
    for heading in (r'^(?:о себе|about me|personal summary)\s*[:—–-]?\s*(.*)$',
                    r'^(?:опыт работы|профессиональный опыт|work experience|professional experience)\s*[:—–-]?\s*(.*)$'):
        for index, line in enumerate(lines):
            match = re.match(heading, line, re.I)
            if not match:
                continue
            passages = [match[1]] if match[1] else []
            for following in lines[index + 1:index + 6]:
                if re.match(stop, following, re.I):
                    break
                passages.append(following)
            passage = ' '.join(passages)
            passage = re.split(r'\s+(?:Образование|Education|Навыки|Skills|Контакты|Contacts)\s*:', passage, maxsplit=1, flags=re.I)[0]
            if sum(char.isalpha() for char in passage) >= 40:
                if len(passage) > 1000:
                    passage = passage[:1000].rsplit(' ', 1)[0]
                return passage.strip()
    return ''


def parse_upload(body):
    require(set(body) == {'filename', 'contentBase64'}, 'Выберите файл резюме.', 400)
    filename = body.get('filename')
    require(isinstance(filename, str) and 1 <= len(filename) <= 180 and '\x00' not in filename,
            'Проверьте название файла.', 400)
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename.replace('\\', '/').split('/')[-1]).strip()
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    require(extension in MIMES, 'Загрузите резюме в PDF, DOCX или TXT.', 415)
    encoded = body.get('contentBase64')
    require(isinstance(encoded, str) and 0 < len(encoded) <= MAX_ENCODED, 'Резюме должно быть не больше 5 МБ.', 413)
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise Problem('Не удалось получить файл. Загрузите резюме снова.', 400)
    require(0 < len(content) <= MAX_BYTES, 'Резюме должно быть не больше 5 МБ.', 413)
    raw = {'txt': _text_file, 'docx': _docx, 'pdf': _pdf}[extension](content)
    value = unicodedata.normalize('NFKC', raw).replace('\x00', '').strip()
    require(len(value.encode('utf-8')) <= MAX_TEXT_BYTES, 'Слишком большой текст резюме. Загрузите короткую версию.', 422)
    letters = sum(char.isalpha() for char in value)
    require(letters >= 80 and len(re.findall(r'\w+', value)) >= 20,
            'В файле недостаточно читаемого текста. Если это скан, загрузите резюме с текстовым слоем, DOCX или TXT.', 422)
    return {'filename': filename, 'mime': MIMES[extension], 'content': content, 'text': value,
            'fields': extract_preferences(value)}


def _pdf_worker(source, target):
    import resource
    for setting, limit in ((resource.RLIMIT_CPU, 8), (resource.RLIMIT_FSIZE, MAX_TEXT_BYTES + 1),
                           (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_AS, 512 * 1024 * 1024)):
        try:
            resource.setrlimit(setting, (limit, limit))
        except (ValueError, OSError):
            pass
    executable = shutil.which('pdftotext')
    if not executable:
        raise SystemExit(127)
    # Extract the whole document. CPU, output-size and wall-clock limits reject
    # an oversized extraction instead of accepting a silently truncated CV.
    os.execv(executable, [executable, '-layout', '-enc', 'UTF-8', source, target])


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--pdf-worker':
        _pdf_worker(sys.argv[2], sys.argv[3])
    raise SystemExit(2)
