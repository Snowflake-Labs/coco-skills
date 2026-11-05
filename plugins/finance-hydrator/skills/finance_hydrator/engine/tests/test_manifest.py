"""ENGINE_MANIFEST.json / TEMPLATES_MANIFEST.json must match the files on disk.

If this fails after an intended change, regenerate with
``python3 engine/scripts/update_manifests.py``.
"""

from __future__ import annotations

import json
import unittest

import _support  # noqa: F401  (puts engine/scripts on sys.path)
import update_manifests as um


class ManifestTests(unittest.TestCase):
    def check(self, manifest_path, root):
        recorded = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
        actual = um.manifest_files(root, manifest_path)
        missing = sorted(set(recorded) - set(actual))
        unlisted = sorted(set(actual) - set(recorded))
        changed = sorted(k for k in set(recorded) & set(actual) if recorded[k] != actual[k])
        self.assertFalse(missing, f"listed in {manifest_path.name} but missing: {missing}")
        self.assertFalse(changed, f"hash mismatch in {manifest_path.name}: {changed}")
        self.assertFalse(unlisted, f"not listed in {manifest_path.name}: {unlisted}")

    def test_engine_manifest(self):
        self.check(um.ENGINE_MANIFEST, um.ENGINE_DIR)
        data = json.loads(um.ENGINE_MANIFEST.read_text(encoding="utf-8"))
        version = (um.ENGINE_DIR / "ENGINE_VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(data.get("engine_version"), version)

    def test_templates_manifest(self):
        self.check(um.TEMPLATES_MANIFEST, um.TEMPLATES_DIR)


if __name__ == "__main__":
    unittest.main()
