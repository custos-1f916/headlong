"""Validate field evidence before using it in a researched comparison.
JSON: {claims:[{variant,field,value,unit,sources:[{url,quote,primary}],
conflicts:[{url,quote}],resolution?}], ratios:[{variant,numerator,denominator}]}
This checks provenance completeness and arithmetic, not whether a quote is true.
For public PR live checks: custos-evidence live-capture|live-render --help.
"""
import json
import math
import sys
from urllib.parse import urlsplit


def validate(document):
    claims = document.get('claims')
    if not isinstance(claims, list) or not claims: raise ValueError('claims required')
    index = {}; unresolved = []
    for claim in claims:
        for key in ('variant', 'field', 'unit'):
            if not isinstance(claim.get(key), str) or not claim[key].strip(): raise ValueError(key + ' required')
        value = claim.get('value')
        if type(value) not in (int, float) or not math.isfinite(value): raise ValueError('finite numeric value required')
        key = (claim['variant'], claim['field'])
        if key in index: raise ValueError('duplicate variant/field; retain contradictory excerpts as conflicts')
        index[key] = claim
        sources = claim.get('sources', [])
        if not sources or not any(s.get('primary') is True for s in sources): raise ValueError('primary source required')
        for source in sources + claim.get('conflicts', []):
            u = urlsplit(source.get('url', ''))
            if u.scheme != 'https' or not u.hostname or u.username or u.password: raise ValueError('public HTTPS source URL required')
            if not isinstance(source.get('quote'), str) or not source['quote'].strip(): raise ValueError('field excerpt required')
        if claim.get('conflicts') and not str(claim.get('resolution', '')).strip(): unresolved.append(key)
    ratios = []
    for ratio in document.get('ratios', []):
        numerator = index[(ratio['variant'], ratio['numerator'])]
        denominator = index[(ratio['variant'], ratio['denominator'])]
        if numerator['unit'] != denominator['unit']: raise ValueError('convert to matching units before deriving a ratio')
        if numerator['value'] <= 0 or denominator['value'] <= 0: raise ValueError('ratio dimensions must be positive')
        # Equivalent focal length is an imaging comparison, not the optical length.
        if ratio.get('kind') == 'focal_ratio' and (ratio['numerator'] != 'actual_focal_length' or ratio['denominator'] != 'aperture'):
            raise ValueError('focal ratio requires actual_focal_length / aperture')
        ratios.append({**ratio, 'value': numerator['value'] / denominator['value']})
    return {'ready': not unresolved, 'unresolved': unresolved, 'claims': claims, 'ratios': ratios,
            'limit': 'Human/model must verify excerpts support these exact fields; validation is not independent source verification.'}


def main():
    if sys.argv[1:2] in (['live-capture'], ['live-render']):
        from custos_review_evidence import main as live_main
        return live_main(sys.argv[1:])
    if '--help' in sys.argv[1:]: print(__doc__); return 0
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072: raise ValueError('evidence ledger too large')
        result = validate(json.loads(raw)); print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ready'] else 1
    except (ValueError, TypeError, KeyError) as error:
        print(json.dumps({'ready': False, 'error': str(error)})); return 1

if __name__ == '__main__': sys.exit(main())
