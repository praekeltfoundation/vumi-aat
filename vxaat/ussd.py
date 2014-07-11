import json
from xml.etree.ElementTree import Element, SubElement, tostring

from twisted.internet.defer import inlineCallbacks
from twisted.python import log
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.config import ConfigText
from vumi.transports.httprpc import HttpRpcTransport


class AatUssdTransportConfig(HttpRpcTransport.CONFIG_CLASS):
    to_addr = ConfigText('The USSD code ',
                         required=True, static=True)
    base_url = ConfigText('The base url of the transport ',
                          required=True, static=True)


class AatUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with AAT
    """
    transport_type = 'ussd'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'provider'])
    OPTIONAL_FIELDS = set(['request'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    INSUFFICIENT_MSG_FIELDS_ERROR = "Insufficient message fields provided."

    CONFIG_CLASS = AatUssdTransportConfig

    def get_callback_url(self):
        config = self.get_static_config()
        return "%s%s" % (
            config.base_url,
            config.web_path)

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        errors = {}
        to_address = self.get_static_config().to_addr

        values, field_value_errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS,
            self.OPTIONAL_FIELDS,
        )
        errors.update(field_value_errors)

        if errors:
            log.msg('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        from_address = values['msisdn']
        provider = values['provider']

        if 'request' in request.args:
            response = request.args.get('request')[0].decode(self.ENCODING)
            session_event = TransportUserMessage.SESSION_RESUME
        else:
            response = ""
            session_event = TransportUserMessage.SESSION_NEW

        log.msg('AatUssdTransport receiving inbound message from %s to %s.' %
                (from_address, to_address))

        yield self.publish_message(
            message_id=message_id,
            content=response,
            to_addr=to_address,
            from_addr=from_address,
            session_event=session_event,
            transport_type=self.transport_type,
            transport_metadata={
                'aat_ussd': {
                    'provider': provider
                }
            }
        )

    def generate_body(self, reply, callback, session_event):
        request = Element('request')
        headertext = SubElement(request, 'headertext')
        headertext.text = reply

        # If this is not a session close event, then send options
        if session_event != TransportUserMessage.SESSION_CLOSE:
            options = SubElement(request, 'options')
            SubElement(
                options,
                'option',
                {
                    'command': '1',
                    'order': '1',
                    'callback': callback,
                    'display': "false"
                }
            )

        return tostring(
            request,
            encoding='utf-8'
        )

    @inlineCallbacks
    def handle_outbound_message(self, message):
        error = None
        message_id = message['message_id']
        body = self.generate_body(
            message['content'],
            self.get_callback_url(),
            message['session_event']
        )

        if message.payload.get('in_reply_to') and 'content' in message.payload:
            response_id = self.finish_request(
                message['in_reply_to'],
                body.encode(self.ENCODING),
            )

            if response_id is None:
                error = self.RESPONSE_FAILURE_ERROR
        else:
            error = self.INSUFFICIENT_MSG_FIELDS_ERROR

        if error is not None:
            yield self.publish_nack(message_id, error)
            return

        yield self.publish_ack(user_message_id=message_id,
                               sent_message_id=message_id)
