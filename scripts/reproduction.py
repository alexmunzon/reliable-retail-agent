"""Run from a checkout: python scripts/reproduction.py [plan|inventory|offline]."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reproduction.cli import main

if __name__ == '__main__':
    main()
