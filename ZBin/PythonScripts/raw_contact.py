"""同相机原始球/车/朝向时间链。当前只作诊断，不授予动作或触球权限。

所有速度用检测时间差；换相机、reset、旧帧和断轨不得借用旧历史。
Simulation只能验证接收年龄，无法从单帧证明捕获到接收的绝对延迟。
"""
from dataclasses import asdict, dataclass
import math
from match_tactics import Point, finite, distance


@dataclass(frozen=True)
class RawContactSample:
    camera: int
    frame_id: int
    capture_time: float
    receive_time: float
    reset_epoch: int
    robot_id: int
    ball: Point
    robot: Point
    heading: float
    velocity: Point
    robot_speed: float
    robot_spin: float
    capture_age_s: float
    age_basis: str
    fresh: bool = True
    new_capture: bool = True
    control_authority: bool = False

    def diagnostics(self):
        result=asdict(self)
        result['ball_frame_id']=[self.camera,self.frame_id]
        result['robot_frame_id']=[self.camera,self.frame_id]
        result['heading_frame_id']=[self.camera,self.frame_id]
        result['velocity_semantics']='same_camera_capture_time_difference'
        return result


class RawContactTracker:
    def __init__(self):
        self.previous={}
        self.epoch=None
        self.diagnostics={}

    def reset(self,epoch):
        self.previous.clear()
        self.diagnostics.clear()
        self.epoch=epoch

    def observe(self,capture,now,epoch,robot_id,config,evidence):
        if self.epoch != epoch:self.reset(epoch)
        def reject(reason,clear=True):
            if clear:self.previous.pop(robot_id,None)
            self.diagnostics[robot_id]=dict(ready=False,reason=reason,control_authority=False)
            return None
        if (capture is None or evidence is None or not evidence.fresh
                or evidence.frame_id != (capture.camera,capture.frame_id)
                or (evidence.trusted_robots is not None and robot_id not in evidence.trusted_robots)):
            return reject('CONTROL_ASSOCIATION_REQUIRED')
        if not evidence.new_capture:
            return reject('NO_NEW_RAW_CAPTURE',clear=False)
        key=(config['team'],robot_id)
        if key in capture.ambiguous_robots:return reject('AMBIGUOUS_ROBOT')
        if capture.ball is None or key not in capture.robots or key not in capture.headings:
            return reject('INCOMPLETE_SAME_FRAME_POSE')
        heading=capture.headings[key]
        if (not finite(capture.ball) or not finite(capture.robots[key])
                or not all(type(v) in (int,float) and math.isfinite(v) for v in
                           (now,capture.capture_time,capture.receive_time,heading))):
            return reject('INVALID_RAW_POSE')
        receive_age=now-capture.receive_time
        guarded_age=evidence.capture_age_s
        age=max(receive_age,guarded_age if guarded_age is not None else receive_age)
        if not 0<=receive_age<=.03 or not 0<=age<=.03:
            return reject('CONTACT_SAMPLE_TOO_OLD')
        sign=-1 if config['side']=='right' else 1
        ball=Point(sign*capture.ball.x,sign*capture.ball.y)
        robot=Point(sign*capture.robots[key].x,sign*capture.robots[key].y)
        heading=math.atan2(math.sin(heading+(math.pi if sign<0 else 0)),
                           math.cos(heading+(math.pi if sign<0 else 0)))
        current=(capture.camera,capture.frame_id,capture.capture_time,ball,robot,heading)
        old=self.previous.get(robot_id)
        if old and old[:3] == current[:3]:
            return reject('DUPLICATE_RAW_POSE',clear=False)
        self.previous[robot_id]=current
        if not old:return reject('RAW_TRACK_WARMUP',clear=False)
        if old[0]!=capture.camera:return reject('RAW_CAMERA_CHANGED',clear=False)
        dt=capture.capture_time-old[2]
        if not .001<=dt<=.1:return reject('RAW_TRACK_GAP_OR_CLOCK_RESET',clear=False)
        velocity=Point((ball.x-old[3].x)/dt,(ball.y-old[3].y)/dt)
        speed=math.hypot(velocity.x,velocity.y)
        robot_speed=distance(robot,old[4])/dt
        spin=math.atan2(math.sin(heading-old[5]),math.cos(heading-old[5]))/dt
        if speed>6500:return reject('SPEED_LIMIT_OR_RESET_SUSPECT')
        sample=RawContactSample(capture.camera,capture.frame_id,capture.capture_time,capture.receive_time,
            epoch,robot_id,ball,robot,heading,velocity,robot_speed,spin,age,
            'receive_age_only_simulation' if config.get('raw_capture_clock')=='simulation' else 'capture_clock_guard')
        self.diagnostics[robot_id]=dict(ready=True,reason='CONTINUOUS_SAME_FRAME_RAW_POSE',**sample.diagnostics())
        return sample
