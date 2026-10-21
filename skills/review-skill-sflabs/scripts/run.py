"""Isolated-mode host entry: import only from this reviewed script directory."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from launch import main

if __name__ == '__main__':
    sys.exit(main())
