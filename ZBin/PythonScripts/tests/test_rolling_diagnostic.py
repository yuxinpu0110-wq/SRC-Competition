import copy
import unittest
from rolling_diagnostic import assess


def samples():
    # Synthetic constant-speed segment followed by rest; not physical data.
    rows=[dict(kind='RAW_TRIAL_START')]
    for i in range(201):
        rows.append(dict(kind='RAW_CAPTURE',camera=0,frame_id=i,capture_time=i*.01,
            receive_time=100+i*.01,ball=[min(i*10,1000),0],robots=[]))
    rows.append(dict(kind='RAW_TRIAL_END',interrupted=False,poll_failures=0))
    return rows


class RollingDiagnosticTests(unittest.TestCase):
    def test_rest_to_rest_is_diagnostic_not_model(self):
        result=assess(samples(),[0,0],1)
        self.assertTrue(result['qualified'])
        self.assertEqual(result['observed_displacement_mm'],1000)
        self.assertFalse(result['usable_for_planning'])
        self.assertFalse(result['release_speed_calibrated'])

    def test_double_kick_rejected(self):
        self.assertFalse(assess(samples(),[0,0],2)['qualified'])

    def test_camera_handoff_cannot_fake_complete_track(self):
        rows=samples()
        for r in rows[102:-1]: r['camera']=1
        self.assertFalse(assess(rows,[0,0],1)['qualified'])

    def test_camera_endpoint_disagreement_rejected(self):
        rows=samples()
        second=copy.deepcopy(rows[1:-1])
        for r in second:
            r['camera']=1
            r['ball'][0]*=1.1
        rows[-1:-1]=second
        self.assertEqual(assess(rows,[0,0],1)['reason'],'CAMERAS_DISAGREE_ON_ENDPOINT')

    def test_gap_rejected(self):
        rows=samples();del rows[50:90]
        self.assertFalse(assess(rows,[0,0],1)['qualified'])

    def test_late_collision_rejected(self):
        rows=samples();rows[80]['robots']=[['yellow',2,800,0]]
        self.assertFalse(assess(rows,[0,0],1)['qualified'])

    def test_missing_end_rejected(self):
        self.assertFalse(assess(samples()[:-1],[0,0],1)['qualified'])

    def test_moving_endpoint_rejected(self):
        rows=samples()
        for i,r in enumerate(rows[1:-1]): r['ball']=[i*10,0]
        self.assertFalse(assess(rows,[0,0],1)['qualified'])


if __name__=='__main__':unittest.main()
