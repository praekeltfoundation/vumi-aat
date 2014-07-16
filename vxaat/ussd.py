import json
from xml.etree.ElementTree import Element, SubElement, tostring, ElementTree

from twisted.internet.defer import inlineCallbacks
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.config import ConfigText
from vumi.transports.httprpc import HttpRpcTransport
from vumi import log


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
    OPTIONAL_FIELDS = set(['request', 'ussdSessionId', 'session_event'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    NOT_REPLY_ERROR = "Outbound message is not a reply"
    NO_CONTENT_ERROR = "Outbound message has no content."

    CONFIG_CLASS = AatUssdTransportConfig

    def get_callback_url(self):
        config = self.get_static_config()
        return "%s%s?session_event=resume" % (
            config.base_url.rstrip("/"),
            config.web_path)

    def get_optional_field_values(self, request, optional_fields=frozenset()):
        values = {}
        for field in optional_fields:
            if field in request.args:
                raw_value = request.args.get(field)[0]
                values[field] = raw_value.decode(self.ENCODING)
            else:
                values[field] = None
        return values

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        to_address = self.get_static_config().to_addr

        values, errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS,
            self.OPTIONAL_FIELDS,
        )

        optional_values = self.get_optional_field_values(
            request,
            self.OPTIONAL_FIELDS
        )

        if errors:
            log.info('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        from_address = values['msisdn']
        provider = values['provider']
        ussd_session_id = optional_values['ussdSessionId']

        if optional_values['session_event'] == "resume":
            session_event = TransportUserMessage.SESSION_RESUME
            content = optional_values['request']
            code = None
        else:
            session_event = TransportUserMessage.SESSION_NEW
            content = None
            code = optional_values['request']

        log.info('AatUssdTransport receiving inbound message from %s to %s.' %
                (from_address, to_address))

        yield self.publish_message(
            message_id=message_id,
            content=content,
            to_addr=to_address,
            from_addr=from_address,
            session_event=session_event,
            transport_type=self.transport_type,
            transport_metadata={
                'aat_ussd': {
                    'provider': provider,
                    'ussd_session_id': ussd_session_id,
                    'code': code
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
            encoding='utf-8',
        )

    @inlineCallbacks
    def handle_outbound_message(self, message):
        # Generate outbound message
        message_id = message['message_id']
        body = self.generate_body(
            message['content'],
            self.get_callback_url(),
            message['session_event']
        )
        log.info('AatUssdTransport outbound message with content: %r'
                 % (body,))

        # Errors
        if not message['content']:
            yield self.publish_nack(message_id, self.NO_CONTENT_ERROR)
            return
        if not message['in_reply_to']:
            yield self.publish_nack(message_id, self.NOT_REPLY_ERROR)
            return

        # Finish Request
        response_id = self.finish_request(
            message['in_reply_to'],
            body,
        )

        # Response failure
        if response_id is None:
            yield self.publish_nack(message_id, self.RESPONSE_FAILURE_ERROR)
            return

        yield self.publish_ack(user_message_id=message_id,
                               sent_message_id=message_id)

