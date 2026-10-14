"""筛选单相机、单次出脚的完整滚动记录。只做诊断，不生成比赛模型。

换相机不能靠拼接坐标自动证明连续性。至少一台相机必须从静止起点
连续看到最终停球，否则返回未知。测得总位移也不是可保证射程下限。
"""
import math


def assess(rows, origin, departures):
    base=dict(usable_for_planning=False,physical_acceptance_passed=False,
              release_speed_calibrated=False)
    def reject(reason): return dict(base,qualified=False,reason=reason)
    if departures != 1: return reject('REQUIRES_EXACTLY_ONE_DEPARTURE')
    if not rows or rows[-1].get('kind')!='RAW_TRIAL_END' or rows[-1].get('interrupted'):
        return reject('RAW_RECORD_INTERRUPTED')
    if rows[-1].get('poll_failures') or any(r.get('kind')=='RAW_POLL_UNAVAILABLE' for r in rows):
        return reject('RAW_POLL_UNAVAILABLE')
    cameras={}
    for row in rows:
        if row.get('kind')!='RAW_CAPTURE' or row.get('ball') is None: continue
        values=list(row['ball'])+[row['capture_time'],row['receive_time']]
        if len(row['ball'])!=2 or not all(type(v) in (int,float) and math.isfinite(v) for v in values):
            return reject('INVALID_RAW_SAMPLE')
        cameras.setdefault(row['camera'],[]).append(row)
    candidates=[]
    rejected=[]
    for camera,samples in cameras.items():
        samples=sorted(samples,key=lambda r:r['capture_time'])
        if len(samples)<30 or math.dist(samples[0]['ball'],origin)>10:
            rejected.append('CAMERA_MISSING_RELEASE_ORIGIN');continue
        if samples[-1]['capture_time']-samples[0]['capture_time']<1:
            rejected.append('INSUFFICIENT_DURATION');continue
        failure=None
        speeds=[]
        for a,b in zip(samples,samples[1:]):
            dt=b['capture_time']-a['capture_time']
            if not .001<=dt<=.12:
                failure='CAPTURE_GAP_OR_DUPLICATE';break
            speed=math.dist(a['ball'],b['ball'])/dt
            if speed>6500:
                failure='RAW_SPEED_LIMIT_OR_RESET_SUSPECT';break
            speeds.append(speed)
            if math.dist(b['ball'],origin)>180 and any(math.dist(b['ball'],robot[2:4])<125 for robot in b.get('robots',[])):
                failure='LATE_CONTACT_SUSPECT';break
        if failure:
            rejected.append(failure);continue
        end=samples[-1]['capture_time']
        tail=[r for r in samples if end-r['capture_time']<=.6]
        if len(tail)<10 or tail[-1]['capture_time']-tail[0]['capture_time']<.5:
            rejected.append('NO_TERMINAL_REST_WINDOW');continue
        if max(math.dist(r['ball'],tail[-1]['ball']) for r in tail)>5:
            rejected.append('BALL_NOT_CONFIRMED_AT_REST');continue
        displacement=math.dist(origin,tail[-1]['ball'])
        if displacement<100:
            rejected.append('NO_MEASURABLE_RELEASE');continue
        candidates.append(dict(camera=camera,raw_samples=len(samples),end_ball_mm=tail[-1]['ball'],
            observed_displacement_mm=displacement,raw_step_speed_max_mm_s=max(speeds),
            measured_duration_s=end-samples[0]['capture_time'],
            qualified=True,reason='SINGLE_CAMERA_REST_TO_REST_DIAGNOSTIC'))
    if not candidates: return dict(reject('NO_COMPLETE_SINGLE_CAMERA_TRACK'),camera_rejections=sorted(set(rejected)))
    # Multiple cameras may independently cover the whole path. They must agree
    # on the resting endpoint; don't manufacture extra independent shot counts.
    if any(math.dist(a['end_ball_mm'],b['end_ball_mm'])>10 for a in candidates for b in candidates):
        return reject('CAMERAS_DISAGREE_ON_ENDPOINT')
    best=max(candidates,key=lambda r:r['raw_samples'])
    return dict(base,**best,independently_covering_cameras=[r['camera'] for r in candidates],
                limitation='Diagnostic displacement only; no release-speed fit, holdout acceptance or physics-source verification.')
