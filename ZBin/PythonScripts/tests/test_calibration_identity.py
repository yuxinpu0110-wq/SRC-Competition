import unittest
from kick_calibration import analyze


class CalibrationIdentityTests(unittest.TestCase):
    def row(self, run, epoch=0, departure=False):
        base=dict(run_id=run, reset_epoch=epoch, plan_id='kick-1', leg_id='0', transaction_id=1)
        request=dict(base, kind='KICK_REQUESTED', now=10, robot=0,
                     start_ball={'x':2700,'y':0}, target={'x':4500,'y':0})
        events=[request]
        if departure:
            events.append(dict(base,kind='BALL_DEPARTED',now=10.1,delay_s=.1))
        return dict(run_id=run, reset_epoch=epoch,physical_events=events,
                    kick_events=[dict(base,power=4500)])

    def test_restarted_run_same_id_cannot_borrow_departure(self):
        result=analyze([self.row('a',departure=True),self.row('b')])
        self.assertEqual(len(result['transactions']),2)
        self.assertEqual([r['departed'] for r in result['transactions']],[True,False])

    def test_same_run_other_epoch_cannot_borrow_departure(self):
        result=analyze([self.row('a',0,True),self.row('a',1)])
        self.assertEqual([r['departed'] for r in result['transactions']],[True,False])

    def test_read_only_audit_does_not_restore_control_departure(self):
        row=self.row('a')
        request=row['physical_events'][0]
        row['flight_audit_events']=[dict(request,kind='BALL_DEPARTED',now=10.1,delay_s=.1)]
        result=analyze([row])
        tx=result['transactions'][0]
        self.assertFalse(tx['departed'])
        self.assertTrue(tx['audit_departed'])
        self.assertFalse(result['usable_for_planning'])
