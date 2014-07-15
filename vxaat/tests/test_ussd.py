import json

from twisted.internet.defer import inlineCallbacks

from vumi.message import TransportUserMessage
from vumi.tests.helpers import VumiTestCase
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper

from vxaat.ussd import AatUssdTransport


class TestAatUssdTransport(VumiTestCase):

    @inlineCallbacks
    def setUp(self):
        request_defaults = {
            'msisdn': '27729042520',
            'provider': 'MTN',
        }
        self.config = {
            'base_url': "http://www.example.com/foo",
            'web_path': '/api/v1/aat/ussd/',
            'web_port': '0',
            'to_addr': '1234'
        }
        self.tx_helper = self.add_helper(
            HttpRpcTransportHelper(
                AatUssdTransport,
                request_defaults=request_defaults,
            )
        )
        self.transport = yield self.tx_helper.get_transport(self.config)
        self.transport_url = self.transport.get_transport_url(
            self.config['web_path'],
        )

    def callback_url(self):
        # Not sure if I should reconstruct it here.
        return ("http://www.example.com/foo/api/v1/aat/ussd/"
                "?session_event=resume")

    def assert_inbound_message(self, msg, **field_values):
        expected_field_values = {
            'content':  "",
            'to_addr': '1234',
            'from_addr': self.tx_helper.request_defaults['msisdn'],
        }
        expected_field_values.update(field_values)
        for field, expected_value in expected_field_values.iteritems():
            self.assertEqual(msg[field], expected_value)

    def assert_outbound_message(self, msg, content, callback,
                                continue_session=True):
        headertext = '<headertext>%s</headertext>' % content

        if continue_session:
            options = (
                '<options>'
                '<option callback="%s" command="1" display="false"'
                ' order="1" />'
                '</options>'
            ) % callback
        else:
            options = ""

        xml = ''.join([
            '<request>',
            headertext,
            options,
            '</request>',
        ])

        self.assertEqual(msg, xml)

    def assert_ack(self, ack, reply):
        self.assertEqual(ack.payload['event_type'], 'ack')
        self.assertEqual(ack.payload['user_message_id'], reply['message_id'])
        self.assertEqual(ack.payload['sent_message_id'], reply['message_id'])

    def assert_nack(self, nack, reply, reason):
        self.assertEqual(nack.payload['event_type'], 'nack')
        self.assertEqual(nack.payload['user_message_id'], reply['message_id'])
        self.assertEqual(nack.payload['nack_reason'], reason)

    @inlineCallbacks
    def test_inbound_begin(self):

        # Send initial request
        d = self.tx_helper.mk_request()
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=None,
        )

        reply_content = 'We are the Knights Who Say ... Ni!'
        reply = msg.reply(reply_content)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            reply_content,
            self.callback_url(),
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_begin_with_close(self):

        # Send initial request
        d = self.tx_helper.mk_request()
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=None,
        )

        reply_content = 'We are no longer the Knight who say Ni!'
        reply = msg.reply(reply_content, continue_session=False)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            reply_content,
            self.callback_url(),
            continue_session=False,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_resume_and_reply_with_end(self):

        user_content = "I didn't expect a kind of Spanish Inquisition!"
        d = self.tx_helper.mk_request(request=user_content,
                                      session_event="resume")
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_RESUME,
            content=user_content,
        )

        reply_content = "Nobody expects the Spanish Inquisition!"
        reply = msg.reply(reply_content, continue_session=False)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            reply_content,
            self.callback_url(),
            continue_session=False,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_inbound_resume_and_reply_with_resume(self):

        user_content = "Well, what is it you want?"
        d = self.tx_helper.mk_request(request=user_content,
                                      session_event="resume")
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_RESUME,
            content=user_content,
        )

        reply_content = "We want ... a shrubbery!"
        reply = msg.reply(reply_content, continue_session=True)
        self.tx_helper.dispatch_outbound(reply)
        response = yield d

        self.assert_outbound_message(
            response.delivered_body,
            reply_content,
            self.callback_url(),
            continue_session=True,
        )

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)

    @inlineCallbacks
    def test_request_with_missing_parameters(self):
        response = yield self.tx_helper.mk_request_raw(
            params={"request": '', "provider": ''})

        self.assertEqual(
            json.loads(response.delivered_body),
            {'missing_parameter': ['msisdn']})

        self.assertEqual(response.code, 400)

    @inlineCallbacks
    def test_request_with_unexpected_parameters(self):
        response = yield self.tx_helper.mk_request(
            unexpected_p1='', unexpected_p2='')

        self.assertEqual(response.code, 400)
        body = json.loads(response.delivered_body)
        self.assertEqual(set(['unexpected_parameter']), set(body.keys()))
        self.assertEqual(
            sorted(body['unexpected_parameter']),
            ['unexpected_p1', 'unexpected_p2'])

    @inlineCallbacks
    def test_no_reply_to_in_response(self):
        msg = yield self.tx_helper.make_dispatch_outbound(
            content="Nudge, nudge, wink, wink. Know what I mean?",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Outbound message is not a reply")

    @inlineCallbacks
    def test_no_content_in_reply(self):
        msg = yield self.tx_helper.make_dispatch_outbound(
            content="",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Outbound message has no content.")

    @inlineCallbacks
    def test_failed_request(self):
        msg = yield self.tx_helper.make_dispatch_outbound(
            in_reply_to='xxxx',
            content="She turned me into a newt!",
            message_id=1
        )
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_nack(nack, msg, "Response to http request failed.")

    @inlineCallbacks
    def test_ussd_session_id_handled(self):
        ussd_session_id = 'xxxx'
        user_content = "Well, what is it you want?"
        d = self.tx_helper.mk_request(request=user_content,
                                      ussdSessionId=ussd_session_id)
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)
        self.assert_inbound_message(
            msg,
            session_event=TransportUserMessage.SESSION_NEW,
            content=user_content,
            transport_metadata={
                'aat_ussd': {
                    'provider': 'MTN',
                    'ussd_session_id': ussd_session_id,
                }
            }
        )

        reply_content = "We want ... a shrubbery!"
        reply = msg.reply(reply_content, continue_session=True)
        self.tx_helper.dispatch_outbound(reply)
        yield d

        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assert_ack(ack, reply)
