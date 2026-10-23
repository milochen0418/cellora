"""Tests never touch the real notebooks: CELLORA_HOME points at a fresh folder."""

import os
import tempfile

os.environ["CELLORA_HOME"] = tempfile.mkdtemp(prefix="cellora-test-")
os.environ.pop("DDNS_COLLECTIONS_ROOT", None)
