"""Calibrated rolling-ball bounds, in mm, seconds and mm/s.

SDK power is an exact profile key, never interpreted as physical speed. Only
release-reference models with verified holdout reports are accepted here.
Measured-window models require a separate reference-point implementation.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath


def number(value, low=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= low


def interval(value, low=0):
    return (isinstance(value, list) and len(value) == 2
            and all(number(v, low) for v in value) and value[0] <= value[1])


@dataclass(frozen=True)
class TravelResult:
    reachable: bool
    reason: str
    model_id: str | None = None
    time_low_s: float | None = None
    time_high_s: float | None = None
    arrival_speed_low_mm_s: float | None = None
    arrival_speed_high_mm_s: float | None = None
    range_low_mm: float | None = None
    release_delay_high_s: float | None = None
    angle_error_high_rad: float | None = None
    min_goal_arrival_speed_mm_s: float | None = None

    def diagnostics(self):
        return asdict(self)


class BallTravelModel:
    def __init__(self, data, runtime_hashes=None, field_settings=None):
        self.data = deepcopy(data)
        self.runtime_hashes = dict(runtime_hashes or {})
        self.field_settings = dict(field_settings or {})
        self.validate()

    def validate(self):
        d = self.data
        if (type(d.get('schema_version')) is not int or d['schema_version'] != 1
                or type(d.get('usable_for_planning')) is not bool
                or not isinstance(d.get('profiles'), list)):
            raise ValueError('Invalid ball travel schema')
        if not d['usable_for_planning']:
            return
        if not isinstance(d.get('model_id'), str) or not d['model_id']:
            raise ValueError('Missing calibrated model_id')
        hashes = d.get('runtime_hashes', {})
        required = {'PythonScripts/CppPackage/CppPackage.pyd', 'MedusaCore.dll',
                    'grSim.exe', 'ode_double.dll', 'zss_simulator.ini'}
        if not isinstance(hashes, dict) or not required <= hashes.keys():
            raise ValueError('Model must bind SDK, physics DLL and simulator settings hashes')
        if any(not isinstance(h, str) or len(h) != 64 or any(c not in '0123456789abcdef' for c in h)
               for h in hashes.values()):
            raise ValueError('Invalid runtime SHA256')
        fields = d.get('field_settings')
        if not isinstance(fields, dict) or not all(number(fields.get(n), .001) for n in
                ('field_length', 'field_width', 'goal_width', 'defense_depth', 'defense_width')):
            raise ValueError('Model must bind field dimensions')
        if (not isinstance(d.get('acceptance_report_sha256'), str)
                or len(d['acceptance_report_sha256']) != 64
                or any(c not in '0123456789abcdef' for c in d['acceptance_report_sha256'])):
            raise ValueError('Missing independent calibration acceptance report hash')
        keys = set()
        if not d['profiles']:
            raise ValueError('Usable model cannot have zero calibrated profiles')
        for p in d['profiles']:
            if (type(p.get('robot_id')) is not int or not 0 <= p['robot_id'] < 16
                    or type(p.get('sdk_power')) is not int or not 1000 <= p['sdk_power'] <= 6000
                    or p.get('contact_mode') != 'static'
                    or p.get('speed_semantics') != 'validated_release_speed_interval'):
                raise ValueError('Invalid or unsupported calibrated profile')
            key = (p['robot_id'], p['sdk_power'], p['contact_mode'])
            if key in keys:
                raise ValueError('Duplicate calibrated profile')
            keys.add(key)
            for name, low in (('initial_speed_mm_s', .001), ('deceleration_mm_s2', 0),
                              ('release_delay_s', 0), ('distance_domain_mm', 0),
                              ('contact_forward_mm', 75)):
                if not interval(p.get(name), low):
                    raise ValueError('Invalid profile interval: '+name)
            if p['contact_forward_mm'][1] > 110:
                raise ValueError('Contact calibration exceeds verified physical contact band')
            if not number(p.get('contact_lateral_max_mm')) or p['contact_lateral_max_mm'] > 10:
                raise ValueError('Invalid calibrated lateral contact domain')
            for name in ('angle_error_high_rad', 'reachable_distance_low_mm', 'min_goal_arrival_speed_mm_s'):
                if not number(p.get(name)):
                    raise ValueError('Invalid profile bound: '+name)
            if p['min_goal_arrival_speed_mm_s'] <= 0:
                raise ValueError('Goal arrival speed must have a positive calibrated minimum')
            if p['angle_error_high_rad'] > .25:
                raise ValueError('Static shot error bound exceeds supported angular domain')
            for name in ('raw_attempts', 'clean_flights', 'holdout_flights'):
                if type(p.get(name)) is not int or p[name] < 1:
                    raise ValueError('Missing physical/holdout sample counts: '+name)
            if p['clean_flights'] > p['raw_attempts'] or p['holdout_flights'] > p['raw_attempts']:
                raise ValueError('Inconsistent physical sample counts')
            if p.get('speed_limit_verified') is not True or p.get('holdout_validated') is not True:
                raise ValueError('Unverified calibrated profile')

    def reject(self, reason):
        return TravelResult(False, reason, self.data.get('model_id'))

    def profile(self, robot_id, sdk_power, contact_mode='static'):
        return next((p for p in self.data['profiles'] if p['robot_id'] == robot_id
                     and p['sdk_power'] == sdk_power and p['contact_mode'] == contact_mode), None)

    def evaluate(self, robot_id, sdk_power, distance_mm, contact_mode='static', *, contact=None):
        if not self.data['usable_for_planning']:
            return self.reject('MODEL_UNCALIBRATED')
        if (self.data['runtime_hashes'] != self.runtime_hashes
                or self.data['field_settings'] != self.field_settings):
            return self.reject('MODEL_CONTEXT_MISMATCH')
        if not number(distance_mm) or not number(sdk_power) or type(robot_id) is not int:
            return self.reject('INVALID_TRAVEL_INPUT')
        p = self.profile(robot_id, sdk_power, contact_mode)
        if not p:
            return self.reject('MODEL_OUT_OF_DOMAIN')
        if not p['distance_domain_mm'][0] <= distance_mm <= p['distance_domain_mm'][1]:
            return self.reject('MODEL_OUT_OF_DOMAIN')
        if contact is not None:
            forward, lateral = contact
            if (not number(forward) or not number(lateral)
                    or not p['contact_forward_mm'][0] <= forward <= p['contact_forward_mm'][1]
                    or lateral > p['contact_lateral_max_mm']):
                return self.reject('MODEL_CONTACT_OUT_OF_DOMAIN')
        v_low, v_high = p['initial_speed_mm_s']
        a_low, a_high = p['deceleration_mm_s2']
        if v_high > 6500:
            return self.reject('MODEL_SPEED_LIMIT_UNSAFE')
        disc_low = v_low*v_low-2*a_high*distance_mm
        if disc_low < 0 or distance_mm > p['reachable_distance_low_mm']:
            return self.reject('RANGE_UNREACHABLE')
        disc_high = v_high*v_high-2*a_low*distance_mm
        end_low, end_high = math.sqrt(disc_low), math.sqrt(disc_high)
        # Stable at a≈0 and at stopping distance; delay is reported separately.
        time_low = 2*distance_mm/(v_high+end_high)
        time_high = 2*distance_mm/(v_low+end_low)
        return TravelResult(True, 'REACHABLE', self.data['model_id'], time_low, time_high,
                            end_low, end_high, p['reachable_distance_low_mm'],
                            p['release_delay_s'][1], p['angle_error_high_rad'],
                            p['min_goal_arrival_speed_mm_s'])


def load_ball_model(path, runtime_root, config):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    runtime_root = Path(runtime_root).resolve()
    hashes = {}
    for name in data.get('runtime_hashes', {}):
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
            raise ValueError('Runtime hash paths must stay inside ZBin')
        file = (runtime_root/str(relative)).resolve()
        if not file.is_relative_to(runtime_root):
            raise ValueError('Runtime hash path escapes ZBin')
        if file.is_file():
            with file.open('rb') as stream:
                hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    fields = {n: config[n] for n in ('field_length', 'field_width', 'goal_width', 'defense_depth', 'defense_width')}
    model = BallTravelModel(data, hashes, fields)
    if data['usable_for_planning']:
        report = data.get('acceptance_report_path')
        if not isinstance(report, str) or not report:
            raise ValueError('Missing calibration acceptance report file')
        report_path = Path(path).parent/report
        actual = hashlib.sha256(report_path.read_bytes()).hexdigest()
        if actual != data['acceptance_report_sha256']:
            raise ValueError('Calibration acceptance report hash mismatch')
        evidence = json.loads(report_path.read_text(encoding='utf-8-sig'))
        if (evidence.get('physical_acceptance_passed') is not True
                or evidence.get('model_id') != data['model_id']
                or evidence.get('runtime_hashes') != data['runtime_hashes']):
            raise ValueError('Calibration report has not accepted this physical model')
    return model
