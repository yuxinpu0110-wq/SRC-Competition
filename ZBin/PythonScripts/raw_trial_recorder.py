"""Read-only raw relay evidence; no SDK import, commands or model promotion.

Records every available camera separately. Merely storing overlapping cameras
does NOT certify a continuous trajectory across cameras.
"""
import json
import time


class RawTrialRecorder:
    def __init__(self, path, query=None):
        self.path=path
        self.query=query
        self.stream=None
        self.seen=set()
        self.samples=0
        self.poll_failures=0
        self.last_poll=0
        self.started=time.perf_counter()

    def __enter__(self):
        if self.path is not None:
            self.stream=self.path.open('w',encoding='utf8')
            self.stream.write(json.dumps(dict(kind='RAW_TRIAL_START',started=self.started,
                source='independent_relay_captures',coordinate_frame='simulator_world',
                certified_camera_handoff=False,usable_for_planning=False))+'\n')
        return self

    def record(self, captures):
        for c in captures:
            key=(c['camera'],c['frame_id'],c['capture_time'])
            if key in self.seen or c['receive_time'] < self.started:
                continue
            self.seen.add(key)
            self.stream.write(json.dumps(dict(kind='RAW_CAPTURE',**c),allow_nan=False)+'\n')
            self.samples+=1

    def poll(self):
        now=time.perf_counter()
        if self.stream is None or now-self.last_poll < .02:
            return
        self.last_poll=now
        if self.query is None:
            from vision_relay import status
            data=status(command=b'captures')
        else:
            data=self.query()
        if not data or data.get('schema_version',0)<2:
            self.poll_failures+=1
            self.stream.write(json.dumps(dict(kind='RAW_POLL_UNAVAILABLE',now=now))+'\n')
        else:
            self.record(data.get('captures',[]))
        self.stream.flush()

    def __exit__(self, exc_type, exc, traceback):
        if self.stream is not None:
            self.stream.write(json.dumps(dict(kind='RAW_TRIAL_END',samples=self.samples,
                poll_failures=self.poll_failures,interrupted=exc_type is not None,
                usable_for_planning=False))+'\n')
            self.stream.close()
