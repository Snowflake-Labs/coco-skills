#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.parse


def build_cf_payload(parsed: dict) -> str:
    form_data = []

    if parsed.get('title'):
        form_data.append(('./data/master/title', parsed['title']))

    if parsed.get('author'):
        form_data.append(('./data/master/author', parsed['author']))

    if parsed.get('summary'):
        form_data.append(('./data/master/summary', parsed['summary']))

    body = parsed.get('body', '')
    if body:
        form_data.append(('./data/master/prompt', body))

    if parsed.get('repo_url'):
        form_data.append(('./data/master/repoUrl', parsed['repo_url']))

    if parsed.get('related_skill'):
        form_data.append(('./data/master/relatedSkill', parsed['related_skill']))

    form_data.append(('./data/master/relatedSkillUrl', ''))

    if parsed.get('demo_url'):
        form_data.append(('./data/master/demoUrl', parsed['demo_url']))

    type_tag = parsed.get('type_tag', '')
    if type_tag:
        form_data.append(('./data/master/type@TypeHint', 'String[]'))
        form_data.append(('./data/master/type', type_tag))

    return urllib.parse.urlencode(form_data, safe=":/")


def main():
    parser = argparse.ArgumentParser(description='Prepare AEM payload from parsed skill JSON')
    parser.add_argument('input_json', help='Path to JSON from parse_skill.py')
    parser.add_argument('--output-json', help='Output file path (default: stdout)')
    args = parser.parse_args()

    try:
        with open(args.input_json, 'r', encoding='utf-8') as f:
            parsed = json.load(f)

        payload = build_cf_payload(parsed)
        result = json.dumps({'cf_payload': payload}, indent=2)

        if args.output_json:
            with open(args.output_json, 'w', encoding='utf-8') as f:
                f.write(result)
            print(f"✅ Output written to {args.output_json}", file=sys.stderr)
        else:
            print(result)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
