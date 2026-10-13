"""Bounded local document extraction and public vacancy downloads, without an LLM.

Every result is source material, never instructions. Callers must authenticate and
rate-limit the import before calling prepare(), then validate the model result.
No uploaded file is executed, and URL requests carry no cookies or credentials.
"""
import base64
import binascii
import codecs
from html.parser import HTMLParser
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
import zipfile
import zlib

from candidate_resume import _docx, _pdf, _text_file
from domain import Problem, require


MAX_BYTES = 5 * 1024 * 1024
MAX_ENCODED = 4 * ((MAX_BYTES + 2) // 3)
MAX_URL_BYTES = 2 * 1024 * 1024
MAX_TEXT = 24_000
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PDF_PAGES = 50
MAX_OCR_PAGES = 6
DOWNLOAD_SECONDS = 10
WORKER_SECONDS = 35
FORMATS = frozenset(('pdf', 'docx', 'txt', 'md', 'csv', 'rtf', 'doc', 'odt', 'png', 'jpg', 'jpeg', 'webp'))
_DNS_SLOTS = threading.BoundedSemaphore(4)
_SOURCE_LABELS = {'hh': 'HH.ru', 'superjob': 'SuperJob', 'other': 'Ссылка на вакансию'}
_BLOCKED_SITE = 'Площадка не отдаёт описание вакансии. Загрузите файл или вставьте текст.'
_UNAVAILABLE_SITE = 'Вакансия по этой ссылке недоступна. Проверьте ссылку или загрузите файл.'
_READ_ERROR = 'Не удалось прочитать файл вакансии. Пересохраните его или вставьте текст.'


def _checked_text(value):
    require(isinstance(value, str), 'Добавьте текст вакансии.', 400)
    require(len(value) <= MAX_TEXT, 'Описание слишком длинное: разрешено до 24 000 знаков. Сократите исходный файл или текст.', 422)
    # NFKC makes visually identical input stable. No content is silently cut off.
    value = unicodedata.normalize('NFKC', value).replace('\r\n', '\n').replace('\r', '\n').strip()
    require(len(value) <= MAX_TEXT, 'Описание слишком длинное: разрешено до 24 000 знаков.', 422)
    require('\x00' not in value and not any((ord(c) < 32 and c not in '\n\t\f') or 0xD800 <= ord(c) <= 0xDFFF for c in value),
            'Не удалось прочитать текст вакансии. Загрузите текстовый документ.', 422)
    require(sum(c.isalpha() for c in value) >= 40 and len(re.findall(r'\w+', value)) >= 8,
            'Недостаточно читаемого текста вакансии. Добавьте обязанности, требования или условия.', 422)
    return value


def _translated(call, *args):
    """Reuse the tested CV readers without showing CV-specific copy to employers."""
    try:
        return call(*args)
    except Problem as error:
        message = error.message.replace('резюме', 'описание вакансии').replace('Резюме', 'Описание вакансии')
        raise Problem(message, error.status) from None


def _zip_checks(content, required):
    require(content.startswith(b'PK\x03\x04'), _READ_ERROR, 422)
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            require(len(entries) <= 1000 and len(names) == len(set(names)) and
                    sum(item.file_size for item in entries) <= MAX_ARCHIVE_BYTES,
                    'Документ слишком сложный. Сохраните описание вакансии в PDF или TXT.', 422)
            require(all(not item.flag_bits & 1 for item in entries),
                    'Снимите пароль с документа и загрузите его снова.', 422)
            require(all(name in names for name in required), _READ_ERROR, 422)
            require(all(archive.getinfo(name).file_size <= MAX_XML_BYTES for name in required),
                    'Слишком большой текст документа. Сохраните короткую версию вакансии.', 422)
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, NotImplementedError):
        raise Problem(_READ_ERROR, 422) from None


def _odt(content):
    _zip_checks(content, ('mimetype', 'content.xml'))
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            require(archive.read('mimetype').strip() == b'application/vnd.oasis.opendocument.text',
                    'Файл не похож на ODT. Проверьте формат документа.', 422)
            xml = archive.read('content.xml')
        markup = xml.replace(b'\x00', b'').upper()
        require(b'<!DOCTYPE' not in markup and b'<!ENTITY' not in markup,
                'Не удалось безопасно прочитать ODT. Сохраните документ в PDF или TXT.', 422)
        root = ET.fromstring(xml)
        ns = '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}'
        for element in root.iter():
            if element.tag == ns + 'tab':
                element.text = '\t'
            elif element.tag == ns + 'line-break':
                element.text = '\n'
            elif element.tag == ns + 's':
                # Repeated spaces do not carry vacancy facts; do not expand attacker-controlled counts.
                element.text = ' '
        return '\n'.join(''.join(node.itertext()) for node in root.iter() if node.tag in (ns + 'p', ns + 'h'))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError, OSError, NotImplementedError):
        raise Problem(_READ_ERROR, 422) from None


def _rtf(content):
    """Read RTF text groups, Unicode and code pages; ignore objects and instructions."""
    require(re.match(br'^\{\\rtf\d', content.lstrip()), 'Файл не похож на RTF. Проверьте формат документа.', 422)
    text = content.decode('latin1').strip()
    skip, uc, fallback, encoding = False, 1, 0, 'cp1252'
    stack, parts, buffer = [], [], bytearray()
    ignored = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'pict', 'object', 'objdata', 'datastore',
               'shppict', 'nonshppict', 'xmlopen', 'xmlclose', 'xmlattrname', 'xmlattrvalue',
               'listtable', 'listoverridetable', 'generator', 'fldinst', 'themedata', 'colorschememapping'}

    def flush():
        if buffer:
            try:
                parts.append(buffer.decode(encoding))
            except UnicodeDecodeError:
                raise Problem('Не удалось прочитать кодировку RTF. Пересохраните документ в DOCX или TXT.', 422) from None
            buffer.clear()

    def byte(value):
        nonlocal fallback
        if fallback:
            fallback -= 1
        elif not skip:
            buffer.append(value)

    control = re.compile(r'\\([a-zA-Z]+)(-?\d+)? ?')
    index = 0
    while index < len(text):
        char = text[index]
        if char == '{':
            flush()
            require(len(stack) < 128, 'Документ RTF слишком сложный. Сохраните его в TXT.', 422)
            stack.append((skip, uc))
            index += 1
        elif char == '}':
            flush()
            require(bool(stack), _READ_ERROR, 422)
            skip, uc = stack.pop()
            fallback = 0
            index += 1
        elif char == '\\':
            require(index + 1 < len(text), _READ_ERROR, 422)
            next_char = text[index + 1]
            if next_char in '\\{}':
                byte(ord(next_char))
                index += 2
                continue
            if next_char == "'":
                require(bool(re.fullmatch('[0-9a-fA-F]{2}', text[index + 2:index + 4])), _READ_ERROR, 422)
                byte(int(text[index + 2:index + 4], 16))
                index += 4
                continue
            match = control.match(text, index)
            if not match:
                if next_char == '*':
                    flush()
                    skip = True
                elif next_char in ('~', '_', '-'):
                    flush()
                    if fallback:
                        fallback -= 1
                    elif not skip:
                        parts.append({'~': '\u00a0', '_': '\u2011', '-': ''}[next_char])
                index += 2
                continue
            word = match[1].lower()
            require(match[2] is None or len(match[2]) <= 10, _READ_ERROR, 422)
            number = int(match[2]) if match[2] is not None else None
            index = match.end()
            if word == 'bin':
                require(number is not None and 0 <= number <= len(text) - index, _READ_ERROR, 422)
                index += number
                continue
            flush()
            if word in ignored:
                skip = True
            elif word == 'uc' and number is not None:
                require(0 <= number <= 16, _READ_ERROR, 422)
                uc = number
            elif word == 'ansicpg' and number is not None:
                require(number in (1250, 1251, 1252, 1253, 1254, 1255, 1256, 1257, 1258, 866, 65001),
                        'Эта кодировка RTF не поддерживается. Сохраните документ в DOCX или TXT.', 422)
                encoding = 'utf-8' if number == 65001 else 'cp' + str(number)
            elif word == 'u' and number is not None:
                require(-32768 <= number <= 65535, _READ_ERROR, 422)
                if not skip:
                    parts.append(chr(number % 65536))
                fallback = uc
            elif not skip and word in ('par', 'line', 'tab', 'emdash', 'endash', 'bullet', 'lquote', 'rquote', 'ldblquote', 'rdblquote'):
                parts.append({'par': '\n', 'line': '\n', 'tab': '\t', 'emdash': '—', 'endash': '–', 'bullet': '•',
                              'lquote': '‘', 'rquote': '’', 'ldblquote': '“', 'rdblquote': '”'}[word])
        else:
            if char not in '\r\n':
                byte(ord(char))
            index += 1
    flush()
    require(not stack, _READ_ERROR, 422)
    return ''.join(parts).encode('utf-16', errors='surrogatepass').decode('utf-16', errors='replace')


def _image_magic(content, extension):
    signatures = {'png': content.startswith(b'\x89PNG\r\n\x1a\n'),
                  'jpg': content.startswith(b'\xff\xd8\xff'), 'jpeg': content.startswith(b'\xff\xd8\xff'),
                  'webp': content.startswith(b'RIFF') and content[8:12] == b'WEBP'}
    require(signatures.get(extension, False), 'Содержимое изображения не соответствует формату файла.', 422)


def _extract_file(content, extension):
    warnings = []
    if extension in ('txt', 'md', 'csv'):
        require(not content.startswith((b'%PDF-', b'PK\x03\x04', b'\xd0\xcf\x11\xe0', b'\x89PNG', b'\xff\xd8\xff', b'MZ', b'\x7fELF')),
                'Содержимое файла не соответствует текстовому формату. Проверьте расширение.', 422)
        value = _translated(_text_file, content)
    elif extension == 'docx':
        _zip_checks(content, ('[Content_Types].xml', 'word/document.xml'))
        value = _translated(_docx, content)
    elif extension == 'odt':
        value = _odt(content)
    elif extension == 'rtf':
        value = _rtf(content)
    else:
        if extension == 'pdf':
            require(content.lstrip().startswith(b'%PDF-'), 'Файл не похож на PDF. Проверьте формат документа.', 422)
        elif extension == 'doc':
            require(content.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'),
                    'Файл не похож на DOC. Пересохраните его в DOCX или PDF.', 422)
        else:
            _image_magic(content, extension)
        value, warnings = _run_worker(content, extension)
    return _checked_text(value), warnings


def _run_worker(content, extension):
    """External parsers run in their own process group, with no threaded preexec_fn."""
    with tempfile.TemporaryDirectory(prefix='hr-vacancy-') as directory:
        source, target = Path(directory) / ('source.' + extension), Path(directory) / 'result.json'
        source.write_bytes(content)
        process = None
        try:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--extract-worker',
                                        extension, str(source), str(target)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            process.wait(timeout=WORKER_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            raise Problem('Не удалось разобрать файл за отведённое время. Загрузите меньший документ или вставьте текст.', 422) from None
        require(target.is_file() and target.stat().st_size <= 512 * 1024, _READ_ERROR, 422)
        try:
            result = json.loads(target.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError):
            raise Problem(_READ_ERROR, 422) from None
        if process.returncode != 0:
            if isinstance(result, dict) and result.get('status') in (413, 415, 422, 503) and isinstance(result.get('error'), str):
                raise Problem(result['error'], result['status'])
            raise Problem(_READ_ERROR, 422)
        require(isinstance(result, dict) and isinstance(result.get('text'), str) and isinstance(result.get('warnings'), list),
                _READ_ERROR, 422)
        return result['text'], result['warnings']


def _tool(name):
    executable = shutil.which(name)
    require(executable, 'Чтение этого формата временно недоступно. Загрузите DOCX или TXT.', 503)
    return executable


def _command(arguments, *, timeout=10, output=False):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                                env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
        require(result.returncode == 0, _READ_ERROR, 422)
        if output:
            require(len(result.stdout) <= 512 * 1024, _READ_ERROR, 422)
            return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise Problem(_READ_ERROR, 422) from None


def _ocr(source, target):
    executable = _tool('tesseract')
    languages = _command([executable, '--list-langs'], timeout=3, output=True).decode('utf-8', errors='replace').splitlines()
    require('rus' in languages and 'eng' in languages,
            'Распознавание сканов на русском временно недоступно. Загрузите PDF с текстом, DOCX или TXT.', 503)
    _command([executable, str(source), str(target), '-l', 'rus+eng', '--psm', '3'], timeout=12)
    text_path = Path(str(target) + '.txt')
    require(text_path.is_file() and text_path.stat().st_size <= 512 * 1024, _READ_ERROR, 422)
    return text_path.read_text(encoding='utf-8')


def _worker_image(source, directory):
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        raise Problem('Распознавание изображений временно недоступно. Загрузите PDF с текстом, DOCX или TXT.', 503) from None
    try:
        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        with Image.open(source) as image:
            expected = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'webp': 'WEBP'}[source.suffix[1:]]
            require(image.format == expected and getattr(image, 'n_frames', 1) == 1,
                    'Загрузите одно статичное изображение PNG, JPG или WEBP.', 422)
            require(0 < image.width * image.height <= MAX_IMAGE_PIXELS,
                    'Изображение слишком большое. Разрешено до 20 мегапикселей.', 422)
            image.load()
            if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                rgba = image.convert('RGBA')
                rendered = Image.new('RGB', image.size, 'white')
                rendered.paste(rgba, mask=rgba.getchannel('A'))
            else:
                rendered = image.convert('RGB')
            normalized = directory / 'page.png'
            rendered.save(normalized)
        return _ocr(normalized, directory / 'ocr'), ['Текст распознан с изображения. Проверьте цифры, зарплату и названия.']
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise Problem('Не удалось прочитать изображение. Сохраните его заново в PNG или JPG.', 422) from None


def _worker_pdf(source, directory):
    info = _command([_tool('pdfinfo'), str(source)], timeout=5, output=True).decode('utf-8', errors='replace')
    pages = re.search(r'^Pages:\s*(\d+)\s*$', info, re.M)
    require(pages is not None and 0 < int(pages[1]) <= MAX_PDF_PAGES,
            'PDF должен содержать не более 50 страниц. Для сканов разрешено до 6 страниц.', 422)
    count = int(pages[1])
    raw = _translated(_pdf, source.read_bytes())
    page_texts = raw.split('\f')
    if page_texts and not page_texts[-1].strip():
        page_texts.pop()
    require(len(page_texts) == count, 'Не удалось прочитать все страницы PDF. Пересохраните документ или загрузите DOCX.', 422)
    scanned = [index for index, text in enumerate(page_texts) if sum(char.isalpha() for char in text) < 20]
    if not scanned:
        return '\n\n'.join(page_texts), []
    require(count <= MAX_OCR_PAGES,
            'В PDF есть страницы без текстового слоя. Для распознавания загрузите не более 6 страниц.', 422)
    # Per-page rendering is capped in both dimensions to prevent extreme page-size allocations.
    renderer = _tool('pdftoppm')
    for index in scanned:
        image = directory / ('page-' + str(index))
        _command([renderer, '-f', str(index + 1), '-l', str(index + 1), '-r', '160', '-scale-to', '2400',
                  '-singlefile', '-png', str(source), str(image)], timeout=6)
        page_texts[index] = _ocr(Path(str(image) + '.png'), directory / ('ocr-' + str(index)))
    warnings = ['Текст распознан со скана PDF. Проверьте цифры, зарплату и названия.']
    if any(sum(char.isalpha() for char in text) < 20 for text in page_texts):
        warnings.append('На некоторых страницах текст не распознан. Проверьте, что в них нет важных условий вакансии.')
    return '\n\n'.join(page_texts), warnings


def _extract_worker(extension, source, target):
    import resource
    for setting, limit in ((resource.RLIMIT_CPU, 30), (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
                           (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_AS, 768 * 1024 * 1024)):
        try:
            resource.setrlimit(setting, (limit, limit))
        except (ValueError, OSError):
            pass
    source, target = Path(source), Path(target)
    try:
        if extension == 'pdf':
            value, warnings = _worker_pdf(source, source.parent)
        elif extension == 'doc':
            # antiword only reads legacy document structures; it does not run macros.
            raw = _command([_tool('antiword'), '-m', 'UTF-8.txt', str(source)], timeout=10, output=True)
            value, warnings = raw.decode('utf-8'), []
        else:
            value, warnings = _worker_image(source, source.parent)
        target.write_text(json.dumps({'text': _checked_text(value), 'warnings': warnings}, ensure_ascii=False), encoding='utf-8')
        return 0
    except Problem as error:
        target.write_text(json.dumps({'error': error.message, 'status': error.status}, ensure_ascii=False), encoding='utf-8')
        return 1
    except (OSError, ValueError, UnicodeError, MemoryError):
        target.write_text(json.dumps({'error': _READ_ERROR, 'status': 422}), encoding='utf-8')
        return 1


def _url(value):
    require(isinstance(value, str) and 0 < len(value) <= 2048, 'Добавьте ссылку на вакансию длиной до 2048 знаков.', 400)
    require(not re.search(r'[\s\x00-\x20\x7f\\]', value), 'Проверьте ссылку на вакансию.', 400)
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        require(parsed.scheme == 'https' and host and parsed.port in (None, 443) and
                parsed.username is None and parsed.password is None,
                'Нужна публичная HTTPS-ссылка на вакансию без логина, пароля и нестандартного порта.', 400)
        host = host.rstrip('.').encode('idna').decode('ascii').lower()
    except (ValueError, UnicodeError):
        raise Problem('Проверьте ссылку на вакансию.', 400) from None
    require('%' not in host and host != 'localhost' and not host.endswith(('.localhost', '.local', '.internal', '.invalid', '.test', '.onion')),
            'Импорт доступен только по публичной ссылке на вакансию.', 400)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        require(len(host) <= 253 and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) and
                all(0 < len(label) <= 63 and not label.startswith('-') and not label.endswith('-') for label in host.split('.')),
                'Проверьте адрес сайта вакансии.', 400)
        address = None
    if address is not None:
        require(_public_address(address), 'Импорт доступен только по публичной ссылке на вакансию.', 400)
    netloc = '[' + host + ']' if ':' in host else host
    path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    normalized = urlunsplit(('https', netloc, path, query, ''))
    require(len(normalized) <= 2048, 'Ссылка слишком длинная. Укажите короткую прямую ссылку на вакансию.', 400)
    return normalized


def _public_address(address):
    return bool(address.is_global and not (address.is_private or address.is_loopback or address.is_link_local or
                address.is_multicast or address.is_reserved or address.is_unspecified or
                getattr(address, 'is_site_local', False) or getattr(address, 'ipv4_mapped', None) or
                getattr(address, 'sixtofour', None) or getattr(address, 'teredo', None)))


def _resolve_public(host, timeout):
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        require(_public_address(literal), 'Импорт доступен только по публичной ссылке на вакансию.', 400)
        return (socket.AF_INET6, (str(literal), 443, 0, 0)) if literal.version == 6 else (socket.AF_INET, (str(literal), 443))
    # DNS calls have no portable socket timeout. Bound the waiter and the number
    # of outstanding resolver threads; a stuck resolver cannot grow a request queue.
    require(_DNS_SLOTS.acquire(blocking=False), 'Загрузка ссылок занята. Попробуйте через несколько секунд.', 503)
    answer = queue.Queue(maxsize=1)

    def resolve():
        try:
            answer.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError:
            answer.put(None)
        finally:
            _DNS_SLOTS.release()

    threading.Thread(target=resolve, daemon=True, name='vacancy-dns').start()
    try:
        entries = answer.get(timeout=max(0.01, timeout))
    except queue.Empty:
        raise Problem('Сайт вакансии не ответил вовремя. Попробуйте ещё раз или загрузите файл.', 504) from None
    require(entries, 'Не удалось найти сайт вакансии. Проверьте ссылку.', 422)
    endpoints = []
    for family, _, _, _, endpoint in entries:
        require(family in (socket.AF_INET, socket.AF_INET6), 'Импорт доступен только по публичной ссылке.', 400)
        try:
            address = ipaddress.ip_address(endpoint[0])
        except ValueError:
            raise Problem('Не удалось проверить адрес сайта вакансии.', 422) from None
        require(_public_address(address), 'Импорт доступен только по публичной ссылке на вакансию.', 400)
        endpoints.append((family, endpoint))
    return endpoints[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the checked IP once; keep the original host for TLS and Host."""
    def __init__(self, host, endpoint, timeout):
        context = ssl.create_default_context()
        # Explicit contexts do not receive HTTPSConnection's default ALPN setup.
        context.set_alpn_protocols(['http/1.1'])
        super().__init__(host, port=443, timeout=timeout, context=context)
        self.endpoint = endpoint
        self.transport = None

    def connect(self):
        family, address = self.endpoint
        raw = socket.socket(family, socket.SOCK_STREAM)
        try:
            raw.settimeout(self.timeout)
            raw.connect(address)
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self.transport = self.sock
        except BaseException:
            raw.close()
            raise


def _fetch_public(value):
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    current = _url(value)
    for attempt in range(4):
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Сайт вакансии не ответил вовремя. Попробуйте ещё раз или загрузите файл.', 504)
        parsed = urlsplit(current)
        endpoint = _resolve_public(parsed.hostname, remaining)
        connection = _PinnedHTTPSConnection(parsed.hostname, endpoint, max(0.01, deadline - time.monotonic()))
        try:
            user_agent = 'HRVisionVacancyImport/1.0 (https://hr-vision.158-160-179-53.sslip.io)'
            # Omit Accept rather than forcing a negotiation unsupported by some
            # job-board frontends. The response MIME is checked before parsing.
            headers = {'User-Agent': user_agent, 'Accept-Encoding': 'identity'}
            connection.request('GET', urlunsplit(('', '', parsed.path, parsed.query, '')),
                               headers=headers)
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                require(attempt < 3, 'Ссылка перенаправляет слишком много раз. Укажите прямую ссылку на вакансию.', 422)
                location = response.getheader('Location')
                require(isinstance(location, str) and len(location) <= 2048, 'Не удалось открыть перенаправление вакансии.', 422)
                current = _url(urljoin(current, location))
                continue
            if response.status in (401, 403, 429, 503):
                error = Problem(_BLOCKED_SITE, 422)
                error.remote_status = response.status
                raise error
            if response.status != 200:
                error = Problem(_UNAVAILABLE_SITE, 422)
                error.remote_status = response.status
                raise error
            content_length = response.getheader('Content-Length')
            if content_length is not None:
                require(re.fullmatch(r'[0-9]+', content_length), 'Не удалось прочитать ответ сайта вакансии.', 422)
                require(len(content_length) <= 10, 'Страница слишком большая. Загрузите файл вакансии или вставьте текст.', 413)
                require(int(content_length) <= MAX_URL_BYTES, 'Страница слишком большая. Загрузите файл вакансии или вставьте текст.', 413)
            encoding = (response.getheader('Content-Encoding') or 'identity').lower().strip()
            require(encoding in ('identity', 'gzip'), 'Сайт использует неподдерживаемый формат ответа. Загрузите файл вакансии.', 422)
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'Сайт вакансии не ответил вовремя. Попробуйте ещё раз или загрузите файл.', 504)
                if connection.transport is not None:
                    connection.transport.settimeout(remaining)
                chunk = response.read1(min(64 * 1024, MAX_URL_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                require(size <= MAX_URL_BYTES, 'Страница слишком большая. Загрузите файл вакансии или вставьте текст.', 413)
            content = b''.join(chunks)
            if encoding == 'gzip':
                try:
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    content = decoder.decompress(content, MAX_URL_BYTES + 1)
                    require(len(content) <= MAX_URL_BYTES and not decoder.unconsumed_tail,
                            'Страница слишком большая. Загрузите файл вакансии или вставьте текст.', 413)
                    require(decoder.eof and not decoder.unused_data, 'Не удалось прочитать ответ сайта вакансии.', 422)
                except zlib.error:
                    raise Problem('Не удалось прочитать ответ сайта вакансии.', 422) from None
            return {'content': content, 'contentType': response.getheader('Content-Type') or '', 'url': current}
        except (socket.timeout, TimeoutError):
            raise Problem('Сайт вакансии не ответил вовремя. Попробуйте ещё раз или загрузите файл.', 504) from None
        except (OSError, ssl.SSLError, http.client.HTTPException):
            raise Problem('Не удалось безопасно загрузить вакансию. Проверьте ссылку или загрузите файл.', 422) from None
        finally:
            connection.close()
    raise Problem('Не удалось загрузить вакансию по ссылке.', 422)


class _PageText(HTMLParser):
    _ignored = {'script', 'style', 'noscript', 'svg', 'nav', 'header', 'footer', 'form', 'button', 'iframe', 'template'}
    _blocks = {'p', 'div', 'section', 'article', 'main', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'tr', 'td', 'hr', 'dl', 'dt', 'dd'}
    _void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.main, self.ld, self.title = [], [], [], [], []
        self.script = None

    def _append(self, value):
        if any(hidden for _, hidden, _ in self.stack):
            return
        self.parts.append(value)
        if any(main for _, _, main in self.stack):
            self.main.append(value)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and (attrs.get('type') or '').split(';')[0].strip().lower() == 'application/ld+json':
            self.script = []
        hidden = tag in self._ignored or 'hidden' in attrs or attrs.get('aria-hidden') == 'true' or bool(re.search(r'display\s*:\s*none', attrs.get('style') or '', re.I))
        if tag in self._blocks:
            self._append('\n')
        if tag not in self._void:
            require(len(self.stack) < 128, 'Страница слишком сложная. Загрузите файл вакансии или вставьте текст.', 422)
            self.stack.append((tag, hidden, tag in ('main', 'article') or attrs.get('role') == 'main'))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.ld.append(''.join(self.script))
            self.script = None
        if tag in self._blocks:
            self._append('\n')
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        if any(tag == 'title' for tag, _, _ in self.stack):
            self.title.append(value)
        self._append(value)


def _plain_html(value):
    parser = _PageText()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError):
        raise Problem('Не удалось прочитать описание на сайте. Загрузите файл или вставьте текст.', 422) from None
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def _job_postings(value):
    pending, jobs, seen, count = [value], [], set(), 0
    while pending:
        item = pending.pop()
        count += 1
        require(count <= 20_000, 'Страница содержит слишком много данных. Укажите прямую ссылку на вакансию.', 422)
        if isinstance(item, dict):
            kind = item.get('@type')
            if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if fingerprint not in seen:
                    jobs.append(item)
                    seen.add(fingerprint)
            else:
                pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return jobs


def _value(value):
    if isinstance(value, str):
        return _plain_html(value) if '<' in value and '>' in value else value.strip()
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    return str(value)


def _labelled(items):
    return '\n\n'.join(label + ':\n' + rendered for label, value in items if (rendered := _value(value)))


def _job_text(job):
    return _labelled((('Название вакансии', job.get('title') or job.get('name')),
                     ('Компания', job.get('hiringOrganization')), ('Описание', job.get('description')),
                     ('Обязанности', job.get('responsibilities')), ('Требования', job.get('qualifications')),
                     ('Навыки', job.get('skills')), ('Опыт', job.get('experienceRequirements')),
                     ('Образование', job.get('educationRequirements')), ('Место работы', job.get('jobLocation')),
                     ('География кандидатов', job.get('applicantLocationRequirements')), ('Формат работы', job.get('jobLocationType')),
                     ('Занятость', job.get('employmentType')), ('График', job.get('workHours')),
                     ('Оплата', job.get('baseSalary')), ('Бонусы', job.get('incentiveCompensation')),
                     ('Условия', job.get('jobBenefits') or job.get('benefits')), ('Отрасль', job.get('industry')),
                     ('Дата начала', job.get('jobStartDate') or job.get('startDate')),
                     ('Дата публикации', job.get('datePosted')), ('Вакансия действительна до', job.get('validThrough'))))


def _hh_text(job):
    require(isinstance(job, dict) and isinstance(job.get('name'), str) and isinstance(job.get('description'), str),
            'HH не вернул описание вакансии. Загрузите файл или вставьте текст.', 422)
    return _labelled((('Название вакансии', job['name']), ('Компания', (job.get('employer') or {}).get('name')),
                     ('Описание', job['description']), ('Город или регион', job.get('area')),
                     ('Адрес места работы', job.get('address')), ('Формат работы', job.get('work_format')),
                     ('Тип занятости', job.get('employment_form') or job.get('employment')),
                     ('График', job.get('schedule')), ('Рабочие дни', job.get('work_schedule_by_days')),
                     ('Рабочие часы', job.get('working_hours')), ('Опыт', job.get('experience')),
                     ('Навыки', job.get('key_skills')), ('Языки', job.get('languages')),
                     ('Оплата (значения площадки, без пересчёта валюты и периода)', job.get('salary_range') or job.get('salary')),
                     ('Специализация', job.get('professional_roles')), ('Рабочее время', job.get('working_time_intervals')),
                     ('Рабочие дни (дополнительно)', job.get('working_days')), ('Варианты рабочего времени', job.get('working_time_modes')),
                     ('Командировки', job.get('business_trip_readiness')), ('Переезд', job.get('relocation_type')),
                     ('Дата публикации', job.get('published_at')), ('Архивная вакансия', job.get('archived'))))


def _decode_page(content, content_type):
    match = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', content_type, re.I)
    if not match:
        match = re.search(r'charset\s*=\s*[\"\']?([\w-]+)', content[:2048].decode('ascii', errors='ignore'), re.I)
    encodings = [match[1]] if match else ['utf-8-sig', 'cp1251']
    for encoding in encodings:
        try:
            normal = codecs.lookup(encoding).name
            require(normal in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'cp1251', 'cp1252', 'iso8859-1', 'cp866', 'koi8-r'),
                    'Кодировка страницы не поддерживается. Загрузите файл или вставьте текст.', 422)
            return content.decode(encoding)
        except (LookupError, UnicodeError):
            pass
    raise Problem('Не удалось прочитать текст страницы. Загрузите файл или вставьте текст.', 422)


def _from_response(response):
    content, content_type = response['content'], response['contentType']
    media_type = content_type.split(';')[0].strip().lower()
    if media_type == 'application/pdf' or content.startswith(b'%PDF-'):
        return _extract_file(content, 'pdf')
    require(media_type in ('text/html', 'application/xhtml+xml', 'application/json', 'text/plain', 'text/csv', 'text/markdown') or
            media_type.endswith('+json'), 'По ссылке нет поддерживаемого описания вакансии. Загрузите файл или вставьте текст.', 415)
    value = _decode_page(content, content_type)
    if media_type == 'application/json' or media_type.endswith('+json'):
        try:
            data = json.loads(value)
            jobs = _job_postings(data)
            require(len(jobs) <= 1, 'По ссылке найдено несколько вакансий. Укажите ссылку на одну вакансию.', 422)
            return (_job_text(jobs[0]) if jobs else _value(data)), []
        except (ValueError, RecursionError):
            raise Problem('Сайт вернул нечитаемое описание вакансии. Загрузите файл или вставьте текст.', 422) from None
    if media_type in ('text/plain', 'text/csv', 'text/markdown'):
        return value, []
    parser = _PageText()
    try:
        parser.feed(value)
        parser.close()
        jobs = []
        for raw in parser.ld:
            try:
                jobs.extend(_job_postings(json.loads(raw)))
            except (ValueError, RecursionError):
                continue
        # Duplicate script tags are common; distinct vacancies must not be merged.
        jobs = list({json.dumps(job, sort_keys=True, ensure_ascii=False): job for job in jobs}.values())
    except (ValueError, RecursionError):
        raise Problem('Не удалось прочитать описание вакансии. Загрузите файл или вставьте текст.', 422) from None
    require(len(jobs) <= 1, 'По ссылке найдено несколько вакансий. Укажите ссылку на одну вакансию.', 422)
    if jobs:
        return _job_text(jobs[0]), []
    visible = ''.join(parser.main or parser.parts)
    head = ' '.join(parser.title) + '\n' + visible[:1500]
    require(not re.search(r'captcha|access denied|just a moment|доступ ограничен|подтвердите[^.\n]{0,80}(?:человек|не робот)|checking your browser', head, re.I),
            _BLOCKED_SITE, 422)
    visible = '\n'.join(line.strip() for line in visible.splitlines() if line.strip())
    return visible, ['Описание загружено с публичной страницы. Проверьте, что в нём только нужная вакансия.']


def _superjob_page(response, vacancy_id):
    """Read the one posting in SuperJob's public SSR JSON; never its suggestions.

    APP_STATE is parsed as a JSON literal. No script, JSON.parse call or browser
    JavaScript is evaluated, and no API application key is used.
    """
    html = _decode_page(response['content'], response['contentType'])
    match = re.search(r'\b(?:window\.)?APP_STATE\s*=\s*', html)
    state = None
    if match:
        try:
            state, _ = json.JSONDecoder().raw_decode(html, match.end())
        except (ValueError, RecursionError):
            pass
    entities = state.get('entities') if isinstance(state, dict) else None
    if isinstance(entities, dict):
        def entity(kind, key=vacancy_id):
            group = entities.get(kind)
            result = group.get(str(key)) if isinstance(group, dict) else None
            return result if isinstance(result, dict) and str(result.get('id')) == str(key) else {}

        def attributes(record):
            data = record.get('attributes')
            return data if isinstance(data, dict) else {}

        def related(record, name):
            links = record.get('relationships')
            link = links.get(name) if isinstance(links, dict) else None
            data = link.get('data') if isinstance(link, dict) else link
            refs = data if isinstance(data, list) else [data]
            output = []
            for ref in refs[:30]:
                if not isinstance(ref, dict) or not isinstance(ref.get('type'), str) or not isinstance(ref.get('id'), (str, int)):
                    continue
                details = attributes(entity(ref['type'], ref['id']))
                if details:
                    output.append(details)
            return output

        vacancy = entity('vacancy')
        main = attributes(entity('vacancyMainInfo'))
        details_node, salary_node = entity('vacancyDetailInfo'), entity('vacancySalary')
        details, salary = attributes(details_node), attributes(salary_node)
        company = attributes(entity('vacancyCompanyInfo'))
        contact = attributes(entity('vacancyContactInfo'))
        description = details.get('fullText') or details.get('fullTextPlain')
        if isinstance(main.get('profession'), str) and isinstance(description, str) and description.strip():
            value = _labelled((('Название вакансии', main['profession']), ('Компания', company.get('name')),
                               ('Описание', description), ('Город', related(vacancy, 'town')),
                               ('Метро', related(vacancy, 'metroStations')), ('Адрес работы', contact.get('address')),
                               ('Обязанности', details.get('duties')), ('Требования', details.get('requirements')),
                               ('Условия', details.get('conditions')), ('Опыт', related(vacancy, 'requiredExperience')),
                               ('График', related(details_node, 'workType')), ('Образование', related(details_node, 'education')),
                               ('Навыки', related(details_node, 'professionalSkills')), ('Языки', related(details_node, 'languages')),
                               ('Формат работы', related(vacancy, 'workFormatTags')),
                               ('Занятость', related(vacancy, 'employmentTypeTags')),
                               ('Дополнительные условия', related(vacancy, 'vacancyBenefitsTags')),
                               ('Оплата (значения площадки)', salary), ('Валюта оплаты', related(salary_node, 'currency')),
                               ('Период оплаты', related(salary_node, 'salaryPeriod')),
                               ('Налоги в оплате', related(salary_node, 'salaryType')),
                               ('Дата публикации', main.get('publishedAt')), ('Дата архивирования', details.get('archivedAt'))))
            return value, []
    # JSON-LD is a second explicit, bounded representation of a single posting.
    value, notices = _from_response(response)
    require(not notices, _BLOCKED_SITE, 422)
    return value, []


def _from_url(body):
    value = _url(body.get('url'))
    requested = body.get('source', 'other')
    require(isinstance(requested, str) and requested in _SOURCE_LABELS, 'Выберите HH, SuperJob или другую ссылку.', 400)
    host = urlsplit(value).hostname
    is_hh, is_superjob = host == 'hh.ru' or host.endswith('.hh.ru'), host == 'superjob.ru' or host.endswith('.superjob.ru')
    require(requested != 'hh' or is_hh, 'Добавьте ссылку на вакансию HH.ru.', 400)
    require(requested != 'superjob' or is_superjob, 'Добавьте ссылку на вакансию SuperJob.', 400)
    if is_hh:
        parsed = urlsplit(value)
        match = re.fullmatch(r'/vacanc(?:y|ies)/(\d{1,12})/?', parsed.path)
        require(match, 'Добавьте ссылку на отдельную вакансию HH.ru, например https://hh.ru/vacancy/123456.', 400)
        canonical = 'https://hh.ru/vacancy/' + match[1]
        try:
            response = _fetch_public('https://api.hh.ru/vacancies/' + match[1])
        except Problem as error:
            if error.message != _BLOCKED_SITE and getattr(error, 'remote_status', None) not in (404, 406, 410):
                raise
            # The API may require application access while the same vacancy is
            # publicly readable. Only read that exact public posting, no login.
            public = _fetch_public(canonical)
            final = urlsplit(public['url'])
            require((final.hostname == 'hh.ru' or final.hostname.endswith('.hh.ru')) and
                    final.path.rstrip('/') == '/vacancy/' + match[1], _BLOCKED_SITE, 422)
            text, warnings = _from_response(public)
            return {'text': _checked_text(text), 'source': {'kind': 'url', 'label': 'HH.ru', 'url': canonical}, 'warnings': warnings}
        try:
            data = json.loads(response['content'])
            require(isinstance(data, dict) and str(data.get('id')) == match[1],
                    'HH вернул другую вакансию. Проверьте ссылку.', 422)
            text = _hh_text(data)
        except (ValueError, RecursionError, AttributeError, TypeError):
            raise Problem('HH не вернул описание вакансии. Загрузите файл или вставьте текст.', 422) from None
        return {'text': _checked_text(text), 'source': {'kind': 'url', 'label': 'HH.ru', 'url': canonical}, 'warnings': []}
    if is_superjob:
        match = re.fullmatch(r'/vakansii/[^/]+-(\d{1,12})\.html', urlsplit(value).path)
        require(match, 'Добавьте ссылку на отдельную вакансию SuperJob.', 400)
        response = _fetch_public(value)
        final = urlsplit(response['url'])
        require((final.hostname == 'superjob.ru' or final.hostname.endswith('.superjob.ru')) and
                re.fullmatch(r'/vakansii/[^/]+-' + match[1] + r'\.html', final.path), _BLOCKED_SITE, 422)
        text, warnings = _superjob_page(response, match[1])
    else:
        response = _fetch_public(value)
        text, warnings = _from_response(response)
    return {'text': _checked_text(text), 'source': {'kind': 'url', 'label': 'SuperJob' if is_superjob else _SOURCE_LABELS['other'],
                                                 'url': response['url']}, 'warnings': warnings}


def prepare(body):
    """Return {text, source: {kind, label, url?}, warnings}; never save a vacancy."""
    require(isinstance(body, dict), 'Выберите источник вакансии.', 400)
    kind = body.get('kind')
    if kind == 'text':
        require(set(body) <= {'kind', 'text'}, 'Проверьте поля загрузки вакансии.', 400)
        return {'text': _checked_text(body.get('text')), 'source': {'kind': 'text', 'label': 'Текст вакансии'}, 'warnings': []}
    if kind == 'url':
        require(set(body) <= {'kind', 'url', 'source'}, 'Проверьте поля загрузки вакансии.', 400)
        return _from_url(body)
    if kind == 'file':
        require(set(body) <= {'kind', 'filename', 'contentBase64'}, 'Проверьте поля загрузки вакансии.', 400)
        filename = body.get('filename')
        require(isinstance(filename, str) and 0 < len(filename) <= 180 and '\x00' not in filename and
                not any(0xD800 <= ord(char) <= 0xDFFF for char in filename),
                'Проверьте название файла вакансии.', 400)
        filename = re.sub(r'[\x00-\x1f\x7f]', '', filename.replace('\\', '/').split('/')[-1]).strip()
        extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        require(extension in FORMATS, 'Поддерживаются PDF, DOCX, DOC, ODT, RTF, TXT, MD, CSV и изображения PNG, JPG, WEBP.', 415)
        encoded = body.get('contentBase64')
        require(isinstance(encoded, str) and 0 < len(encoded) <= MAX_ENCODED, 'Файл вакансии должен быть не больше 5 МБ.', 413)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise Problem('Не удалось получить файл. Загрузите его снова.', 400) from None
        require(0 < len(content) <= MAX_BYTES, 'Файл вакансии должен быть не больше 5 МБ.', 413)
        text, warnings = _extract_file(content, extension)
        return {'text': text, 'source': {'kind': 'file', 'label': filename}, 'warnings': warnings}
    raise Problem('Выберите файл, ссылку или текст вакансии.', 400)


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--extract-worker':
        raise SystemExit(_extract_worker(sys.argv[2], sys.argv[3], sys.argv[4]))
    raise SystemExit(2)
