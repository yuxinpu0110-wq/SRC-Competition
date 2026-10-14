from copy import deepcopy
from pathlib import Path
import json
import math
import tempfile
import unittest

from ball_travel import BallTravelModel, load_ball_model
from match_tactics import Point
from shot_opportunity import KickPlan, evaluate_kick
from run_match import load_config

ROOT = Path(__file__).resolve().parents[3]


def model_data(speed=(4500, 4800), deceleration=(500, 700), range_low=9000):
    """Synthetic physics solely for offline tests, never exported as calibration."""
    fields = {name: value for name, value in zip(
        ('field_length', 'field_width', 'goal_width', 'defense_depth', 'defense_width'),
        (9000, 6000, 1000, 1000, 2000))}
    profiles = [dict(robot_id=n, sdk_power=4500, contact_mode='static',
        speed_semantics='validated_release_speed_interval', initial_speed_mm_s=list(speed),
        deceleration_mm_s2=list(deceleration), release_delay_s=[.05, .12],
        distance_domain_mm=[0, 9000], contact_forward_mm=[90, 110], contact_lateral_max_mm=10,
        angle_error_high_rad=.005, reachable_distance_low_mm=range_low,
        min_goal_arrival_speed_mm_s=100, raw_attempts=40, clean_flights=30, holdout_flights=10,
        speed_limit_verified=True, holdout_validated=True) for n in (0, 1)]
    return dict(schema_version=1, model_id='SYNTHETIC_TEST_ONLY', usable_for_planning=True,
                runtime_hashes={key: 'a'*64 for key in
                    ('PythonScripts/CppPackage/CppPackage.pyd', 'MedusaCore.dll', 'grSim.exe',
                     'ode_double.dll', 'zss_simulator.ini')},
                field_settings=fields, acceptance_report_sha256='b'*64, profiles=profiles)


def test_model(**kwargs):
    data = model_data(**kwargs)
    return BallTravelModel(data, data['runtime_hashes'], data['field_settings'])


class TravelTests(unittest.TestCase):
    def test_long_reachable_shot_is_allowed(self):
        result = test_model().evaluate(0, 4500, 6000)
        self.assertTrue(result.reachable)
        self.assertGreater(result.arrival_speed_low_mm_s, 0)
        self.assertLessEqual(result.time_low_s, result.time_high_s)

    def test_short_distance_insufficient_power_rejected(self):
        self.assertEqual(test_model(speed=(1000, 1100), deceleration=(1000, 1100)).evaluate(0, 4500, 600).reason,
                         'RANGE_UNREACHABLE')

    def test_zero_deceleration_and_stopping_distance_are_stable(self):
        result = test_model(deceleration=(0, 0)).evaluate(0, 4500, 4500)
        self.assertAlmostEqual(result.time_high_s, 1.)
        result = test_model(speed=(3000, 3000), deceleration=(1000, 1000)).evaluate(0, 4500, 4500)
        self.assertTrue(result.reachable)
        self.assertEqual(result.arrival_speed_low_mm_s, 0)
        self.assertEqual(result.time_high_s, 3.)

    def test_no_extrapolation_or_invalid_values(self):
        model = test_model()
        for robot, power, length in ((0, 4000, 100), (2, 4500, 100), (0, 4500, 9100),
                                     (0, float('nan'), 100), (0, 4500, float('inf')), (0, 4500, -1)):
            with self.subTest(robot=robot, power=power, length=length):
                self.assertFalse(model.evaluate(robot, power, length).reachable)

    def test_runtime_or_field_mismatch_rejects(self):
        data = model_data()
        self.assertEqual(BallTravelModel(data).evaluate(0, 4500, 100).reason, 'MODEL_CONTEXT_MISMATCH')
        fields = dict(data['field_settings'], field_length=12000)
        self.assertFalse(BallTravelModel(data, data['runtime_hashes'], fields).evaluate(0, 4500, 100).reachable)

    def test_contact_domain_is_measured(self):
        model = test_model()
        self.assertTrue(model.evaluate(0, 4500, 1000, contact=(100, 0)).reachable)
        for contact in ((85, 0), (111, 0), (100, 11), (float('nan'), 0)):
            self.assertEqual(model.evaluate(0, 4500, 1000, contact=contact).reason, 'MODEL_CONTACT_OUT_OF_DOMAIN')

    def test_unusable_and_unsafe_upper_speed_are_rejected(self):
        data = model_data()
        data['usable_for_planning'] = False
        self.assertEqual(BallTravelModel(data).evaluate(0, 4500, 1).reason, 'MODEL_UNCALIBRATED')
        self.assertEqual(test_model(speed=(6400, 6600)).evaluate(0, 4500, 1).reason, 'MODEL_SPEED_LIMIT_UNSAFE')

    def test_schema_cannot_promote_diagnostic_window_speed(self):
        for updates in ({'speed_semantics': 'measured_window_speed_not_instantaneous_release_speed'},
                        {'initial_speed_mm_s': [float('nan'), 5000]}, {'holdout_flights': 0},
                        {'speed_limit_verified': False}, {'contact_forward_mm': [75, 120]}):
            data = model_data()
            data['profiles'][0].update(updates)
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                BallTravelModel(data)

    def test_physical_report_is_required_by_file_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'model.json'
            data = model_data()
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'acceptance report'):
                load_ball_model(path, directory, data['field_settings'])


class OpportunityTests(unittest.TestCase):
    def setUp(self):
        self.model = test_model()
        self.config = dict(self.model.data['field_settings'], power_cap=6000, ball_radius_mm=21.5)
        self.ball = Point(2700, 0)
        self.plan = KickPlan(0, 'shot', self.ball, Point(4500, 0), 4500, 0, 10., self.model.data['model_id'])

    def evaluate(self, plan=None, **changes):
        options = dict(ball=self.ball, heading=0, actual_power=4500, epoch=0, now=10.1)
        options.update(changes)
        return evaluate_kick(plan or self.plan, model=self.model, config=self.config, **options)

    def test_full_ball_crossing_adds_radius_to_distance(self):
        self.model = test_model(range_low=1810)
        self.assertEqual(self.evaluate().reason, 'RANGE_UNREACHABLE')
        self.model = test_model(range_low=1830)
        self.assertTrue(self.evaluate().reachable)

    def test_reachable_long_goal_and_fast_flight_allowed(self):
        self.ball = Point(-1500, 0)
        self.plan = KickPlan(0, 'shot', self.ball, Point(4500, 0), 4500, 0, 10., self.model.data['model_id'])
        self.assertTrue(self.evaluate().reachable)

    def test_expired_epoch_power_or_ball_change_rejects(self):
        for options in ({'actual_power': 4400}, {'epoch': 1}, {'now': 10.5},
                        {'ball': Point(2730, 0)}, {'heading': .1}):
            with self.subTest(options=options):
                self.assertFalse(self.evaluate(**options).reachable)

    def test_arrival_speed_is_separate_hard_constraint(self):
        self.model.data['profiles'][0]['min_goal_arrival_speed_mm_s'] = 5000
        self.assertEqual(self.evaluate().reason, 'ARRIVAL_TOO_SLOW')

    def test_aperture_accounts_for_heading_and_error(self):
        target = Point(4500, 475)
        plan = KickPlan(0, 'shot', self.ball, target, 4500, 0, 10., self.model.data['model_id'])
        self.assertEqual(self.evaluate(plan, heading=math.atan2(475, 1800)).reason, 'GOAL_APERTURE_UNSAFE')

    def test_unvalidated_pass_cannot_bypass_gate(self):
        plan = KickPlan(0, 'pass', self.ball, Point(3300, 1200), 4500, 0, 10., self.model.data['model_id'])
        self.assertEqual(self.evaluate(plan).reason, 'ADVANCE_PLAN_NOT_VALIDATED')

    def test_shot_crossing_defense_zone_is_allowed(self):
        self.assertTrue(self.evaluate().reachable)

    def test_candidate_stays_closed_and_development_loads(self):
        config = load_config(ROOT/'configs/v2.2-development.json')
        self.assertEqual(config['control_contract_version'], 2)
        with self.assertRaisesRegex(ValueError, 'candidate is not accepted'):
            load_config(ROOT/'configs/v2.2-candidate.json')
