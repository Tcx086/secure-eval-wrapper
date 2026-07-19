from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from secure_eval_wrapper.live.bootstrap import EXPECTED_MIGRATION_CATALOG as BOOTSTRAP_CATALOG
from secure_eval_wrapper.live.migration_catalog import (
    CANONICAL_MIGRATION_CATALOG,
    CANONICAL_MIGRATION_ROWS,
    EXPECTED_MIGRATION_CATALOG,
    MIGRATION_0026_SHA256,
    validate_migration_catalog_rows,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "open-core" / "db" / "migrations"


class Phase8BShadowMigrationCatalogTests(unittest.TestCase):
    def test_files_and_bootstrap_share_exact_immutable_0001_through_0026_catalog(self):
        self.assertEqual(len(CANONICAL_MIGRATION_CATALOG), 26)
        self.assertEqual(tuple(BOOTSTRAP_CATALOG.items()), tuple(EXPECTED_MIGRATION_CATALOG.items()))
        self.assertEqual(
            MIGRATION_0026_SHA256,
            "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a",
        )
        self.assertFalse(any(MIGRATIONS.glob("0027*.sql")))
        self.assertEqual(
            tuple(path.name for path in sorted(MIGRATIONS.glob("*.sql"))),
            tuple(entry.filename for entry in CANONICAL_MIGRATION_CATALOG),
        )
        for entry in CANONICAL_MIGRATION_CATALOG:
            content = (MIGRATIONS / entry.filename).read_bytes().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry.sha256, entry.filename)

    def test_filename_hash_order_count_and_unknown_id_attacks_all_fail_closed(self):
        valid = CANONICAL_MIGRATION_ROWS
        attacks = []

        changed_hash = list(valid)
        changed_hash[0] = (changed_hash[0][0], changed_hash[0][1], "0" * 64)
        attacks.append(("old_hash", tuple(changed_hash)))

        changed_filename = list(valid)
        changed_filename[3] = (changed_filename[3][0], "0004_old_name.sql", changed_filename[3][2])
        attacks.append(("filename", tuple(changed_filename)))

        unknown = list(valid)
        unknown[9] = ("0099_unknown", unknown[9][1], unknown[9][2])
        attacks.append(("same_count_unknown_id", tuple(unknown)))

        swapped = list(valid)
        swapped[4], swapped[5] = swapped[5], swapped[4]
        attacks.append(("swapped", tuple(swapped)))
        attacks.append(("partial", valid[:-1]))
        attacks.append(("0027", valid + (("0027_attack", "0027_attack.sql", "f" * 64),)))

        for name, attacked in attacks:
            with self.subTest(name=name):
                with self.assertRaises(PermissionError):
                    validate_migration_catalog_rows(attacked)
        self.assertIs(validate_migration_catalog_rows(valid), valid)
        self.assertEqual(CANONICAL_MIGRATION_ROWS, valid)


if __name__ == "__main__":
    unittest.main()
