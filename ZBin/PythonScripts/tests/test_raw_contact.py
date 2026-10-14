"""真实协议形状的合成包只验证数据契约，不是实测重定向支持档。"""
from dataclasses import replace
import math
import unittest
from unittest.mock import patch
from match_tactics import Point
from observation import Capture,Evidence,decode_detection,capture_record,capture_from_record
from raw_contact import RawContactTracker
from redirect_gate import DynamicContactGate
from sim_tools import field


def packet(heading=0.,duplicate=False):
    ball=field(1,1.,5)+field(3,200.,5)+field(4,0.,5)
    robot=field(1,1.,5)+field(2,0)+field(3,0.,5)+field(4,0.,5)
    if heading is not None:robot+=field(5,heading,5)
    frame=field(1,1)+field(2,100.,1)+field(4,0)+field(5,ball,2)+field(7,robot,2)
    if duplicate:frame+=field(7,robot,2)
    return field(1,frame,2)


class PoseProtocolTests(unittest.TestCase):
    def test_orientation_rad_survives_protocol_and_relay(self):
        capture=list(decode_detection(packet(.37),10.))[0]
        self.assertAlmostEqual(capture.headings['blue',0],.37,places=6)
        self.assertEqual(capture_from_record(capture_record(capture)),capture)

    def test_optional_orientation_missing_is_not_zero_heading(self):
        capture=list(decode_detection(packet(None),10.))[0]
        self.assertIn(('blue',0),capture.robots)
        self.assertNotIn(('blue',0),capture.headings)

    def test_invalid_or_duplicate_heading_cannot_enter_pose_chain(self):
        for data in (packet(float('nan')),packet(0.,True)):
            capture=list(decode_detection(data,10.))[0]
            self.assertNotIn(('blue',0),capture.headings)

    def test_old_capture_and_old_relay_payload_stay_compatible(self):
        capture=Capture(0,1,100.,10.,Point(200,0),{('blue',0):Point(0,0)})
        old=capture_record(capture);old.pop('robot_headings');old.pop('ambiguous_robots')
        self.assertEqual(capture_from_record(old).headings,{})

    def test_old_relay_is_not_implicitly_killed(self):
        import vision_relay
        with patch.object(vision_relay,'status',return_value={'schema_version':2}) as query:
            with self.assertRaisesRegex(RuntimeError,'restart explicitly'):
                vision_relay.ensure_relay()
        self.assertEqual(query.call_count,1)


class ContactTrackTests(unittest.TestCase):
    def setUp(self):
        self.tracker=RawContactTracker()
        self.config=dict(team='blue',side='left',raw_capture_clock='simulation')

    def capture(self,frame=1,dt=0,ball=Point(200,0),heading=0.,camera=0):
        return Capture(camera,frame,100+dt,10+dt,ball,{('blue',0):Point(0,0)},
                       {('blue',0):heading})

    def observe(self,capture,now=None,epoch=0,**updates):
        e=Evidence(True,True,'RAW_ASSOCIATED',capture.capture_time,capture.receive_time,
                   (capture.camera,capture.frame_id),capture_clock='simulation',capture_age_s=0,
                   trusted_robots=(0,))
        e=replace(e,**updates)
        return self.tracker.observe(capture,now if now is not None else capture.receive_time,
                                    epoch,0,self.config,e)

    def warmup(self):
        self.assertIsNone(self.observe(self.capture()))

    def test_capture_difference_velocity_and_same_frame_heading(self):
        self.warmup()
        sample=self.observe(self.capture(2,.02,Point(180,0),.001))
        self.assertAlmostEqual(sample.velocity.x,-1000)
        self.assertEqual(sample.heading,.001)
        d=sample.diagnostics()
        self.assertEqual(d['ball_frame_id'],d['heading_frame_id'])
        self.assertEqual(d['robot_frame_id'],d['ball_frame_id'])
        self.assertFalse(d['control_authority'])
        self.assertEqual(d['age_basis'],'receive_age_only_simulation')

    def test_stale_or_untrusted_control_cannot_get_raw_pose(self):
        self.warmup()
        for kw in ({'fresh':False},{'trusted_robots':()},{'frame_id':(0,9)}):
            self.assertIsNone(self.observe(self.capture(2,.02),**kw))

    def test_duplicate_metadata_cannot_be_marked_new_by_caller(self):
        self.warmup()
        self.assertIsNone(self.observe(self.capture()))
        self.assertEqual(self.tracker.diagnostics[0]['reason'],'DUPLICATE_RAW_POSE')

    def test_epoch_and_camera_switch_do_not_borrow_velocity(self):
        self.warmup()
        self.assertIsNone(self.observe(self.capture(2,.02,Point(180,0)),epoch=1))
        self.assertIsNone(self.observe(self.capture(3,.04,Point(160,0),camera=1),epoch=1))
        self.assertEqual(self.tracker.diagnostics[0]['reason'],'RAW_CAMERA_CHANGED')

    def test_old_frame_age_is_rejected(self):
        self.warmup()
        self.assertIsNone(self.observe(self.capture(2,.02),now=10.08))
        self.assertEqual(self.tracker.diagnostics[0]['reason'],'CONTACT_SAMPLE_TOO_OLD')

    def test_gap_clock_reset_and_speed_suspect_are_rejected(self):
        for capture in (self.capture(2,.2),self.capture(0,-1),self.capture(2,.02,Point(600,0))):
            self.setUp();self.warmup()
            self.assertIsNone(self.observe(capture))

    def test_missing_orientation_never_falls_back_to_sdk(self):
        c=self.capture();c.headings.clear()
        self.assertIsNone(self.observe(c))
        self.assertEqual(self.tracker.diagnostics[0]['reason'],'INCOMPLETE_SAME_FRAME_POSE')

    def test_heading_wrap_uses_short_rotation(self):
        self.observe(self.capture(heading=math.pi-.001))
        sample=self.observe(self.capture(2,.02,heading=-math.pi+.001))
        self.assertAlmostEqual(sample.robot_spin,.1)

    def test_mirror_rotates_position_velocity_and_heading_together(self):
        self.config.update(team='yellow',side='right')
        a=Capture(0,1,100,10,Point(-200,0),{('yellow',0):Point(0,0)},{('yellow',0):math.pi})
        b=replace(a,frame_id=2,capture_time=100.02,receive_time=10.02,ball=Point(-180,0))
        self.observe(a);sample=self.observe(b)
        self.assertAlmostEqual(sample.velocity.x,-1000)
        self.assertAlmostEqual(sample.heading,0)

    def test_real_pose_input_still_requires_physical_support(self):
        self.warmup()
        sample=self.observe(self.capture(2,.02,Point(180,0)))
        result=DynamicContactGate().evaluate(ball=sample.ball,velocity=sample.velocity,
            robot=sample.robot,heading=sample.heading,contact=Point(110,0),target=Point(2000,0),
            robot_id=0,power=6000,fresh=sample.fresh,new_capture=sample.new_capture,
            rule_allowed=True,legal_pose=True,robot_speed=sample.robot_speed,robot_spin=sample.robot_spin,
            capture_age_s=sample.capture_age_s,ball_frame_id=(sample.camera,sample.frame_id),
            robot_frame_id=(sample.camera,sample.frame_id),support=None)
        self.assertEqual(result.reason,'PROFILE_UNVERIFIED')
