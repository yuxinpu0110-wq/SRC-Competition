"""V2.3完整动作计划。时间是粗估区间，不是标定保证，不直接执行动作。"""
from dataclasses import dataclass,replace
import math
from match_tactics import Point,distance


@dataclass(frozen=True)
class PlanLeg:
    id: str
    purpose: str
    sender: int
    receiver: int | None
    contact_point: Point
    receiver_pose: Point | None
    receiver_heading: float | None
    outgoing_target: Point
    sdk_power: int
    receive_mode: str
    prepare_high_s: float=0.0


@dataclass(frozen=True)
class AttackPlan:
    id: str
    epoch: int
    kind: str
    created_at: float
    deadline: float
    legs: tuple
    terminal_shooter: int | None
    terminal_target: Point | None
    time_low_s: float
    time_high_s: float
    evidence: str='HEURISTIC_NOT_CALIBRATED'

    def freeze(self,identity,epoch,now,deadline):
        return replace(self,id=identity,epoch=epoch,created_at=now,deadline=deadline)


def wrap(angle):return math.atan2(math.sin(angle),math.cos(angle))


def terminal_adjustment(origin,contact,target):
    # 接应车朝来球；然后合法退球、空车绕到射门方向。不能把这个成本藏起来。
    incoming=math.atan2(origin.y-contact.y,origin.x-contact.x)
    outgoing=math.atan2(target.y-contact.y,target.x-contact.x)
    turn=abs(wrap(outgoing-incoming))
    return (420+320*turn)/900+turn/.9 if turn>.06 else .12


def direct_plan(ball,goal,shooter,players,config):
    robot=players[shooter]
    desired=math.atan2(goal.y-ball.y,goal.x-ball.x)
    radial=abs(wrap(desired+math.pi-math.atan2(robot.y-ball.y,robot.x-ball.x)))
    prep=max(0,distance(robot,ball)-110)/1100+320*radial/900+.15
    cost=prep+distance(ball,goal)/1800
    leg=PlanLeg('shot-0','shot',shooter,None,ball,None,None,goal,config['reactive_shot_power'],'STATIC')
    return AttackPlan('',0,'DIRECT_SHOT',0,0,(leg,),shooter,goal,max(.1,cost*.7),cost*1.4+.3)


def pass_plan(ball,contact,sender,receiver,players,goal,tactics,config):
    length=distance(ball,contact)
    limit=config['v23_advance_pass_max_mm']
    if not 650<=length<=limit:return None
    ux,uy=(contact.x-ball.x)/length,(contact.y-ball.y)/length
    receiving=Point(contact.x+110*ux,contact.y+110*uy)
    sending=Point(ball.x-110*ux,ball.y-110*uy)
    if any(distance(p,tactics.legal(p))>1 for p in (receiving,sending)):return None
    if receiver not in players or sender not in players:return None
    from legal_path import route,length as path_length
    sender_path=route(tactics,players[sender],sending)
    receiver_path=route(tactics,players[receiver],receiving)
    if sender_path is None or receiver_path is None:return None
    heading=math.atan2(-uy,-ux)
    # 准备/飞行仅用于比较，实际许可仍依据新鲜视觉和到位检查。
    prep=max(path_length(receiver_path)/1300+.3,path_length(sender_path)/1100+.3)
    power=config['reactive_pass_power'] if length<=config['reactive_pass_max_mm'] else config['v23_advance_pass_power']
    flight=length/(1200 if power==4500 else 1600)+.2
    adjustment=terminal_adjustment(ball,contact,goal)
    terminal_range=distance(contact,goal)+25<=config['reactive_shot_max_mm']
    kind='TRAP_SHOT' if terminal_range else 'ADVANCE_PASS'
    cost=prep+flight+adjustment+.15+distance(contact,goal)/1800
    legs=[PlanLeg('pass-0','pass',sender,receiver,contact,receiving,heading,contact,power,'TRAP')]
    if terminal_range:
        legs.append(PlanLeg('shot-1','shot',receiver,None,contact,None,None,goal,config['reactive_shot_power'],
                            'STATIC',adjustment*1.4+.3))
    return AttackPlan('',0,kind,0,0,tuple(legs),receiver if terminal_range else None,
                      goal if terminal_range else None,max(.1,cost*.7),cost*1.4+.3)


class EpisodeBudget:
    """同一球位的失败重规划不能延长总预算；新裁判回合/明显球位变化才重置。"""
    def __init__(self,seconds):
        self.seconds=seconds;self.started=None;self.deadline=None;self.origin=None;self.epoch=None
    def observe(self,now,ball,epoch):
        if self.started is None or epoch!=self.epoch or distance(ball,self.origin)>400:
            self.started=now;self.deadline=now+self.seconds;self.origin=ball;self.epoch=epoch
        return now>=self.deadline


def validate_config(c):
    if c.get('strategy_version')!='2.3':return
    if (c.get('v23_mode')!='development' or type(c.get('attack_plan_contract_version')) is not int
            or c['attack_plan_contract_version']!=1):
        raise ValueError('V2.3 requires explicit development AttackPlan contract 1')
    if c.get('v22_mode')!='reactive-development':
        raise ValueError('V2.3 currently supports explicit local heuristic mode only')
    if c.get('enable_fast_finish') is not False:
        raise ValueError('V2.3 fast finish is not physically verified; leave disabled')
    for key,lo,hi in [('v23_advance_pass_max_mm',1400,2100),('v23_episode_seconds',8,10),
                      ('v23_terminal_timeout_s',.5,4)]:
        value=c.get(key)
        if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi:
            raise ValueError('Invalid V2.3 parameter: '+key)
    if c.get('v23_advance_pass_power')!=6000:
        raise ValueError('V2.3 advance pass uses explicit local experimental power 6000')
