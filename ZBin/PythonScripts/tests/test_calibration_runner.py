"""Offline-only tests: never starts SDK, simulator or referee sender."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

TOOL=Path(__file__).resolve().parents[3]/'tools/calibrate_kicks.py'
spec=importlib.util.spec_from_file_location('calibration_runner',TOOL)
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CalibrationRunnerTests(unittest.TestCase):
    def test_static_contact_does_not_mutate_baseline(self):
        base=dict(attackers=[0,1],settle_on_contact=False,calibration_placement_distance_mm=100)
        cfg=runner.diagnostic_config(base,1,3500)
        self.assertEqual(cfg['attackers'],[1,0])
        self.assertEqual(cfg['calibration_placement_distance_mm'],110)
        self.assertTrue(cfg['settle_on_contact'])
        self.assertFalse(cfg['enable_pass'])
        self.assertEqual(base['calibration_placement_distance_mm'],100)

    def test_wrong_config_report_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'; cfg=Path(tmp)/'cfg.json'
            path.write_text(json.dumps(dict(complete=True,runtime_manifest={'config_path':'old'})),encoding='utf8')
            self.assertIsNone(runner.own_report(path,cfg,time.time()-10))

    def test_stale_report_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'; cfg=Path(tmp)/'cfg.json'
            path.write_text(json.dumps(dict(complete=True,runtime_manifest={'config_path':str(cfg.resolve())})),encoding='utf8')
            self.assertIsNone(runner.own_report(path,cfg,time.time()+10))
            self.assertTrue(runner.own_report(path,cfg,time.time()-10)['complete'])


if __name__=='__main__': unittest.main()
