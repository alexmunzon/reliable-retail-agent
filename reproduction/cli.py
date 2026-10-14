"""Only offline commands exist; outputs are exclusive and never overwritten."""
import argparse
import json
from pathlib import Path
from reproduction.inventory import inventory
from reproduction.manifest import dry_plan

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inventory','plan','offline'], nargs='?', default='plan')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'offline':
        from reproduction.runner import offline_grid
        result = offline_grid()
    else:
        result = inventory() if args.command == 'inventory' else dry_plan()
    text = json.dumps(result, indent=2)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            stream.write(text)
        print(f'Offline evidence saved: {args.output}')
    else:
        print(text, end='')

if __name__ == '__main__':
    main()
