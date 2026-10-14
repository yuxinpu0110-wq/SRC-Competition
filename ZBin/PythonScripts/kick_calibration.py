"""只读汇总事务与原始球路，保留失败；没有测到的力度不能外推为安全。"""
import math
import statistics


def percentile(values, q):
    if not values:
        return None
    values=sorted(values)
    return values[min(len(values)-1, math.ceil(len(values)*q)-1)]


def rolling_time(length, speed, deceleration):
    """mm/s 与 mm/s²；到不了目标返回 None，绝不把力度当速度。"""
    if not all(math.isfinite(v) for v in (length,speed,deceleration)) or length < 0 or speed <= 0 or deceleration < 0:
        return None
    if deceleration < 1e-9:
        return length/speed
    disc=speed*speed-2*deceleration*length
    return None if disc < 0 else 2*length/(speed+math.sqrt(disc))


def fit_window(samples, expected_angle):
    """同相机至少5帧的无明显转折区间；报告窗口速度，不冒充瞬时初速。"""
    if len(samples) < 5:
        return dict(valid=False,reason='INSUFFICIENT_RAW_SAMPLES')
    cameras={s['camera'] for s in samples}
    if len(cameras) != 1:
        return dict(valid=False,reason='CAMERA_CHANGED')
    speeds,angles,midpoints=[],[],[]
    for a,b in zip(samples,samples[1:]):
        dt=b['capture_time']-a['capture_time']
        dx,dy=b['ball'][0]-a['ball'][0],b['ball'][1]-a['ball'][1]
        if not .001 <= dt <= .1:
            return dict(valid=False,reason='INVALID_CAPTURE_INTERVAL')
        speed=math.hypot(dx,dy)/dt
        if not 500 < speed <= 6500:
            return dict(valid=False,reason='STATIONARY_COLLISION_OR_SPEED_LIMIT')
        angles.append(math.atan2(dy,dx))
        speeds.append(speed)
        midpoints.append((a['capture_time']+b['capture_time'])/2)
    direction=math.atan2(sum(math.sin(a) for a in angles),sum(math.cos(a) for a in angles))
    wrap=lambda a:math.atan2(math.sin(a),math.cos(a))
    if max(abs(wrap(a-direction)) for a in angles) > .08:
        return dict(valid=False,reason='TRAJECTORY_TURNED')
    # 稳健中位斜率估计速度变化；明显再加速不当作踢球器初段。
    slopes=[(speeds[j]-speeds[i])/(midpoints[j]-midpoints[i])
            for i in range(len(speeds)) for j in range(i+1,len(speeds))]
    slope=statistics.median(slopes)
    if slope > 500:
        return dict(valid=False,reason='REACCELERATION_OR_COLLISION')
    return dict(valid=True,camera=next(iter(cameras)),raw_samples=len(samples),
        observed_speed_interval_mm_s=[min(speeds),max(speeds)],
        observed_speed_median_mm_s=statistics.median(speeds),
        deceleration_estimate_mm_s2=max(0,-slope),
        target_error_deg=math.degrees(wrap(direction-expected_angle)),
        speed_semantics='measured_window_speed_not_instantaneous_release_speed')


def analyze(rows):
    requests,departures={},{}
    raw={}
    audit_departures, audit_raw = {}, {}
    def identity(event, row):
        return (event.get('run_id', row.get('run_id', 'legacy')),
                event.get('reset_epoch', row.get('reset_epoch', 0)),
                event.get('plan_id', 'legacy'), event.get('leg_id', '0'),
                event.get('transaction_id'))
    for row in rows:
        for e in row.get('physical_events',[]):
            key=identity(e, row)
            if e.get('kind')=='KICK_REQUESTED':
                requests[key]=dict(e, power=None)
            elif e.get('kind')=='BALL_DEPARTED':
                departures[key]=e
        for pulse in row.get('kick_events',[]):
            key=identity(pulse, row)
            if key in requests:
                requests[key]['power']=pulse.get('power')
        for e in row.get('flight_audit_events', []):
            key=identity(e, row)
            if e.get('kind') == 'BALL_DEPARTED':
                audit_departures[key]=e
            elif e.get('kind') == 'FLIGHT_SAMPLE' and e.get('ball'):
                audit_raw[key,e['camera'],e['frame_id']]=e
        sample=row.get('raw_capture')
        if sample and sample.get('ball') and row.get('observation',{}).get('fresh'):
            raw[row.get('run_id','legacy'),row.get('reset_epoch',0),sample['camera'],sample['frame_id']]=sample
    results=[]
    for key,e in requests.items():
        departure=departures.get(key)
        item=dict(run_id=key[0],reset_epoch=key[1],plan_id=key[2],leg_id=key[3],
                  transaction_id=key[4],robot=e['robot'],power=e['power'],departed=departure is not None,
                  departure_confirmation_delay_s=departure['delay_s'] if departure else None)
        audit=audit_departures.get(key)
        item['audit_departed']=audit is not None
        if audit:
            item['audit_confirmation_delay_s']=audit['delay_s']
            samples=sorted((s for sample_key,s in audit_raw.items() if sample_key[0] == key
                           and audit['now'] <= s['receive_time'] <= audit['now']+.25),
                           key=lambda s:s['capture_time'])
            angle=math.atan2(e['target']['y']-e['start_ball']['y'],e['target']['x']-e['start_ball']['x'])
            item['audit_flight']=fit_window(samples,angle)
        if departure:
            dx=e['target']['x']-e['start_ball']['x']
            dy=e['target']['y']-e['start_ball']['y']
            # 确认离脚后0..0.25s；分离的窗口可能错过真正瞬时初速，明确保留语义。
            samples=sorted((s for identity_key,s in raw.items() if identity_key[:2] == key[:2]
                           and departure['now'] <= s['receive_time'] <= departure['now']+.25),
                           key=lambda s:s['capture_time'])
            item['flight']=fit_window(samples,math.atan2(dy,dx))
        results.append(item)
    groups={}
    for item in results:
        key=f"robot-{item['robot']}/power-{item['power']}"
        groups.setdefault(key,[]).append(item)
    summary={}
    for key,items in groups.items():
        delays=[i['departure_confirmation_delay_s'] for i in items if i['departed']]
        clean=[i['flight'] for i in items if i.get('flight',{}).get('valid')]
        summary[key]=dict(attempts=len(items),departures=len(delays),departure_rate=len(delays)/len(items),
            confirmation_delay_p50_s=percentile(delays,.5),confirmation_delay_p95_s=percentile(delays,.95),
            clean_raw_flights=len(clean),
            measured_window_speed_interval_mm_s=[min(i['observed_speed_interval_mm_s'][0] for i in clean),
                max(i['observed_speed_interval_mm_s'][1] for i in clean)] if clean else None)
    return dict(schema_version=1,status='DIAGNOSTIC_ONLY' if results else 'NO_SAMPLES',
                usable_for_planning=False,instantaneous_release_speed_calibrated=False,
                groups=summary,transactions=results,
                limitation='Raw-associated fused timestamps are unknown; window speeds and confirmation delays are not a validated release model.')
