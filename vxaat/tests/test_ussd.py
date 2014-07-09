from twisted.internet.defer import inlineCallbacks

from vumi.message import TransportUserMessage
from vumi.tests.helpers import VumiTestCase
from vxaat.ussd import AatUssdTransport
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper

class TestAatUssdTransport(VumiTestCase):
    _from_addr = '27729042520'
    _to_addr = '1234'
    _request_defaults = {
        'msisdn': _from_addr,
        'request': "He's not dead, he is pining for the fjords",
    }

    @inlineCallbacks
    def setUp(self):
        self.config = {
            'web_path': '/api/v1/aat/ussd/',
            'suffix_to_addrs': {
                'some-suffix': self._to_addr,
                'some-more-suffix': '4321'
            }
        }
        self.tx_helper = self.add_helper(
            HttpRpcTransportHelper(
                AatUssdTransport,
                request_defaults=self._request_defaults
            )
        )
        self.transport = yield self.tx_helper.get_transport(self.config)
        self.transport_url = self.transport.get_transport_url(
            self.config['web_path']
        )
        yield self.session_manager.redis._purge_all()  # just in case

    def assert_inbound_message(self, msg, **field_values):
        expected_field_values = {
            'content': self._request_defaults['request'],
            'to_addr': '1234',
            'from_addr': self._request_defaults['msisdn'],
            'transport_metadata': {
                'aat_ussd': {

                },
            }
        }
        expected_field_values.update(field_values)

        for field, expected_value in expected_field_values.iteritems():
            self.assertEqual(msg[field], expected_value)

    @inlineCallbacks
    def test_inbound_begin(self):

        # Second connect is the actual start of the session
        user_content = "Who are you?"
        d = self.tx_helper.mk_request('some-suffix', msg=user_content)
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=user_content
        )

        reply_content = "We are the Knights Who Say ... Ni!"
        reply = msg.reply(reply_content)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d
        self.assertEqual(response.delivered_body, reply_content)
        self.assertEqual(
            response.headers.getRawHeaders('X-USSD-SESSION'), ['1'])

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)