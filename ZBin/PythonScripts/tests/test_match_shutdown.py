import subprocess
import unittest
from unittest.mock import Mock,patch
from match_shutdown import cleanup
from safety_watchdog import DecisionWatchdog
from match_processes import process_count


class ShutdownTests(unittest.TestCase):
    def test_failure_does_not_skip_other_stop_or_closures(self):
        calls=[]
        def failed():
            calls.append('failed');raise OSError(10014,'invalid pointer')
        errors=cleanup([('native',failed),('stop',lambda:calls.append('stop')),
                        ('lock',lambda:calls.append('lock'))])
        self.assertEqual(calls,['failed','stop','lock'])
        self.assertEqual(errors[0][0],'native')
        self.assertIsInstance(errors[0][1],OSError)

    def test_successful_cleanup(self):
        self.assertEqual(cleanup([('close',Mock())]),[])

    def test_watchdog_send_failure_closes_socket_and_leaves_worker_stop_path(self):
        watchdog=DecisionWatchdog.__new__(DecisionWatchdog)
        watchdog.port=45994;watchdog.socket=Mock();watchdog.process=Mock()
        watchdog.socket.sendto.side_effect=OSError(10014,'invalid pointer')
        with self.assertRaises(OSError):watchdog.close()
        watchdog.socket.close.assert_called_once()
        watchdog.process.wait.assert_called_once_with(timeout=3.5)
        watchdog.process.terminate.assert_not_called()

    def test_watchdog_timeout_is_visible_and_worker_not_killed(self):
        watchdog=DecisionWatchdog.__new__(DecisionWatchdog)
        watchdog.port=45994;watchdog.socket=Mock();watchdog.process=Mock()
        watchdog.process.wait.side_effect=subprocess.TimeoutExpired('worker',3.5)
        with self.assertRaises(subprocess.TimeoutExpired):watchdog.close()
        watchdog.socket.close.assert_called_once()
        watchdog.process.terminate.assert_not_called()

    @patch('match_processes.subprocess.run')
    def test_inventory_has_deadline_and_counts_exact_process(self,run):
        run.return_value=Mock(returncode=0,stdout=b'"Client.exe","1"\r\n"Other.exe","2"\r\n')
        self.assertEqual(process_count('Client.exe'),1)
        self.assertEqual(run.call_args.kwargs['timeout'],10)

    @patch('match_processes.subprocess.run')
    def test_failed_inventory_cannot_prove_no_controller(self,run):
        run.return_value=Mock(returncode=1)
        with self.assertRaises(RuntimeError):process_count('Client.exe')

    @patch('match_processes.subprocess.run')
    def test_inventory_timeout_is_not_zero_processes(self,run):
        run.side_effect=subprocess.TimeoutExpired('tasklist',10)
        with self.assertRaises(subprocess.TimeoutExpired):process_count('Client.exe')


if __name__=='__main__':unittest.main()
