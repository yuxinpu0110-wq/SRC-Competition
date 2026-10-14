import unittest
from run_match import ROOT,load_config
from match_tactics import Point,Tactics,distance
from legal_path import route,length


class LegalPathTests(unittest.TestCase):
    def setUp(self):self.t=Tactics(load_config(ROOT/'configs/v2.3-development.json'))
    def test_straight_legal_segment(self):
        p=route(self.t,Point(0,0),Point(2000,0))
        self.assertEqual(len(p),2);self.assertEqual(length(p),2000)
    def test_route_around_box_not_repeated_sideways_waypoints(self):
        a,b=Point(4000,1500),Point(4000,-1500)
        p=route(self.t,a,b)
        self.assertIsNotNone(p)
        self.assertGreater(length(p),distance(a,b))
        self.assertTrue(all(self.t.segment_legal(x,y) for x,y in zip(p,p[1:])))
        self.assertEqual(p[-1],b)
    def test_illegal_receiver_pose_rejected(self):
        self.assertIsNone(route(self.t,Point(0,0),Point(4000,0)))


if __name__=='__main__':unittest.main()
