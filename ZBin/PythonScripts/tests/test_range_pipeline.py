import unittest
from match_tactics import Point
from run_match import load_config
from test_ball_travel import ROOT, test_model
import test_v21 as fixtures


class RangePipelineTests(fixtures.V21IntegrationTests):
    def setUp(self):
        super().setUp()
        self.c = load_config(ROOT/'configs/v2.2-development.json')
        self.play = self.NormalPlay(self.c)
        self.opponents = []
        self.players = {0: Point(2605, 0)}
        self.angles[0] = 0

    def ready(self):
        for now in (0, .02, .04, .14):
            self.tick(now)

    def test_unmeasured_model_cannot_send_sdk_kick(self):
        self.ready()
        self.assertFalse(any(call[0] == 'kick' for call in self.calls))
        self.assertIsNone(self.play.transactions.active)
        self.assertEqual(self.play.shot_opportunities[0]['reason'], 'MODEL_UNCALIBRATED')

    def test_unreachable_shot_never_requests_transaction_or_setKick(self):
        self.play.travel_model = test_model(speed=(1000, 1100), deceleration=(1000, 1100))
        self.ready()
        self.assertFalse(any(call[0] == 'kick' for call in self.calls))
        self.assertIsNone(self.play.transactions.active)
        self.assertEqual(self.play.shot_opportunities[0]['reason'], 'RANGE_UNREACHABLE')

    def test_frozen_power_reaches_actual_official_skill(self):
        # evalPower fixture returns 4000, but the calibrated plan must keep 4500.
        self.play.travel_model = test_model()
        self.ready()
        kicks = [call for call in self.calls if call[0] == 'kick']
        self.assertEqual(kicks, [('kick', 0, 4500)])
        self.assertEqual(self.play.shot_opportunities[0]['actual_setKick_power'], 4500)

    def test_stale_and_halt_still_preempt_reachable_plan(self):
        self.play.travel_model = test_model()
        self.ready()
        for mode, fresh in (('PLAY', False), ('HALT', True)):
            self.tick(.16, mode=mode, fresh=fresh)
            self.assertFalse(any(call[0] == 'kick' for call in self.calls))
            self.assertEqual(self.play.executed_state, 'Hold')

    def test_unreachable_is_not_relaxed_by_long_wait(self):
        self.play.travel_model = test_model(speed=(1000, 1100), deceleration=(1000, 1100))
        for now in (0, .02, .04, .14, .2, .4, .6, .8, 1., 1.5, 2., 3., 5.):
            self.tick(now)
            self.assertFalse(any(call[0] == 'kick' for call in self.calls))


for name in dir(fixtures.V21IntegrationTests):
    if name.startswith('test_'):
        setattr(RangePipelineTests, name, None)
