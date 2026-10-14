"""合成动态档仅用于逻辑测试；不是真实标定，不写到比赛配置。"""
from dataclasses import replace
import unittest
from match_tactics import Point
from redirect_gate import DynamicContactGate,RedirectSupport


class RedirectGateTests(unittest.TestCase):
    def setUp(self):
        self.gate=DynamicContactGate()
        self.support=RedirectSupport(True,0,6000,500,1500,.15,.005,.025,4000,20)
        self.args=dict(ball=Point(130,0),velocity=Point(-1000,0),robot=Point(0,0),heading=0,
            contact=Point(110,0),target=Point(2000,0),robot_id=0,power=6000,fresh=True,
            new_capture=True,rule_allowed=True,legal_pose=True,robot_speed=0,robot_spin=0,capture_age_s=0,
            ball_frame_id=(0,1),robot_frame_id=(0,1),support=self.support)
    def result(self,**kw):return self.gate.evaluate(**dict(self.args,**kw))
    def test_supported_front_window(self):self.assertTrue(self.result().allowed)
    def test_unverified_profile(self):self.assertFalse(self.result(support=None).allowed)
    def test_stale(self):self.assertFalse(self.result(fresh=False).allowed)
    def test_duplicate(self):self.assertFalse(self.result(new_capture=False).allowed)
    def test_backside(self):self.assertEqual(self.result(velocity=Point(1000,0)).reason,'BACKSIDE_INCOMING')
    def test_body_aperture(self):self.assertFalse(self.result(ball=Point(130,40)).allowed)
    def test_late(self):self.assertEqual(self.result(ball=Point(100,0)).reason,'CONTACT_WINDOW_MISSED')
    def test_rule_lock(self):self.assertFalse(self.result(rule_allowed=False).allowed)
    def test_unsupported_speed(self):self.assertFalse(self.result(velocity=Point(-3000,0)).allowed)
    def test_unsafe_outgoing_speed(self):
        self.assertFalse(self.result(support=replace(self.support,outgoing_speed_high=7000)).allowed)
    def test_insufficient_physical_contacts(self):
        self.assertFalse(self.result(support=replace(self.support,valid_contacts=2)).allowed)
    def test_old_capture_cannot_predict_window_as_now(self):
        self.assertFalse(self.result(capture_age_s=.1).allowed)
    def test_contact_point_must_be_in_front(self):
        self.assertFalse(self.result(contact=Point(-110,0)).allowed)
    def test_ball_and_robot_cannot_borrow_unmatched_frames(self):
        self.assertFalse(self.result(robot_frame_id=(0,2)).allowed)


if __name__=='__main__':unittest.main()
