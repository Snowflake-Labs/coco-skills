#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

REPO_BASE_URL = "https://github.com/Snowflake-Labs/cortex-code-skills/tree/main/skills"

TYPE_TAG_MAP = {
    "community": "snowflake-site:taxonomy/solution-center/verified-badge/community",
    "snowflake staff": "snowflake-site:taxonomy/solution-center/verified-badge/bundled",
    "snowflake": "snowflake-site:taxonomy/solution-center/verified-badge/bundled",
    "partner": "snowflake-site:taxonomy/solution-center/verified-badge/partner",
}


def extract_frontmatter(text):
    text = text.lstrip("\ufeff")
    fm_match = re.match(r'^\s*---\s*\n(.*?)\n---\s*(\n|$)', text, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        content = text[fm_match.end():]
    else:
        sep = re.search(r'\n\s*\n', text)
        if not sep:
            return {}, text
        fm_text = text[:sep.start()].strip()
        content = text[sep.end():]

    frontmatter = {}
    if yaml:
        try:
            frontmatter = yaml.safe_load(fm_text) or {}
        except Exception:
            frontmatter = _parse_frontmatter_fallback(fm_text)
    else:
        frontmatter = _parse_frontmatter_fallback(fm_text)
    return frontmatter, content


def _parse_frontmatter_fallback(fm_text: str) -> dict:
    result = {}
    current_key = None
    current_lines = []
    for line in fm_text.split('\n'):
        if line and not line.startswith(' ') and not line.startswith('\t') and ': ' in line:
            if current_key:
                result[current_key] = '\n'.join(current_lines).strip()
            key, _, value = line.partition(': ')
            current_key = key.strip()
            current_lines = [value.strip()]
        elif line.startswith('  ') or line.startswith('\t'):
            if current_key:
                current_lines.append(line.strip())
        else:
            if current_key and line.strip():
                current_lines.append(line.strip())
    if current_key:
        result[current_key] = '\n'.join(current_lines).strip()
    return result


def parse_skill(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()

    frontmatter, body = extract_frontmatter(text)

    name = str(frontmatter.get('name', Path(file_path).parent.name)).strip()
    title = str(frontmatter.get('title', '')).strip()
    summary = str(frontmatter.get('summary', '')).strip()
    description = str(frontmatter.get('description', '')).strip()
    author = str(frontmatter.get('author', '')).strip()
    skill_type = str(frontmatter.get('type', 'Community')).strip()
    demo_url = str(frontmatter.get('demo-url', '')).strip()
    language = str(frontmatter.get('language', 'en')).strip()
    status = str(frontmatter.get('status', 'Published')).strip()

    type_tag = TYPE_TAG_MAP.get(skill_type.lower(), TYPE_TAG_MAP['community'])
    repo_url = f"{REPO_BASE_URL}/{name}"
    related_skill = f"${name}"

    return {
        'name': name,
        'title': title,
        'summary': summary,
        'description': description,
        'body': body.strip(),
        'author': author,
        'type': skill_type,
        'type_tag': type_tag,
        'demo_url': demo_url,
        'repo_url': repo_url,
        'related_skill': related_skill,
        'language': language,
        'status': status,
    }


def main():
    parser = argparse.ArgumentParser(description='Parse SKILL.md and output JSON')
    parser.add_argument('file_path', help='Path to SKILL.md')
    parser.add_argument('--output-json', help='Output file path (default: stdout)')
    args = parser.parse_args()

    try:
        result = parse_skill(args.file_path)
        output = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output_json:
            with open(args.output_json, 'w', encoding='utf-8') as f:
                f.write(output)
            print(f"✅ Output written to {args.output_json}", file=sys.stderr)
        else:
            print(output)
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
