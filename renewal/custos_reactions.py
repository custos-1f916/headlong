"""Bounded Unicode emoji validation shared by native response and host delivery."""
import re

_base = r'[\U0001F300-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\u00A9\u00AE\u203C\u2049\u2122\u2139\u2190-\u21FF\u3030\u303D\u3297\u3299]'
_unit = _base + r'\ufe0f?[\U0001F3FB-\U0001F3FF]?'
_emoji = re.compile(r'(?:[\U0001F1E6-\U0001F1FF]{2}|[#*0-9]\ufe0f?\u20e3|'
                    + _unit + r'(?:\u200d' + _unit + r')*(?:[\U000E0020-\U000E007E]+\U000E007F)?)')


def valid_emoji(value):
    return isinstance(value, str) and len(value) <= 32 and _emoji.fullmatch(value) is not None
