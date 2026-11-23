"""Export catalog review inputs and apply assistant-authored descriptions separately.

Run through scripts-production. This script never calls an LLM API. Export snapshots, have the
assistant fill each record's changes object, review the values, then apply the plan.
Existing nonempty copy is preserved unless --refresh explicitly allows replacement.
Examples:
  python scripts/enrich_catalog_descriptions.py --production --limit 50 --plan-file .tmp/catalog-copy.jsonl
  python scripts/enrich_catalog_descriptions.py --production --apply-plan .tmp/catalog-copy.jsonl
  # Review the preview, then repeat with --apply to write
Use --include-examples for up to three public article examples per entity; it adds
read-only database queries, not model calls. --workers bounds database concurrency.
--resume-plan skips records already exported into a checkpoint.
Production requires --production and the production scripts service.
Author visible descriptions without a final period, including multi-sentence
fields; preserve periods between sentences and search-summary punctuation. --trim-final-periods
exports guarded punctuation-only changes to existing visible descriptions.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import itertools
import json
import os
from pathlib import Path

def bootstrap(*, production=False):
    if not production or os.environ.get('APP_STAGE') != 'prod':
        raise ValueError('Run through scripts-production with --production')
    import boto3
    if not os.environ.get('DYNAMODB_TABLE'):
        stack = boto3.client('cloudformation').describe_stacks(StackName=os.environ['AWS_STACK'])['Stacks'][0]
        outputs = {item['OutputKey']: item['OutputValue'] for item in stack['Outputs']}
        os.environ['DYNAMODB_TABLE'] = outputs['DynamoDbTableName']
    return boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])

COPY_FIELDS = ('description', 'meta_description')


def candidates(table, kind):
    from boto3.dynamodb.conditions import Key

    params = {'KeyConditionExpression': Key('pk').eq(kind.upper()),
              'ProjectionExpression': 'pk, sk, #n, #d, #m',
              'ExpressionAttributeNames': {'#n': 'name', '#d': 'description', '#m': 'meta_description'}}
    if kind == 'tag':
        params.update(IndexName='TAGS_BY_TYPE_NAME', KeyConditionExpression=Key('tag_type_pk').eq('POST_TAG'))
    else:
        params['ConsistentRead'] = True
    while True:
        page = table.query(**params)
        yield from page.get('Items', [])
        if not page.get('LastEvaluatedKey'):
            return
        params['ExclusiveStartKey'] = page['LastEvaluatedKey']



def proposal(kind, item, *, refresh=False, examples=()):

    missing = [field for field in COPY_FIELDS if refresh or not item.get(field)]
    if not missing:
        return None
    slug = item['pk'][9:] if kind == 'tag' else item['sk']
    name = item.get('name') or slug
    name = name.replace('-', ' ').replace('_', ' ') if kind == 'tag' else name
    snapshot = {key: item[key] for key in ('pk', 'sk', 'name', *COPY_FIELDS) if key in item}
    return {'kind': kind, 'slug': slug, 'name': name, 'snapshot': snapshot,
            'missing': missing, 'changes': {}, 'examples': list(examples),
            'replace_existing': refresh}


def apply_record(table, record, *, apply=False):
    from botocore.exceptions import ClientError
    from validation import validate_catalog_description

    kind, slug = record['kind'], record['slug']
    if kind not in ('category', 'tag') or not isinstance(slug, str) or not slug:
        raise ValueError('Invalid catalog plan identity')
    key = {'pk': 'POST_TAG#' + slug, 'sk': 'META'} if kind == 'tag' else {'pk': kind.upper(), 'sk': slug}
    snapshot, changes = record['snapshot'], record['changes']
    if record.get('operation') not in (None, 'trim_final_period'):
        raise ValueError('Unsupported catalog plan operation')
    if not isinstance(record.get('replace_existing', False), bool):
        raise ValueError('replace_existing must be a boolean')
    if any(snapshot.get(field) != value for field, value in key.items()):
        raise ValueError('Catalog plan key does not match its identity')
    if not changes:
        return 'unchanged'
    if set(changes) - {'description', 'meta_description'}:
        raise ValueError('Catalog plan may update only description fields')
    changes = {field: validate_catalog_description(value) for field, value in changes.items()}
    if record.get('operation') == 'trim_final_period':
        from catalog_copy import without_final_period

        previous = snapshot.get('description', '')
        if (set(changes) != {'description'} or not isinstance(previous, str)
                or without_final_period(previous) == previous
                or changes['description'] != without_final_period(previous)):
            raise ValueError('Punctuation cleanup may only remove a final description period')
    if any(not value or (snapshot.get(field) and not record.get('replace_existing')) for field, value in changes.items()):
        raise ValueError('Catalog backfill must fill missing copy only')
    current = table.get_item(Key=key, ConsistentRead=True).get('Item')
    if not current:
        return 'missing'
    if all(current.get(field) == value for field, value in changes.items()):
        return 'already_applied'
    names = {'#pk': 'pk'}
    values = {}
    conditions = ['attribute_exists(#pk)']
    for index, field in enumerate(('name', 'description', 'meta_description')):
        alias = '#old' + str(index)
        names[alias] = field
        if field in snapshot:
            token = ':old' + str(index)
            values[token] = snapshot[field]
            conditions.append(f'{alias} = {token}')
        else:
            conditions.append(f'attribute_not_exists({alias})')
    setters = []
    for index, (field, value) in enumerate(changes.items()):
        alias, token = '#new' + str(index), ':new' + str(index)
        names[alias], values[token] = field, value
        setters.append(f'{alias} = {token}')
    if any(current.get(field) != snapshot.get(field) or (field in current) != (field in snapshot)
           for field in ('name', *COPY_FIELDS)):
        return 'changed_since_review'
    if not apply:
        return 'preview'
    try:
        table.update_item(Key=key, UpdateExpression='SET ' + ', '.join(setters),
                          ConditionExpression=' AND '.join(conditions),
                          ExpressionAttributeNames=names, ExpressionAttributeValues=values)
    except ClientError as error:
        if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return 'changed_since_review'
        raise
    return 'applied'


def punctuation_proposal(kind, item):
    from catalog_copy import without_final_period

    previous = item.get('description')
    if not isinstance(previous, str) or without_final_period(previous) == previous:
        return None
    record = proposal(kind, item, refresh=True)
    record.update(operation='trim_final_period', missing=['description'],
                  changes={'description': without_final_period(previous)})
    return record


def public_examples(kind, item):
    from query_dtos import ArticleQueryDTO
    from shared_utils import get_articles

    slug = item['pk'][9:] if kind == 'tag' else item['sk']
    filters = {'tags': [slug]} if kind == 'tag' else {kind: slug}
    return [{'title': article.title, 'description': article.preview}
            for article in get_articles(ArticleQueryDTO(limit=3, **filters))]


def bounded_map(function, values, workers):
    """Keep at most workers tasks queued; completed chunks can be checkpointed."""
    iterator = iter(values)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(function, value) for value in itertools.islice(iterator, workers)}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
                value = next(iterator, None)
                if value is not None:
                    pending.add(pool.submit(function, value))


def completed_identities(path):
    if path is None:
        return set()
    with path.open() as source:
        return {(record['kind'], record['slug']) for record in map(json.loads, source)}



def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', action='append', choices=('tag', 'category'))
    parser.add_argument('--limit', type=positive, default=50)
    parser.add_argument('--workers', type=positive, default=1)
    parser.add_argument('--plan-file', type=Path, help='Write exact JSONL plans without bootstrap log lines')
    parser.add_argument('--resume-plan', type=Path, help='Skip records already exported into this plan')
    parser.add_argument('--include-examples', action='store_true', help='Include public article examples for authoring; adds database reads')
    parser.add_argument('--refresh', action='store_true', help='Propose replacements for existing copy; apply remains separate')
    parser.add_argument('--trim-final-periods', action='store_true',
                        help='Export punctuation-only changes to visible catalog descriptions; apply separately')
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--apply', action='store_true', help='Apply a reviewed plan; otherwise preview only')
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    if args.workers > 16:
        parser.error('Use at most 16 workers')
    if args.trim_final_periods and (args.refresh or args.include_examples):
        parser.error('Punctuation cleanup cannot refresh copy or load examples')
    if args.apply_plan and (args.include_examples or args.refresh or args.plan_file or args.resume_plan
                            or args.trim_final_periods):
        parser.error('Apply a reviewed plan separately from exporting review inputs')
    if args.apply and not args.apply_plan:
        parser.error('--apply requires --apply-plan')
    table = bootstrap(production=args.production)
    from shared_utils import DecimalEncoder
    if args.apply_plan:
        def apply(record):
            return {'input': record, 'output': {'kind': record['kind'], 'slug': record['slug'], 'changes': record['changes'], 'applied': args.apply, 'result': apply_record(table, record, apply=args.apply)}}
        with args.apply_plan.open() as source:
            records = itertools.islice(source, args.limit)
            errors = 0
            totals = Counter()
            for raw in records:
                record = {'raw': raw.rstrip('\n')}
                try:
                    record = json.loads(raw)
                    result = apply(record)
                except Exception as error:
                    from botocore.exceptions import ClientError
                    if isinstance(error, ClientError):
                        raise
                    errors += 1
                    result = {'input': record, 'output': {'result': 'error', 'error_type': type(error).__name__, 'reason': str(error)}}
                outcome = result['output']['result']
                totals[outcome] += 1
                if outcome == 'changed_since_review':
                    errors += 1
                print(json.dumps(result, default=str), flush=True)
            print(json.dumps({'totals': totals}), flush=True)
            if errors:
                raise SystemExit(1)
        return
    completed = completed_identities(args.resume_plan)
    def stream(kind):
        for item in candidates(table, kind):
            slug = item['pk'][9:] if kind == 'tag' else item['sk']
            if ((kind, slug) not in completed
                    and (args.refresh or args.trim_final_periods
                         or not item.get('description') or not item.get('meta_description'))):
                if args.trim_final_periods and punctuation_proposal(kind, item) is None:
                    continue
                yield kind, item
    streams = [stream(kind) for kind in dict.fromkeys(args.kind or ('category', 'tag'))]
    pairs = itertools.islice((pair for group in itertools.zip_longest(*streams) for pair in group if pair is not None), args.limit)
    def plan(pair):
        kind, item = pair
        if args.trim_final_periods:
            return punctuation_proposal(kind, item)
        examples = public_examples(kind, item) if args.include_examples else ()
        return proposal(kind, item, refresh=args.refresh, examples=examples)
    destination = None
    if args.plan_file:
        mode = 'a' if args.resume_plan and args.plan_file.resolve() == args.resume_plan.resolve() else 'x'
        destination = args.plan_file.open(mode)
    try:
        for record in bounded_map(plan, pairs, args.workers):
            line = json.dumps(record, cls=DecimalEncoder, ensure_ascii=False)
            if destination:
                destination.write(line + '\n')
                destination.flush()
            else:
                print(line, flush=True)
    finally:
        if destination:
            destination.close()


if __name__ == '__main__':
    main()
