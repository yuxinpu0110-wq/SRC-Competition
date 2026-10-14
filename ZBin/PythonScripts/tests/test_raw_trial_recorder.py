import json
from pathlib import Path
import tempfile
import unittest
from raw_trial_recorder import RawTrialRecorder


class RawRecorderTests(unittest.TestCase):
    def test_disabled_has_no_network_query(self):
        def fail(): raise AssertionError('must not query during ordinary benchmark')
        with RawTrialRecorder(None,query=fail) as r:
            r.poll()
        self.assertEqual(r.samples,0)

    def test_dedup_is_camera_specific_and_filters_previous_trial(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'raw.jsonl'
            with RawTrialRecorder(path) as r:
                c=dict(camera=0,frame_id=1,capture_time=100,receive_time=r.started+.01,
                       ball=[0,0],robots=[])
                r.record([dict(c,receive_time=r.started-1),c,c,dict(c,camera=1)])
                self.assertEqual(r.samples,2)
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertFalse(rows[0]['certified_camera_handoff'])
            self.assertFalse(rows[-1]['interrupted'])

    def test_unavailable_and_interruption_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'raw.jsonl'
            with self.assertRaises(RuntimeError):
                with RawTrialRecorder(path,query=lambda:None) as r:
                    r.poll()
                    raise RuntimeError('synthetic failure')
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[1]['kind'],'RAW_POLL_UNAVAILABLE')
            self.assertTrue(rows[-1]['interrupted'])
            self.assertFalse(rows[-1]['usable_for_planning'])


if __name__=='__main__': unittest.main()
