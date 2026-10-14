"""本机实验策略的粗边界：拒绝明显远射，不冒充标定模型。

不预测精确落点、不承诺成功率。真正接应由每帧视觉修正；安全、静态
接触、裁判和禁区检查仍由官方 State -> Task -> Skill 链路执行。
"""
import math
from ball_travel import TravelResult

MODEL_ID='reactive-local-v1-not-calibrated'


def enabled(config):
    return config.get('strategy_version')=='2.2' and config.get('v22_mode')=='reactive-development'


def validate_config(c):
    if not enabled(c): return
    if c.get('profile')!='qualifier' or c.get('calibration_mode') or c.get('enable_feint'):
        raise ValueError('Reactive development supports local qualifier only, without calibration/feint')
    for key,lo,hi in [('reactive_shot_max_mm',1000,2500),('reactive_pass_max_mm',800,1400),
                      ('reactive_pass_cooldown_s',.5,2),('reactive_receive_shift_mm',100,400),
                      ('reactive_shot_lane_margin_mm',80,250)]:
        v=c.get(key)
        if type(v) not in (float,int) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError('Invalid reactive bound: '+key)
    if c.get('reactive_shot_power')!=6000 or c.get('reactive_pass_power')!=4500:
        raise ValueError('Reactive development uses explicit experimental powers 6000/4500 only')


def evaluate(plan,ball,heading,power,epoch,now,c):
    def result(ok,reason):return TravelResult(ok,reason,MODEL_ID)
    if (plan.model_id!=MODEL_ID or epoch!=plan.reset_epoch or not 0<=now-plan.created_at<=.2
            or power!=plan.power or not math.isfinite(heading)):
        return result(False,'REACTIVE_PLAN_CHANGED')
    dx,dy=plan.target.x-ball.x,plan.target.y-ball.y
    if not all(math.isfinite(v) for v in (dx,dy,ball.x,ball.y)):
        return result(False,'REACTIVE_INVALID_GEOMETRY')
    if math.hypot(ball.x-plan.origin.x,ball.y-plan.origin.y)>10:
        return result(False,'REACTIVE_BALL_CHANGED')
    desired=math.atan2(dy,dx)
    if abs(math.atan2(math.sin(heading-desired),math.cos(heading-desired)))>.018:
        return result(False,'REACTIVE_HEADING_CHANGED')
    length=math.hypot(dx,dy)
    if plan.purpose=='pass':
        ok=power==c['reactive_pass_power'] and 650<=length<=c['reactive_pass_max_mm']
        return result(ok,'REACTIVE_SHORT_PASS_BOUND' if ok else 'REACTIVE_PASS_TOO_LONG')
    if plan.purpose!='shot' or power!=c['reactive_shot_power']:
        return result(False,'REACTIVE_UNSUPPORTED_KICK')
    plane=c['field_length']/2+21.5
    if dx<=0 or abs(plan.target.x-c['field_length']/2)>1 or math.cos(desired)<=.05:
        return result(False,'REACTIVE_AWAY_FROM_GOAL')
    if (plane-ball.x)/math.cos(desired)>c['reactive_shot_max_mm']:
        return result(False,'REACTIVE_SHOT_TOO_FAR')
    for angle in (heading-.025,heading+.025):
        if math.cos(angle)<=.05 or abs(ball.y+(plane-ball.x)*math.tan(angle))>=c['goal_width']/2-70:
            return result(False,'REACTIVE_GOAL_APERTURE')
    return result(True,'REACTIVE_NEAR_SHOT_BOUND_NOT_CALIBRATED')
