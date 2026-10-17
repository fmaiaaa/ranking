import contextlib
import io
import unittest
import requests
from listen import observe


class ListenerTest(unittest.TestCase):
    def scenario(self, replies):
        now = [0]
        calls = []
        def send(messages, timeout):
            m = messages[0]
            calls.append(m)
            channel = m['channel']
            if channel == '/meta/handshake':
                return [{'channel': channel, 'successful': True, 'clientId': 'fake'}]
            if channel == '/meta/subscribe':
                return [{'channel': channel, 'successful': True}]
            if channel == '/meta/disconnect':
                return []
            now[0] += 4
            item = replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = observe(send, seconds=10, clock=lambda: now[0], sleep=lambda _: None)
        return status, output.getvalue(), calls

    def test_timeout_then_event_and_deduplication(self):
        ack = {'channel': '/meta/connect', 'successful': True}
        event = {'channel': '/data/ChangeEvents', 'data': {'event': {'replayId': 9}, 'payload': {
            'ChangeEventHeader': {'entityName': 'Opportunity', 'changeType': 'UPDATE', 'recordIds': ['PRIVATE']},
            'Name': 'CUSTOMER_PRIVATE'}}}
        status, output, _ = self.scenario([requests.ReadTimeout(), [ack, event], [ack, event]])
        self.assertEqual(status, 0)
        self.assertIn('"Opportunity": 1', output)
        self.assertIn('"read_timeouts": 1', output)
        self.assertNotIn('PRIVATE', output)

    def test_unknown_client_resubscribes(self):
        bad = {'channel': '/meta/connect', 'successful': False, 'advice': {'reconnect': 'handshake'}}
        ack = {'channel': '/meta/connect', 'successful': True}
        status, output, calls = self.scenario([[bad], [ack], [ack]])
        self.assertEqual(status, 0)
        self.assertEqual(sum(m['channel'] == '/meta/subscribe' for m in calls), 2)
        self.assertIn('NO_EVENTS_OBSERVED', output)

    def test_only_timeouts_are_transport_blocked(self):
        status, output, _ = self.scenario([requests.ReadTimeout() for _ in range(3)])
        self.assertEqual(status, 3)
        self.assertIn('TRANSPORT_BLOCKED', output)


if __name__ == '__main__':
    unittest.main()
