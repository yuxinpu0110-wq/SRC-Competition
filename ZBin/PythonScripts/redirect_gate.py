"""S4动态接触门：纯判断，不执行原生动作。当前默认无支持档，不能启用快接。

独立于静球ShotGate。真实支持档需专项物理证据；测试中的合成档不输出为配置。
"""
from dataclasses import dataclass
import math
from match_tactics import distance


@dataclass(frozen=True)
class RedirectSupport:
    verified: bool
    robot_id: int
    power: int
    speed_low: float
    speed_high: float
    incidence_high: float
    latency_low: float
    latency_high: float
    outgoing_speed_high: float
    valid_contacts: int


@dataclass(frozen=True)
class RedirectDecision:
    allowed: bool
    reason: str
    contact_enter_s: float | None=None
    contact_exit_s: float | None=None


class DynamicContactGate:
    def evaluate(self, *, ball,velocity,robot,heading,contact,target,robot_id,power,
                 fresh,new_capture,rule_allowed,legal_pose,robot_speed,robot_spin,capture_age_s,
                 ball_frame_id,robot_frame_id,support=None):
        def no(reason):return RedirectDecision(False,reason)
        if fresh is not True or new_capture is not True:return no('STALE_OR_DUPLICATE')
        if (not isinstance(ball_frame_id,tuple) or len(ball_frame_id)!=2 or ball_frame_id!=robot_frame_id):
            return no('UNMATCHED_CAPTURE_METADATA')
        if rule_allowed is not True or legal_pose is not True:return no('RULE_OR_POSE')
        if type(capture_age_s) not in (int,float) or not math.isfinite(capture_age_s) or not 0<=capture_age_s<=.03:
            return no('CONTACT_SAMPLE_TOO_OLD')
        if support is None or support.verified is not True:return no('PROFILE_UNVERIFIED')
        values=[ball.x,ball.y,velocity.x,velocity.y,robot.x,robot.y,heading,contact.x,contact.y,
                target.x,target.y,robot_speed,robot_spin,support.speed_low,support.speed_high,
                support.incidence_high,support.latency_low,support.latency_high,support.outgoing_speed_high]
        if not all(type(v) in (float,int) and math.isfinite(v) for v in values):return no('INVALID_SAMPLE')
        if (support.robot_id!=robot_id or support.power!=power or support.valid_contacts<20
                or not 0<=support.latency_low<=support.latency_high<=.1
                or not 0<support.speed_low<=support.speed_high<=6500
                or not 0<support.outgoing_speed_high<=6500
                or not 0<=support.incidence_high<=.25):return no('PROFILE_UNSUPPORTED')
        if not 0<=robot_speed<=100 or abs(robot_spin)>.1:return no('RECEIVER_NOT_SETTLED')
        desired=math.atan2(target.y-contact.y,target.x-contact.x)
        if abs(math.atan2(math.sin(heading-desired),math.cos(heading-desired)))>.018:return no('OUTGOING_AIM')
        hx,hy=math.cos(heading),math.sin(heading)
        contact_forward=(contact.x-robot.x)*hx+(contact.y-robot.y)*hy
        contact_lateral=abs(-(contact.x-robot.x)*hy+(contact.y-robot.y)*hx)
        if not 105<=contact_forward<=115 or contact_lateral>3:return no('CONTACT_POSE')
        approach=-(velocity.x*hx+velocity.y*hy)
        speed=math.hypot(velocity.x,velocity.y)
        if approach<=0:return no('BACKSIDE_INCOMING')
        if not support.speed_low<=speed<=support.speed_high:return no('INCOMING_SPEED_UNSUPPORTED')
        incidence=math.acos(max(-1,min(1,approach/speed)))
        if incidence>support.incidence_high:return no('INCIDENCE_UNSUPPORTED')
        forward=(ball.x-robot.x)*hx+(ball.y-robot.y)*hy-approach*capture_age_s
        enter=(forward-110)/approach;leave=(forward-105)/approach
        if leave<0:return no('CONTACT_WINDOW_MISSED')
        if enter>support.latency_high or leave<support.latency_low:return no('NO_EXECUTION_WINDOW')
        # 预测穿过踢球口时的侧偏，不能让靠近圆形车体的球冒充正面来球。
        future_x=ball.x+velocity.x*(capture_age_s+max(0,enter))-robot.x
        future_y=ball.y+velocity.y*(capture_age_s+max(0,enter))-robot.y
        lateral=abs(-future_x*hy+future_y*hx)
        if lateral>10:return no('KICKER_APERTURE_OR_BODY_HIT')
        return RedirectDecision(True,'SUPPORTED_DYNAMIC_WINDOW',max(0,enter),leave)
