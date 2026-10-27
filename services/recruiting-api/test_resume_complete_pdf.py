"""Exercise the actual bounded PDF parser with synthetic multi-page documents."""
import base64
import shutil
import unittest

from candidate_resume import parse_upload


def document(pages):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    children = []
    for page in range(1, pages + 1):
        page_id = len(objects) + 1
        content_id = page_id + 1
        children.append(f'{page_id} 0 R')
        text = (f'PAGE_{page:03} Customer service, business development and commercial proposals. '
                'University degree, communication and planning.')
        commands = f'BT /F1 12 Tf 50 780 Td ({text}) Tj ET'.encode()
        objects.extend([
            (f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
             f'/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>').encode(),
            b'<< /Length ' + str(len(commands)).encode() + b' >>\nstream\n' + commands + b'\nendstream'])
    objects[1] = f'<< /Type /Pages /Kids [{" ".join(children)}] /Count {pages} >>'.encode()
    content, offsets = b'%PDF-1.4\n', [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(content))
        content += f'{index} 0 obj\n'.encode() + value + b'\nendobj\n'
    xref = len(content)
    content += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    content += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return content + (f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
                      f'startxref\n{xref}\n%%EOF').encode()


@unittest.skipUnless(shutil.which('pdftotext'), 'actual PDF runtime required')
class CompletePDFTests(unittest.TestCase):
    def test_every_page_survives_extraction_in_original_order(self):
        for pages in (2, 51, 63):
            with self.subTest(pages=pages):
                original = document(pages)
                parsed = parse_upload({'filename': 'synthetic.pdf',
                    'contentBase64': base64.b64encode(original).decode()})
                positions = []
                for page in range(1, pages + 1):
                    marker = f'PAGE_{page:03}'
                    self.assertEqual(parsed['text'].count(marker), 1, marker)
                    positions.append(parsed['text'].index(marker))
                self.assertEqual(positions, sorted(positions))
                self.assertEqual(parsed['content'], original)
