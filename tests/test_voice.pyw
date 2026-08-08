import os
import sys

# Garante import do diretório raiz
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_voice import main

if __name__ == "__main__":
    main()

