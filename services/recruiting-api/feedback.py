"""Feedback grounded in the interviewer's actual questions, without model scoring.

The extractor is intentionally conservative. It never fills missing speech with
generic questions. Users can supply the questions they actually asked if the
transcript is unavailable. A questionnaire is frozen before answers are entered.
"""
import hashlib
import html
import re


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', value))).strip()


def vtt_turns(content, speaker):
    turns = []
    for block in re.split(r'\n\s*\n', content.replace('\r', '')):
        lines = block.splitlines()
        line = next((i for i, value in enumerate(lines) if '-->' in value), None)
        if line is None:
            continue
        stamp = lines[line].split('-->')[0].strip()
        try:
            parts = [float(p) for p in stamp.split(':')]
            seconds = sum(value * 60 ** i for i, value in enumerate(reversed(parts)))
        except ValueError:
            continue
        raw = ' '.join(lines[line + 1:])
        # Daily emits both <v Name>speech</v> and <v>Name:</v>speech.
        voice = re.search(r'<v\s+([^>]+)>', raw)
        if voice:
            name, words = clean(voice[1]), clean(raw[voice.end():])
        else:
            voice = re.search(r'<v>(.*?)</v>', raw)
            if not voice:
                continue  # Do not attribute anonymous/candidate speech to the manager.
            name, words = clean(voice[1]).rstrip(':'), clean(raw[voice.end():])
        if name == speaker and words:
            turns.append({'text': words, 'seconds': round(seconds, 2), 'source': 'transcript'})
    return turns


QUESTION = re.compile(r'^(?:и\s+|а\s+|тогда\s+|скажите[,]?\s+|подскажите[,]?\s+)*(?:как\b|како[йеяуюи]\w*\b|что\b|почему\b|зачем\b|сколько\b|где\b|когда\b|кто\b|чем\b|расскажите\b|опишите\b|приведите\b|объясните\b|можете\b|удалось\b|были?\s+ли\b)', re.I)
LOGISTICS = re.compile(r'(слыш(?:ите|но|ишь)|вид(?:ите|но|ишь)|микрофон|камер[ау]|подключени|запись\s+(?:ид[её]т|включ)|закончим|попрощаемся)', re.I)


def make_questions(turns, meeting_id, manual=False):
    questions, seen = [], set()
    # Join unfinished consecutive chunks. Final punctuation ends a spoken turn.
    grouped = []
    for turn in turns:
        words = clean(str(turn.get('text', '')))[:1500]
        if not words:
            continue
        if (not manual and grouped and not re.search(r'[.!?…]$', grouped[-1]['text'])
                and len(grouped[-1]['text']) < 500
                and 0 <= float(turn.get('seconds', 0)) - grouped[-1]['seconds'] <= 18):
            grouped[-1]['text'] += ' ' + words
        else:
            grouped.append({'text': words, 'seconds': max(0, float(turn.get('seconds', 0))), 'source': turn.get('source', 'live')})
    for turn in grouped:
        sentences = [turn['text']] if manual else re.split(r'(?<=[.!?])\s+', turn['text'])
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 12 or (not manual and (not re.search(r'[.!?…]$', sentence) or LOGISTICS.search(sentence) or not (sentence.endswith('?') or QUESTION.search(sentence)))):
                continue
            normalized = re.sub(r'\W+', ' ', sentence.casefold()).strip()
            if normalized in seen:
                continue
            seen.add(normalized)
            qid = hashlib.sha256((meeting_id + normalized).encode()).hexdigest()[:16]
            questions.append({'id': qid, 'question': sentence, 'seconds': None if manual else turn['seconds'],
                              'source': 'manual' if manual else turn['source']})
    return questions


def questionnaire(turns, meeting_id, manual=False):
    questions = make_questions(turns, meeting_id, manual)
    if not questions:
        return None
    version = hashlib.sha256('|'.join(q['id'] for q in questions).encode()).hexdigest()[:16]
    return {'id': version, 'meetingId': meeting_id, 'questions': questions}
