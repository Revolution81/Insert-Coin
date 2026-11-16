import os
import sys
import tempfile
from pathlib import Path

# Must be set before core.config is imported anywhere.
os.environ.setdefault("CZ_DATA", tempfile.mkdtemp(prefix="czdata-"))
os.environ.setdefault("CZ_ARGON_T", "1")
os.environ.setdefault("CZ_ARGON_M_KIB", "1024")
os.environ.setdefault("CZ_FORCE_ONLINE", "0")
os.environ.setdefault("CZ_SE", "none")          # tests opt in to the simulators
os.environ.setdefault("CZ_FINGER", "none")
os.environ.setdefault("CZ_NFC", "none")
os.environ.setdefault("CZ_BACKUP_DIRS", "")        # don't scan the host's /media
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
