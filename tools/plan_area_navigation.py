"""Promote only source checkpoints passed by the current runtime and world scene."""
import argparse
import json
from pathlib import Path

from area_navigation_contracts import build_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tour', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--scope', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-all', action='store_true')
    args = parser.parse_args()
    result = build_manifest(args.tour, args.report, args.scope, require_all=args.require_all)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({'entries': len(result['entries']), 'pending_areas': result['pending_areas']}))


if __name__ == '__main__':
    main()
