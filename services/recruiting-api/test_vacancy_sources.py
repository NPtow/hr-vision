"""Private synthetic sources: actual readers plus hostile-file and SSRF cases."""
import base64
import gzip
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
import warnings
import zipfile

from domain import Problem
import vacancy_sources as source


TEXT = ('Менеджер по работе с клиентами\nКомпания: Тестовая компания\n'
        'Обязанности: сопровождение клиентов и подготовка коммерческих предложений.\n'
        'Требования: опыт продаж от двух лет, грамотная речь и работа в CRM.\n'
        'Условия: Москва, гибридный график, зарплата от 120000 до 180000 рублей до вычета налогов.')
ENGLISH = ('Account manager. Company: Test company. Responsibilities: support existing business customers, '
           'prepare commercial proposals and manage customer relationships. Requirements: two years of sales '
           'experience and CRM skills. Salary: 120000 to 180000 RUB gross per month.')


def upload(content=TEXT, filename='vacancy.txt'):
    if isinstance(content, str):
        content = content.encode('utf-8')
    return {'kind': 'file', 'filename': filename, 'contentBase64': base64.b64encode(content).decode('ascii')}


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, value in entries:
            output.writestr(name, value)
    return stream.getvalue()


def docx(value=TEXT, xml=None):
    return archive([('[Content_Types].xml', '<Types/>'), ('word/document.xml', xml or (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' +
        ''.join('<w:p><w:r><w:t>' + line + '</w:t></w:r></w:p>' for line in value.splitlines()) +
        '</w:body></w:document>'))])


def odt(value=TEXT, xml=None):
    return archive([('mimetype', 'application/vnd.oasis.opendocument.text'), ('content.xml', xml or (
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text>' +
        ''.join('<text:p>' + line + '</text:p>' for line in value.splitlines()) +
        '</office:text></office:body></office:document-content>'))])


def pdf(pages=1, readable=True):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for index in range(pages):
        page_id, content_id = len(objects) + 1, len(objects) + 2
        kids.append(page_id)
        commands = ('BT /F1 10 Tf 20 780 Td (' + ENGLISH[:120] + ') Tj 0 -20 Td (' + ENGLISH[120:] + ') Tj ET') if readable else '0 0 30 30 re f'
        commands = commands.encode('ascii')
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>'.encode(),
                        b'<< /Length ' + str(len(commands)).encode() + b' >>\nstream\n' + commands + b'\nendstream'])
    objects[1] = ('<< /Type /Pages /Kids [' + ' '.join(str(item) + ' 0 R' for item in kids) + f'] /Count {pages} >>').encode()
    result, offsets = b'%PDF-1.4\n', [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{index} 0 obj\n'.encode() + item + b'\nendobj\n'
    xref = len(result)
    result += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    result += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return result + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


def response(content, content_type='text/html; charset=utf-8', url='https://jobs.example.com/vacancy/123'):
    if isinstance(content, str):
        content = content.encode('utf-8')
    return {'content': content, 'contentType': content_type, 'url': url}


class ProblemAssertions(unittest.TestCase):
    def problem(self, status, callable, *args):
        with self.assertRaises(Problem) as result:
            callable(*args)
        self.assertEqual(result.exception.status, status, result.exception.message)
        self.assertNotIn('Traceback', result.exception.message)
        return result.exception.message


class VacancyFileTest(ProblemAssertions):
    def test_text_keeps_missing_facts_missing_and_returns_source(self):
        value = 'Разработчик Python. Пишет серверный код и тесты. Опыт от трёх лет, работа с PostgreSQL и Linux.'
        result = source.prepare({'kind': 'text', 'text': value})
        self.assertEqual(result, {'text': value, 'source': {'kind': 'text', 'label': 'Текст вакансии'}, 'warnings': []})
        self.assertNotIn('зарплата', result['text'])

    def test_empty_short_and_oversized_input_never_silently_truncate(self):
        for value in ('', 'Нужен менеджер', '\x00' + TEXT, None):
            self.problem(400 if value is None else 422, source.prepare, {'kind': 'text', 'text': value})
        self.problem(422, source.prepare, {'kind': 'text', 'text': TEXT + 'a' * source.MAX_TEXT})
        self.problem(400, source.prepare, {'kind': 'text', 'text': TEXT, 'requestId': 'extra'})
        self.problem(400, source.prepare, {'kind': 'unknown'})
        self.problem(400, source.prepare, [])

    def test_txt_markdown_csv_decodings_and_filename_sanitization(self):
        for extension in ('txt', 'md', 'csv'):
            for encoding in ('utf-8', 'utf-16', 'cp1251'):
                result = source.prepare(upload(TEXT.encode(encoding), '../safe/vacancy.' + extension))
                self.assertEqual(result['text'], TEXT)
                self.assertEqual(result['source'], {'kind': 'file', 'label': 'vacancy.' + extension})

    def test_docx_and_odt_extract_actual_document_text(self):
        for content, extension in ((docx(), 'docx'), (odt(), 'odt')):
            result = source.prepare(upload(content, 'vacancy.' + extension))
            self.assertEqual(result['text'], TEXT)
            self.assertEqual(result['warnings'], [])

    def test_file_extension_signature_size_and_base64_errors_are_typed(self):
        self.problem(415, source.prepare, upload(filename='vacancy.exe'))
        self.problem(413, source.prepare, upload(b'x' * (source.MAX_BYTES + 1)))
        self.problem(400, source.prepare, {'kind': 'file', 'filename': 'a.txt', 'contentBase64': '!!not base64!!'})
        self.problem(422, source.prepare, upload(b'%PDF-1.4 binary here', 'a.txt'))
        self.problem(422, source.prepare, upload(b'not a pdf', 'a.pdf'))
        self.problem(422, source.prepare, upload(b'not a doc', 'a.doc'))
        self.problem(422, source.prepare, upload(b'not a zip', 'a.odt'))
        self.problem(422, source.prepare, upload(b'not a zip', 'a.docx'))
        self.problem(422, source.prepare, upload(b'not rtf', 'a.rtf'))
        for extension in ('png', 'jpg', 'webp'):
            self.problem(422, source.prepare, upload(b'not an image', 'a.' + extension))

    def test_archives_reject_entities_in_utf8_and_utf16_and_wrong_container(self):
        hostile = '<!DOCTYPE a [<!ENTITY x "should never expand">]><a>&x;</a>'
        for extension, builder in (('docx', docx), ('odt', odt)):
            for encoding in ('utf-8', 'utf-16'):
                message = self.problem(422, source.prepare, upload(builder(xml=hostile.encode(encoding)), 'vacancy.' + extension))
                self.assertNotIn('резюме', message)
        self.problem(422, source.prepare, upload(odt(), 'vacancy.docx'))
        self.problem(422, source.prepare, upload(docx(), 'vacancy.odt'))

    def test_zip_bomb_and_duplicate_entries_are_rejected_before_xml(self):
        bomb = archive([('[Content_Types].xml', '<Types/>'), ('word/document.xml', b'x' * (source.MAX_ARCHIVE_BYTES + 1))])
        self.assertLess(len(bomb), source.MAX_BYTES)
        self.problem(422, source.prepare, upload(bomb, 'vacancy.docx'))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            duplicate = archive([('[Content_Types].xml', '<Types/>'), ('word/document.xml', '<x/>'), ('word/document.xml', '<y/>')])
        self.problem(422, source.prepare, upload(duplicate, 'vacancy.docx'))

    def test_rtf_unicode_ansi_and_hidden_objects_are_handled_without_execution(self):
        unicode = r'{\rtf1\ansi\ansicpg1251\uc1{\fonttbl HIDDEN FONT}{\*\generator HIDDEN TOOL}'
        unicode += ''.join(r'\par ' if char == '\n' else char if ord(char) < 128 else '\\u' + str(ord(char)) + '?' for char in TEXT)
        unicode += r'{\object\objdata HIDDEN OBJECT}{\field{\*\fldinst HIDDEN COMMAND}{\fldrslt visible}}}'
        result = source.prepare(upload(unicode.encode('ascii'), 'vacancy.rtf'))
        self.assertIn(TEXT, result['text'])
        self.assertIn('visible', result['text'])
        self.assertNotIn('HIDDEN', result['text'])
        ansi = br'{\rtf1\ansi\ansicpg1251 ' + TEXT.replace('\n', '\\par ').encode('cp1251') + b'}'
        self.assertEqual(source.prepare(upload(ansi, 'vacancy.rtf'))['text'], TEXT)
        hexed = br'{\rtf1\ansi\ansicpg1251 ' + ''.join("\\'%02x" % byte for byte in TEXT.replace('\n', ' ').encode('cp1251')).encode() + b'}'
        self.assertEqual(source.prepare(upload(hexed, 'vacancy.rtf'))['text'], TEXT.replace('\n', ' '))
        binary = br'{\rtf1 ' + ENGLISH.encode() + br'{\*\objdata\bin4 {}}{}}'
        self.assertEqual(source.prepare(upload(binary, 'vacancy.rtf'))['text'], ENGLISH)

    def test_hostile_rtf_is_bounded_and_reports_problem(self):
        for value in (br'{\rtf1', br'{\rtf1\u999999999999 x}', br'{\rtf1\bin99999999 x}',
                      br'{\rtf1\uc9999 x}', br'{\rtf1\ansicpg42 x}', br'{\rtf1' + b'{' * 129):
            self.problem(422, source.prepare, upload(value, 'vacancy.rtf'))

    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdfinfo'), 'poppler tools required for real PDF extraction')
    def test_real_text_pdf_and_page_limits(self):
        result = source.prepare(upload(pdf(), 'vacancy.pdf'))
        self.assertIn('Account manager', result['text'])
        self.assertIn('Requirements', result['text'])
        self.assertEqual(result['warnings'], [])
        self.assertIn('6 страниц', self.problem(422, source.prepare, upload(pdf(7, False), 'scanned.pdf')))
        self.assertIn('50 страниц', self.problem(422, source.prepare, upload(pdf(51), 'too-long.pdf')))

    def test_corrupt_image_after_valid_magic_does_not_reach_ocr(self):
        self.problem(422, source.prepare, upload(b'\x89PNG\r\n\x1a\n' + b'broken', 'vacancy.png'))

    def test_doc_worker_reports_missing_converter_without_server_details(self):
        with tempfile.TemporaryDirectory() as directory:
            document, target = Path(directory) / 'vacancy.doc', Path(directory) / 'out.json'
            document.write_bytes(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')
            # Do not change this unittest process's own resource limits.
            with patch('resource.setrlimit'), patch.object(source.shutil, 'which', return_value=None):
                self.assertEqual(source._extract_worker('doc', document, target), 1)
            error = json.loads(target.read_text())
            self.assertEqual(error['status'], 503)
            self.assertNotIn('antiword', error['error'])

    def test_image_pixel_bomb_rejected_before_ocr(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'huge.png'
            Image.new('L', (4500, 4500), color=255).save(path)
            with patch.object(source, '_ocr') as ocr, warnings.catch_warnings():
                warnings.simplefilter('ignore')
                self.problem(422, source._worker_image, path, Path(directory))
            ocr.assert_not_called()

    def test_scanned_pdf_has_explicit_unreadable_page_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'vacancy.pdf'
            path.write_bytes(pdf(2, False))
            with patch.object(source, '_command', side_effect=[b'Pages: 2\n', None, None]), \
                 patch.object(source, '_tool', return_value='/safe/tool'), \
                 patch.object(source, '_pdf', return_value='\f\f'), \
                 patch.object(source, '_ocr', side_effect=[ENGLISH, '']):
                result, notices = source._worker_pdf(path, Path(directory))
            self.assertIn(ENGLISH, result)
            self.assertTrue(any('не распознан' in notice for notice in notices))

    def test_real_image_ocr_when_russian_runtime_is_available(self):
        if not shutil.which('tesseract'):
            self.skipTest('Tesseract not installed')
        languages = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=5).stdout.splitlines()
        if not {'rus', 'eng'} <= set(languages):
            self.skipTest('Tesseract rus and eng language packs are required')
        from PIL import Image, ImageDraw, ImageFont
        font_path = next((path for path in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') if Path(path).is_file()), None)
        font = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default(size=30)
        image = Image.new('RGB', (1700, 400), 'white')
        draw = ImageDraw.Draw(image)
        for index, line in enumerate((ENGLISH[:100], ENGLISH[100:200], ENGLISH[200:])):
            draw.text((30, 30 + index * 70), line, fill='black', font=font)
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        result = source.prepare(upload(stream.getvalue(), 'vacancy.png'))
        self.assertIn('Account manager', result['text'])
        self.assertTrue(result['warnings'])


class FakeResponse:
    def __init__(self, content=b'', status=200, headers=None):
        self.status = status
        self.headers = headers or {'Content-Type': 'text/html'}
        self.buffer = io.BytesIO(content)

    def getheader(self, name):
        return self.headers.get(name)

    def read1(self, size):
        return self.buffer.read(size)


class FakeConnection:
    def __init__(self, reply):
        self.reply, self.transport, self.requests, self.closed = reply, None, [], False

    def request(self, method, path, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.reply

    def close(self):
        self.closed = True


class VacancyURLSecurityTest(ProblemAssertions):
    def fetch_with(self, replies, url='https://jobs.example.com/vacancy/123'):
        connections = [FakeConnection(reply) for reply in replies]
        with patch.object(source, '_resolve_public', return_value=(socket.AF_INET, ('93.184.216.34', 443))), \
             patch.object(source, '_PinnedHTTPSConnection', side_effect=connections):
            result = source._fetch_public(url)
        return result, connections

    def test_private_ips_credentials_ports_schemes_and_local_hosts_rejected(self):
        invalid = ('http://example.com/job', 'file:///etc/passwd', 'https://user:pass@example.com/job',
                   'https://example.com:444/job', 'https://127.0.0.1/job', 'https://10.130.0.18/job',
                   'https://169.254.169.254/latest/meta-data/', 'https://[::1]/job', 'https://[::ffff:127.0.0.1]/job',
                   'https://localhost/job', 'https://a.local/job', 'https://example.com\\@127.0.0.1/',
                   'https://example.com/\r\nX-Header:evil', 'https://[fe80::1%25eth0]/',
                   'https://224.0.0.1/', 'https://[2002:7f00:0001::]/', 'https://example.com:bad/job')
        for url in invalid:
            with self.subTest(url=url):
                self.problem(400, source._url, url)

    def test_unicode_path_preserved_and_fragment_never_sent(self):
        value = source._url('https://EXAMPLE.com:443/вакансия?id=123#description')
        self.assertEqual(value, 'https://example.com/%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D1%8F?id=123')

    def test_dns_private_or_mixed_answers_rejected(self):
        for answers in (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
             (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))],
            [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::ffff:169.254.169.254', 443, 0, 0))],
        ):
            with patch.object(source.socket, 'getaddrinfo', return_value=answers):
                self.problem(400, source._resolve_public, 'public-looking.example.com', 1)

    def test_numeric_loopback_spellings_are_rejected_after_resolution(self):
        with patch.object(source.socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]):
            self.problem(400, source._resolve_public, '2130706433', 1)

    def test_dns_wait_is_bounded(self):
        def slow(*args, **kwargs):
            time.sleep(0.05)
            return []
        with patch.object(source.socket, 'getaddrinfo', side_effect=slow):
            self.problem(504, source._resolve_public, 'slow.example.com', 0.01)
        time.sleep(0.05)  # Let this test's bounded DNS worker release its slot.

    def test_transport_connects_to_checked_ip_and_uses_hostname_for_tls(self):
        context, raw, secured = MagicMock(), MagicMock(), MagicMock()
        context.wrap_socket.return_value = secured
        with patch.object(source.ssl, 'create_default_context', return_value=context), \
             patch.object(source.socket, 'socket', return_value=raw), \
             patch.object(source.socket, 'getaddrinfo') as dns:
            connection = source._PinnedHTTPSConnection('jobs.example.com', (socket.AF_INET, ('93.184.216.34', 443)), 3)
            connection.connect()
        raw.connect.assert_called_once_with(('93.184.216.34', 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname='jobs.example.com')
        dns.assert_not_called()
        self.assertIs(connection.sock, secured)

    def test_public_download_contains_no_cookies_auth_or_browser_headers(self):
        result, connections = self.fetch_with([FakeResponse(TEXT.encode())])
        self.assertEqual(result['content'], TEXT.encode())
        _, _, headers = connections[0].requests[0]
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Referer', headers)
        self.assertEqual(headers['Accept-Encoding'], 'identity')
        self.assertTrue(connections[0].closed)

    def test_redirect_to_private_endpoint_rejected_before_connection(self):
        connection = FakeConnection(FakeResponse(status=302, headers={'Location': 'https://169.254.169.254/'}))
        with patch.object(source, '_resolve_public', return_value=(socket.AF_INET, ('93.184.216.34', 443))), \
             patch.object(source, '_PinnedHTTPSConnection', return_value=connection) as factory:
            self.problem(400, source._fetch_public, 'https://jobs.example.com/job')
            self.assertEqual(factory.call_count, 1)
        self.assertTrue(connection.closed)

    def test_redirect_dns_checked_again_and_pin_used_for_each_hop(self):
        first = FakeConnection(FakeResponse(status=302, headers={'Location': 'https://other.example.com/job'}))
        second = FakeConnection(FakeResponse(TEXT.encode()))
        answers = [(socket.AF_INET, ('93.184.216.34', 443)), (socket.AF_INET, ('1.1.1.1', 443))]
        with patch.object(source, '_resolve_public', side_effect=answers) as resolver, \
             patch.object(source, '_PinnedHTTPSConnection', side_effect=[first, second]) as factory:
            result = source._fetch_public('https://jobs.example.com/job')
        self.assertEqual([call.args[0] for call in resolver.call_args_list], ['jobs.example.com', 'other.example.com'])
        self.assertEqual([call.args[1] for call in factory.call_args_list], answers)
        self.assertEqual(result['url'], 'https://other.example.com/job')

    def test_redirect_limit_and_http_block_have_useful_errors(self):
        with self.assertRaises(Problem) as result:
            self.fetch_with([FakeResponse(status=302, headers={'Location': '/job'}) for _ in range(4)])
        self.assertEqual(result.exception.status, 422)
        for status in (401, 403, 429, 503):
            with self.assertRaises(Problem) as result:
                self.fetch_with([FakeResponse(b'RAW PROVIDER SECRET ERROR', status=status)])
            self.assertEqual(result.exception.message, source._BLOCKED_SITE)
            self.assertNotIn('RAW', result.exception.message)

    def test_content_length_chunked_size_and_compressed_bomb_are_bounded(self):
        responses = (FakeResponse(headers={'Content-Length': str(source.MAX_URL_BYTES + 1)}),
                     FakeResponse(b'x' * (source.MAX_URL_BYTES + 1)),
                     FakeResponse(gzip.compress(b'x' * (source.MAX_URL_BYTES + 1)), headers={'Content-Encoding': 'gzip'}))
        for reply in responses:
            with self.assertRaises(Problem) as result:
                self.fetch_with([reply])
            self.assertEqual(result.exception.status, 413)
        result, _ = self.fetch_with([FakeResponse(gzip.compress(TEXT.encode()), headers={'Content-Encoding': 'gzip'})])
        self.assertEqual(result['content'], TEXT.encode())

    def test_network_timeout_and_transport_errors_hide_details(self):
        for error, status in ((socket.timeout('internal host secret'), 504), (OSError('private cert detail'), 422)):
            connection = FakeConnection(FakeResponse())
            connection.getresponse = MagicMock(side_effect=error)
            with patch.object(source, '_resolve_public', return_value=(socket.AF_INET, ('93.184.216.34', 443))), \
                 patch.object(source, '_PinnedHTTPSConnection', return_value=connection):
                message = self.problem(status, source._fetch_public, 'https://jobs.example.com/job')
            self.assertNotIn('secret', message)
            self.assertNotIn('private', message)
            self.assertTrue(connection.closed)


class VacancyPageTest(ProblemAssertions):
    def test_jobposting_preferred_over_navigation_and_hidden_content(self):
        job = {'@type': 'JobPosting', 'title': 'Менеджер', 'description': '<p>' + TEXT + '</p>',
               'baseSalary': {'currency': 'USD', 'value': {'minValue': 80000, 'maxValue': 95000, 'unitText': 'YEAR'}}}
        page = '<html><head><script type="application/ld+json">' + json.dumps(job) + '</script></head>'
        page += '<body><nav>ALL JOBS</nav><main>OTHER UNRELATED</main><script>SECRET SCRIPT</script></body></html>'
        value, notices = source._from_response(response(page))
        self.assertIn(TEXT, value)
        self.assertIn('USD', value)
        self.assertIn('YEAR', value)
        self.assertNotIn('RUB', value)
        self.assertNotIn('ALL JOBS', value)
        self.assertNotIn('UNRELATED', value)
        self.assertNotIn('SECRET', value)
        self.assertEqual(notices, [])

    def test_jsonld_graph_and_duplicate_scripts_are_supported_but_job_lists_rejected(self):
        job = {'@type': ['Thing', 'JobPosting'], 'title': 'Sales manager', 'description': ENGLISH}
        page = '<script type="application/ld+json">' + json.dumps({'@graph': [job]}) + '</script>'
        self.assertIn(ENGLISH, source._from_response(response(page + page))[0])
        other = {**job, 'title': 'Python engineer'}
        self.problem(422, source._from_response, response(json.dumps([job, other]), 'application/json'))

    def test_html_fallback_removes_non_content_and_warns(self):
        page = '<html><head><style>HIDDEN STYLE</style></head><body><header>HIDDEN HEADER</header>'
        page += '<nav>HIDDEN NAV</nav><main><h1>Вакансия</h1><p>' + TEXT + '</p><div hidden>HIDDEN SECRET</div>'
        page += '<div aria-hidden="true">HIDDEN ACCESSIBILITY</div><form>HIDDEN FORM</form></main><footer>HIDDEN FOOTER</footer></body></html>'
        value, notices = source._from_response(response(page))
        self.assertIn(TEXT, value)
        self.assertNotIn('HIDDEN', value)
        self.assertTrue(notices)

    def test_captcha_and_unbounded_html_depth_fail_honestly(self):
        self.assertEqual(self.problem(422, source._from_response, response('<title>Доступ ограничен</title><p>Подтвердите, что вы не робот</p>')), source._BLOCKED_SITE)
        self.problem(422, source._from_response, response('<div>' * 130 + TEXT + '</div>' * 130))

    def test_plain_and_json_sources_supported_without_inventing_fields(self):
        self.assertEqual(source._from_response(response(TEXT, 'text/plain; charset=utf-8')), (TEXT, []))
        value, notices = source._from_response(response(json.dumps({'description': TEXT}), 'application/json'))
        self.assertIn('Тестовая компания', value)
        self.assertNotIn('skills', value)
        self.problem(415, source._from_response, response(b'\x00\x01', 'application/octet-stream'))

    def test_hh_uses_official_vacancy_api_and_preserves_salary_period(self):
        vacancy = {'id': '123456', 'name': 'Менеджер', 'description': '<p>' + TEXT + '</p>',
                   'employer': {'name': 'Тестовая компания'}, 'area': {'id': '1', 'name': 'Москва'},
                   'salary_range': {'from': 20, 'to': 30, 'currency': 'USD', 'gross': False,
                                    'mode': {'id': 'HOUR', 'name': 'За час'}, 'frequency': {'id': 'TWICE_PER_MONTH'}},
                   'work_format': [{'id': 'REMOTE', 'name': 'Удалённо'}], 'archived': False}
        with patch.object(source, '_fetch_public', return_value=response(json.dumps(vacancy), 'application/json')) as fetch:
            result = source.prepare({'kind': 'url', 'url': 'https://spb.hh.ru/vacancy/123456?from=test', 'source': 'hh'})
        fetch.assert_called_once_with('https://api.hh.ru/vacancies/123456')
        self.assertEqual(result['source'], {'kind': 'url', 'label': 'HH.ru', 'url': 'https://hh.ru/vacancy/123456'})
        self.assertIn('HOUR', result['text'])
        self.assertIn('USD', result['text'])
        self.assertIn('"gross": false', result['text'])
        self.assertNotIn('в месяц', result['text'])

    def test_hh_missing_pay_does_not_receive_a_default_period_or_amount(self):
        vacancy = {'id': '123456', 'name': 'Менеджер', 'description': ENGLISH.replace('Salary: 120000 to 180000 RUB gross per month.', ''),
                   'employer': {'name': 'Test'}, 'salary': None}
        with patch.object(source, '_fetch_public', return_value=response(json.dumps(vacancy), 'application/json')):
            result = source.prepare({'kind': 'url', 'url': 'https://hh.ru/vacancy/123456', 'source': 'other'})
        self.assertNotIn('Оплата', result['text'])
        self.assertNotIn('RUB', result['text'])

    def test_hh_source_mismatch_bad_api_data_and_wrong_id_rejected(self):
        self.problem(400, source.prepare, {'kind': 'url', 'url': 'https://hh.ru.evil.com/vacancy/123', 'source': 'hh'})
        self.problem(400, source.prepare, {'kind': 'url', 'url': 'https://hh.ru/search/vacancy', 'source': 'hh'})
        self.problem(400, source.prepare, {'kind': 'url', 'url': 'https://jobs.example.com/job', 'source': []})
        for content in ('not json', json.dumps({'id': '999'}), json.dumps({'id': '123456', 'description': ENGLISH})):
            with patch.object(source, '_fetch_public', return_value=response(content, 'application/json')):
                self.problem(422, source.prepare, {'kind': 'url', 'url': 'https://hh.ru/vacancy/123456', 'source': 'hh'})

    def test_hh_api_block_falls_back_to_exact_public_posting(self):
        job = {'@type': 'JobPosting', 'title': 'Менеджер', 'description': TEXT}
        url = 'https://hh.ru/vacancy/123456'
        page = response('<script type="application/ld+json">' + json.dumps(job) + '</script>', url=url)
        with patch.object(source, '_fetch_public', side_effect=[Problem(source._BLOCKED_SITE, 422), page]) as fetch:
            result = source.prepare({'kind': 'url', 'url': url, 'source': 'hh'})
        self.assertIn(TEXT, result['text'])
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ['https://api.hh.ru/vacancies/123456', url])
        archived_api_error = Problem(source._UNAVAILABLE_SITE, 422)
        archived_api_error.remote_status = 404
        with patch.object(source, '_fetch_public', side_effect=[archived_api_error, page]):
            self.assertIn(TEXT, source.prepare({'kind': 'url', 'url': url, 'source': 'hh'})['text'])
        with patch.object(source, '_fetch_public', side_effect=[Problem(source._BLOCKED_SITE, 422), {**page, 'url': 'https://hh.ru/vacancy/999'}]):
            self.problem(422, source.prepare, {'kind': 'url', 'url': url, 'source': 'hh'})

    def test_superjob_uses_public_jobposting_without_claiming_api_connection(self):
        job = {'@context': 'https://schema.org', '@type': 'JobPosting', 'title': 'Менеджер', 'description': TEXT}
        page = '<script type="application/ld+json">' + json.dumps(job) + '</script>'
        url = 'https://www.superjob.ru/vakansii/menedzher-123456.html'
        with patch.object(source, '_fetch_public', return_value=response(page, url=url)) as fetch:
            result = source.prepare({'kind': 'url', 'url': url, 'source': 'superjob'})
        fetch.assert_called_once_with(url)
        self.assertEqual(result['source'], {'kind': 'url', 'label': 'SuperJob', 'url': url})
        self.assertIn(TEXT, result['text'])

    def test_superjob_public_state_selects_exact_id_not_recommended_vacancies(self):
        def entity(id, attributes, relationships=None):
            return {'id': id, 'attributes': attributes, 'relationships': relationships or {}}
        state = {'entities': {
            'vacancyMainInfo': {'123456': entity('123456', {'profession': 'Менеджер'}),
                                '999999': entity('999999', {'profession': 'DO NOT IMPORT ANOTHER JOB'})},
            'vacancyDetailInfo': {'123456': entity('123456', {'fullText': '<p>' + TEXT + '</p>'}),
                                  '999999': entity('999999', {'fullText': 'NEIGHBOUR JOB DESCRIPTION'})},
            'vacancySalary': {'123456': entity('123456', {'minSalary': 120000, 'maxSalary': 180000},
                                              {'salaryPeriod': {'data': {'type': 'vacancyPaymentPeriodDictionary', 'id': '1'}}})},
            'vacancyPaymentPeriodDictionary': {'1': entity('1', {'defaultLabel': 'Месяц', 'key': 'month'})}}}
        page = '<script>window.APP_STATE=' + json.dumps(state) + ';</script><main>RECOMMENDED JOBS</main>'
        url = 'https://www.superjob.ru/vakansii/menedzher-123456.html'
        with patch.object(source, '_fetch_public', return_value=response(page, url=url)):
            result = source.prepare({'kind': 'url', 'url': url, 'source': 'superjob'})
        self.assertIn(TEXT, result['text'])
        self.assertIn('Месяц', result['text'])
        self.assertNotIn('RECOMMENDED', result['text'])
        self.assertNotIn('ANOTHER JOB', result['text'])
        self.assertNotIn('NEIGHBOUR', result['text'])
        self.assertEqual(result['warnings'], [])

    def test_superjob_unrecognized_html_or_other_id_do_not_become_fake_success(self):
        url = 'https://www.superjob.ru/vakansii/menedzher-123456.html'
        page = response('<h1>Рекомендации</h1><main>' + TEXT + '</main>', url=url)
        with patch.object(source, '_fetch_public', return_value=page):
            self.assertEqual(self.problem(422, source.prepare, {'kind': 'url', 'url': url, 'source': 'superjob'}), source._BLOCKED_SITE)
        with patch.object(source, '_fetch_public', return_value={**page, 'url': 'https://www.superjob.ru/vakansii/menedzher-999.html'}):
            self.problem(422, source.prepare, {'kind': 'url', 'url': url, 'source': 'superjob'})


if __name__ == '__main__':
    unittest.main()
