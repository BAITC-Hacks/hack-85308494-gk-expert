"""Name hypotheses from explicit introductions, handoffs and addressed replies.

Acoustic IDs come exclusively from audio. Context never creates or merges voices.
All automatic names remain reviewable; silence alone is never identity evidence.
"""
from copy import deepcopy
import re

NAME = r'[А-ЯЁӘІҢҒҮҰҚӨҺA-Z][а-яёәіңғүұқөһa-z]+(?:[- ][А-ЯЁӘІҢҒҮҰҚӨҺA-Z][а-яёәіңғүұқөһa-z]+){0,2}'
STOP = {'коллеги', 'спасибо', 'хорошо', 'теперь', 'далее', 'итак', 'смотрите', 'пожалуйста', 'добрый', 'уважаемые', 'да', 'нет', 'так', 'прошу', 'понятно', 'жақсы', 'рахмет'}


def roster_names(value):
    if isinstance(value, str):
        return [part.strip() for part in re.split(r'[\n;,]+', value) if part.strip()][:32]
    return [str(part).strip() for part in (value or []) if str(part).strip()][:32]


def canonical_name(raw, roster):
    name = raw.strip(' ,.:;—–-!?')
    if not name or name.split()[0].casefold() in STOP:
        return None
    words = name.casefold().split()
    candidates = []
    for full in roster:
        target = full.casefold().split()
        if len(words) > len(target):
            continue
        good = True
        for a, b in zip(words, target):
            variants = {b, b + 'а', b + 'у', b + 'ом', b + 'е'} | {b + suffix for suffix in ('ға', 'ге', 'қа', 'ке', 'ның', 'нің')}
            if b.endswith('а'):
                variants |= {b[:-1] + ending for ending in ('е', 'у', 'ой', 'ы')}
            if b.endswith('я'):
                variants |= {b[:-1] + ending for ending in ('е', 'ю', 'и')}
            if a not in variants:
                good = False
                break
        if good:
            candidates.append(full)
    return candidates[0] if len(candidates) == 1 else name


def context_turns(segments):
    turns = []
    for segment in segments:
        current = {key: segment.get(key) for key in ('speaker_id', 'text', 'start', 'end', 'overlap')}
        current['start'] = float(current['start'] or 0)
        current['end'] = float(current['end'] or current['start'])
        if turns and current['speaker_id'] and turns[-1]['speaker_id'] == current['speaker_id'] and not current['overlap'] and not turns[-1]['overlap'] and current['start'] - turns[-1]['end'] < 3:
            turns[-1]['text'] += ' ' + current['text']
            turns[-1]['end'] = current['end']
        else:
            turns.append(current)
    return turns


def resolve_names(segments, roster=None, overrides=None):
    segments = deepcopy(segments)
    roster = roster_names(roster)
    profiles = {}
    for item in segments:
        key = item.get('speaker_id')
        if key and key not in profiles:
            profiles[key] = {'id': key, 'label': 'Голос ' + str(len(profiles) + 1), 'name': None,
                             'name_status': 'unresolved', 'evidence': [], 'candidates': [],
                             'sample_start': item['start'], 'sample_end': min(item.get('end', item['start'] + 5), item['start'] + 8)}
        elif key and item.get('end', 0) - item['start'] > profiles[key]['sample_end'] - profiles[key]['sample_start']:
            profiles[key].update(sample_start=item['start'], sample_end=min(item['end'], item['start'] + 8))
    turns = context_turns(segments)

    def add(target, name, kind, turn, strength):
        name = canonical_name(name, roster)
        if name and target in profiles:
            evidence = {'name': name, 'type': kind, 'quote': turn['text'], 'start': turn['start'],
                        'end': turn['end'], 'strength': strength}
            if evidence not in profiles[target]['evidence']:
                profiles[target]['evidence'].append(evidence)

    for index, turn in enumerate(turns):
        key, text = turn['speaker_id'], turn['text'] or ''
        if not key or turn['overlap']:
            continue
        for pattern in (rf'(?i:меня зовут|моё имя|мое имя|на связи|менің атым)\s+({NAME})',
                        rf'^(?i:я)\s*[—–-]\s*({NAME})', rf'^(?i:это)\s+({NAME})[,.!]',
                        rf'^(?i:мен)\s+({NAME})(?i:мын|мін|бын|бін|пын|пін)\b'):
            match = re.search(pattern, text)
            if match and not text.rstrip().endswith('?'):
                add(key, match.group(1), 'self_introduction', turn, 3)
        following = turns[index + 1] if index + 1 < len(turns) else None
        previous = turns[index - 1] if index else None
        # A named person who resumes after thanking/asking them to stop is the previous voice.
        backward = re.search(rf'(?i:спасибо|благодарю|рахмет)[,\s]+({NAME})', text)
        stop = re.search(rf'({NAME}),\s*(?i:не перебивайте|не перебивай|дайте договорить|дай договорить|помолчите|остановитесь)', text)
        if previous and previous['speaker_id'] != key and not previous['overlap'] and turn['start'] - previous['end'] < 6:
            match = backward or stop
            if match:
                add(previous['speaker_id'], match.group(1), 'address_to_previous' if backward else 'interruption_address', turn, 2)
        if not following or following['speaker_id'] == key or not following['speaker_id'] or following['overlap']:
            continue
        # Long gaps/overlapping replies do not establish who accepted the floor.
        gap = following['start'] - turn['end']
        if not -.15 <= gap <= 8 or following['end'] - following['start'] < .65:
            continue
        handoffs = []
        patterns = (
            rf'({NAME}),?\s*(?i:вам слово|тебе слово|сізге сөз|сөз сізде)',
            rf'(?i:слово предоставляется|передаю слово|слово|начн[её]м с|теперь послушаем)\s+({NAME})',
            rf'(?i:сөзді)\s+({NAME})\s+(?i:берейік|беремін)',
            rf'({NAME}),\s*(?i:расскажите|доложите|начинайте|продолжайте|что у вас|как у вас|у вас|сіз айт)',
            rf'({NAME})[,.!?]\s*(?i:вы|ты|сіз|ваш комментарий|ваша позиция)\b',
        )
        for pattern in patterns:
            handoffs += [m.group(1) for m in re.finditer(pattern, text)]
        handoffs = list(dict.fromkeys(name for raw in handoffs if (name := canonical_name(raw, roster))))
        if len(handoffs) == 1:
            add(following['speaker_id'], handoffs[0], 'floor_handoff', turn, 2)
        elif not handoffs:
            address = re.search(rf'(?:^|[.!?]\s+)({NAME}),\s+', text)
            acknowledges = re.match(r'(?i)^(?:да\b|хорошо\b|понял\w*\b|сделаю\b|принято\b|жақсы\b|болады\b|иә\b)', following['text'])
            if address and acknowledges:
                add(following['speaker_id'], address.group(1), 'addressed_reply', turn, 1)

    for profile in profiles.values():
        evidence = profile['evidence']
        profile['candidates'] = list(dict.fromkeys(e['name'] for e in evidence))
        introductions = {e['name'] for e in evidence if e['type'] == 'self_introduction'}
        pool = introductions or set(profile['candidates'])
        if len(pool) == 1:
            profile['name'] = next(iter(pool))
            profile['name_status'] = 'self_introduced' if introductions else 'inferred'
        elif len(pool) > 1:
            profile['name_status'] = 'conflict'
    # One name occurring on two acoustic voices needs a review; do not silently merge.
    by_name = {}
    for key, profile in profiles.items():
        if profile['name']:
            by_name.setdefault(profile['name'].casefold(), []).append(key)
    for keys in by_name.values():
        if len(keys) > 1:
            for key in keys:
                profiles[key].update(name=None, name_status='conflict')
    for key, name in (overrides or {}).items():
        if key in profiles:
            profiles[key].update(name=str(name).strip() or None, name_status='confirmed' if str(name).strip() else 'unresolved')
    for item in segments:
        profile = profiles.get(item.get('speaker_id'))
        if profile:
            name = profile['name']
            item['speaker'] = (f'{name} (по контексту)' if profile['name_status'] == 'inferred' else name) or profile['label']
            item['speaker_name'] = name
            item['name_status'] = profile['name_status']
        else:
            item['speaker'] = 'Наложение голосов' if item.get('overlap') else 'Говорящий не определён'
            item['speaker_name'] = None
            item['name_status'] = 'unresolved'
    return segments, list(profiles.values())
