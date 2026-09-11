"""Signal addressing, independent of message rendering and inference.

Native offsets are UTF-16 code units. Labels come only from operator policy;
Signal's deprecated mention name is never an identity assertion.
"""
import re
import uuid

CATEGORIES = {'to_custos', 'to_others', 'ambient', 'unresolved'}
ELIGIBLE = {'to_custos', 'ambient'}


def identity(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def normalize(body, mentions, quote, policy, vocative, person_names):
    people = {**policy['people'], policy['self_aci']: {'label': 'Custos'}}
    targets, spans, replacements = [], [], []
    unresolved = 0
    # Boundary table prevents slicing a surrogate pair in half.
    boundary, units = {0: 0}, 0
    for index, char in enumerate(body):
        units += 2 if ord(char) > 0xffff else 1
        boundary[units] = index + 1
    native = bool(mentions) or '\ufffc' in body
    if not isinstance(mentions, list):
        mentions, unresolved = [], 1
    candidates = []
    for m in mentions[:64]:
        if not isinstance(m, dict):
            unresolved += 1
            continue
        start, length = m.get('start'), m.get('length')
        who = identity(m.get('uuid') or m.get('author'))
        valid = (type(start) is int and type(length) is int and length > 0 and
                 start in boundary and start + length in boundary)
        candidates.append({'start': start if type(start) is int else None,
                           'length': length if type(length) is int else None,
                           'aci': who, 'valid': valid})
    if len(mentions) > 64:
        unresolved += 1
    for span in candidates:
        start, length = span['start'], span['length']
        valid = span['valid'] and not any(
            other is not span and other['valid'] and
            start < other['start'] + other['length'] and other['start'] < start + length
            for other in candidates)
        who = span['aci']
        known = valid and who in people
        spans.append({**span, 'valid': valid, 'resolved': known})
        if not known:
            unresolved += 1
        if valid:
            label = people[who]['label'] if known else 'unresolved'
            replacements.append((boundary[start], boundary[start + length], '@' + label))
        if known and who not in [t['aci'] for t in targets]:
            targets.append({'aci': who, 'label': people[who]['label'], 'via': 'mention'})
    rendered = body
    for start, end, replacement in sorted(replacements, reverse=True):
        rendered = rendered[:start] + replacement + rendered[end:]
    unresolved += rendered.count('\ufffc')
    rendered = rendered.replace('\ufffc', '@[unresolved]')
    if unresolved and '@[unresolved]' not in rendered and '@unresolved' not in rendered:
        rendered += '\n[Unresolved Signal addressing metadata]'
    # Native mentions have priority over literal names and the quoted author.
    # Quoting someone's message without tagging anyone is a reply to that author.
    if not native:
        literal = []
        for who, person in people.items():
            if vocative(body, person_names(person)):
                literal.append({'aci': who, 'label': person['label'], 'via': 'literal'})
        # Unknown literal @handles are explicit addresses, too. Do not guess.
        for tag in re.findall(r'(?<![\w@])@([\w-]+)', body):
            matches = [who for who, p in people.items()
                       if tag.casefold() in {n.casefold() for n in person_names(p)}]
            if len(matches) != 1:
                unresolved += 1
        targets = literal
        if not targets and not unresolved and quote:
            who = identity(quote.get('authorUuid') or quote.get('author')) if isinstance(quote, dict) else None
            if who in people:
                targets = [{'aci': who, 'label': people[who]['label'], 'via': 'quote'}]
            else:
                unresolved += 1
    # Any ambiguous addressing is context only, even if another valid span is self.
    category = ('unresolved' if unresolved else 'to_custos' if any(
        t['aci'] == policy['self_aci'] for t in targets) else 'to_others' if targets else 'ambient')
    return {'display_body': rendered, 'mentions': spans, 'targets': targets,
            'category': category, 'unresolved': unresolved}


def routing(batch):
    """New batches only. Never reinterpret previously captured legacy requests."""
    if not all('targeting' in item for _, item in batch):
        return None
    items = [{'id': item['request_id'], **{key: item['targeting'][key]
             for key in ('targets', 'category', 'unresolved')}} for _, item in batch]
    return validate({'version': 1, 'items': items,
                     'eligible': [i['id'] for i in items if i['category'] in ELIGIBLE]})


def validate(value):
    def fail():
        raise ValueError('invalid Signal targeting provenance')
    if not isinstance(value, dict) or set(value) != {'version', 'items', 'eligible'} or type(value['version']) is not int or value['version'] != 1:
        fail()
    items = value['items']
    if not isinstance(items, list) or not 1 <= len(items) <= 32:
        fail()
    ids = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {'id', 'targets', 'category', 'unresolved'}:
            fail()
        if not isinstance(item['id'], str) or not re.fullmatch(r'signal:[0-9a-f]{64}', item['id']) or item['id'] in ids:
            fail()
        ids.append(item['id'])
        if item['category'] not in CATEGORIES or type(item['unresolved']) is not int or not 0 <= item['unresolved'] <= 32768:
            fail()
        if bool(item['unresolved']) != (item['category'] == 'unresolved'):
            fail()
        if not isinstance(item['targets'], list) or len(item['targets']) > 9:
            fail()
        seen = set()
        for target in item['targets']:
            if not isinstance(target, dict) or set(target) != {'aci', 'label', 'via'}:
                fail()
            if not identity(target['aci']) or target['aci'] in seen or target['via'] not in {'mention', 'literal', 'quote', 'direct'}:
                fail()
            seen.add(target['aci'])
            if not isinstance(target['label'], str) or not 1 <= len(target['label']) <= 160:
                fail()
    if value['eligible'] != [i['id'] for i in items if i['category'] in ELIGIBLE]:
        fail()
    return value


def contract(value):
    validate(value)
    import json
    return ('\nSignal addressing contract: add the field "reply_to_items" to the response JSON. '
            'It is a list of the message IDs this reply, reaction or deferred task addresses. '
            'For no-reply it must be []; otherwise it must be a nonempty subset of eligible IDs below. '
            'Items addressed to other people or unresolved recipients are context only: do not answer '
            'their questions, accept their tasks, or speak on their behalf. A direct question to you '
            'does not make adjacent questions eligible. Ambient eligible items allow voluntary conversation. '
            'Only eligible items may supply a deferred task. Routing data from the host: ' +
            json.dumps(value, ensure_ascii=False))
