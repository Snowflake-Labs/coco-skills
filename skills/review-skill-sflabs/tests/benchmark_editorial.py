"""Offline editorial scoring, not a model benchmark or a source of human ratings.

Fixtures contain synthetic contrast cases and fixed enum suggestions, NOT ground
truth. A human rates every case. No judgments are shipped; missing judgments
produce an explicit unmeasured result. Unit-test ratings are arithmetic fixtures.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from editorial_broker import ACTIONS, QUESTIONS
from ingest import canonical
from protocol import strict_json

MAX_BYTES = 64 * 1024
DIMENSIONS = tuple(QUESTIONS)
FORMAT = 'human-editorial-judgments/1'


def read_json(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError()
    return strict_json(raw)


def corpus():
    value = read_json(Path(__file__).with_name('editorial_fixtures.json'))
    if (type(value) is not dict or set(value) != {'version', 'cases'}
            or value['version'] != 'synthetic-editorial-corpus/1'
            or type(value['cases']) is not list or not 1 <= len(value['cases']) <= 64):
        raise ValueError()
    seen = set()
    for case in value['cases']:
        if (type(case) is not dict or set(case) != {'id', 'dimension', 'stimulus', 'suggestion'}
                or type(case['id']) is not str or not case['id'].isascii()
                or not case['id'].replace('-', '').isalnum() or len(case['id']) > 64
                or case['id'] in seen or case['dimension'] not in DIMENSIONS
                or case['suggestion'] not in ACTIONS or type(case['stimulus']) is not str
                or not 1 <= len(case['stimulus']) <= 4096):
            raise ValueError()
        seen.add(case['id'])
    if set(case['dimension'] for case in value['cases']) != set(DIMENSIONS):
        raise ValueError()
    return value


def corpus_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def score(value, judgments=None):
    result = {'benchmark': value['version'], 'corpus_sha256': corpus_digest(value),
              'status': 'unmeasured', 'expected': len(value['cases']), 'rated': 0,
              'human_judgments_verified': False, 'live_model_calls': 0,
              'metrics': None, 'by_dimension': None}
    if judgments is None:
        return result
    if (type(judgments) is not dict or set(judgments) != {'format', 'corpus_sha256', 'ratings'}
            or judgments['format'] != FORMAT or judgments['corpus_sha256'] != result['corpus_sha256']
            or type(judgments['ratings']) is not list
            or len(judgments['ratings']) != len(value['cases'])):
        raise ValueError()
    expected = {case['id']: case for case in value['cases']}
    seen = set()
    for rating in judgments['ratings']:
        if (type(rating) is not dict or set(rating) != {
                'case_id', 'human_reviewed', 'accepted', 'usefulness', 'false_positive', 'review_seconds'}
                or type(rating['case_id']) is not str or rating['case_id'] not in expected
                or rating['case_id'] in seen or rating['human_reviewed'] is not True
                or type(rating['accepted']) is not bool or type(rating['false_positive']) is not bool
                or type(rating['usefulness']) is not int or not 1 <= rating['usefulness'] <= 5
                or type(rating['review_seconds']) is not int or not 0 <= rating['review_seconds'] <= 3600
                or (rating['accepted'] and rating['false_positive'])):
            raise ValueError()
        seen.add(rating['case_id'])

    def ratio(total, count):
        # Exact rational strings avoid rounding/order-dependent scores.
        return str(Fraction(total, count)) if count else None

    def metrics(ratings):
        count = len(ratings)
        accepted = [rating for rating in ratings if rating['accepted']]
        return {'suggestions': count, 'accepted': len(accepted),
                'acceptance_rate': ratio(len(accepted), count),
                'mean_usefulness': ratio(sum(rating['usefulness'] for rating in ratings), count),
                'mean_usefulness_per_accepted': ratio(sum(rating['usefulness'] for rating in accepted), len(accepted)),
                'false_positive_rate': ratio(sum(rating['false_positive'] for rating in ratings), count),
                'review_seconds': sum(rating['review_seconds'] for rating in ratings)}

    ratings = judgments['ratings']
    result.update(status='human-rated-unverified', rated=len(ratings), metrics=metrics(ratings),
                  by_dimension={dimension: metrics([rating for rating in ratings
                                if expected[rating['case_id']]['dimension'] == dimension])
                                for dimension in DIMENSIONS})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--judgments', type=Path, help='Explicit human-created local JSON; never synthesized ratings')
    args = parser.parse_args()
    try:
        result = score(corpus(), read_json(args.judgments) if args.judgments else None)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print('Editorial benchmark unavailable: invalid or incomplete human judgments.')
        return 3
    print(json.dumps(result, sort_keys=True))
    return 3 if result['status'] == 'unmeasured' else 0


if __name__ == '__main__':
    sys.exit(main())
