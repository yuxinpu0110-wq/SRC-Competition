from pathlib import Path
import json
import tempfile
import unittest

from run_match import load_config
from strategy_contract import normalize_verified_contract, uses_verified_control
import test_v21 as fixtures

ROOT = Path(__file__).resolve().parents[3]


class ContractTests(unittest.TestCase):
    def test_known_legacy_and_v21_migration(self):
        self.assertFalse(uses_verified_control({}))
        self.assertTrue(uses_verified_control({'upgrade_version': '2.1'}))
        self.assertEqual(normalize_verified_contract({'upgrade_version': '2.1'})['control_contract_version'], 2)

    def test_explicit_v22_inherits_verified_control(self):
        self.assertTrue(uses_verified_control({'strategy_version': '2.2', 'control_contract_version': 2}))

    def test_unknown_and_wrong_type_fail_closed(self):
        for config in ({'upgrade_version': '2.2'}, {'strategy_version': '2.2'},
                       {'upgrade_version': '2.3'}, {'strategy_version': 2.2},
                       {'control_contract_version': True}, {'control_contract_version': '2'},
                       {'control_contract_version': None}, {'control_contract_version': 3}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                normalize_verified_contract(config)

    def test_renamed_label_cannot_skip_startup_validation(self):
        base = json.loads((ROOT/'configs/v2.1-foundation.json').read_text(encoding='utf-8'))
        base.update(upgrade_version='2.2', control_contract_version=2)
        for updates in ({'complete_frame': False}, {'enable_one_touch': True},
                        {'enable_dynamic_keeper': True}, {'vision_max_age_s': '0.2'}):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'config.json'
                path.write_text(json.dumps(dict(base, **updates)), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_config(path)


class RelabelledSafetyTests(fixtures.V21IntegrationTests):
    def setUp(self):
        super().setUp()
        self.c = dict(self.c, upgrade_version=None, control_contract_version=2)
        self.play = self.NormalPlay(self.c)

    # Run the meaningful safety regressions with a different strategy label.
    test_stale = fixtures.V21IntegrationTests.test_v21_stale_preempts_every_attack_state
    test_duplicate = fixtures.V21IntegrationTests.test_v21_duplicate_does_not_emit_or_advance_stability
    test_departure = fixtures.V21IntegrationTests.test_v21_departure_enters_official_observe_flight
    test_no_departure = fixtures.V21IntegrationTests.test_v21_no_departure_enters_official_release
    test_transaction = fixtures.V21IntegrationTests.test_v21_actual_skill_pulses_share_tx_and_confirm_once


for name in dir(fixtures.V21IntegrationTests):
    if name.startswith('test_'):
        setattr(RelabelledSafetyTests, name, None)
