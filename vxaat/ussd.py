import json
from urllib import quote
from xml.etree.ElementTree import Element, SubElement, tostring

from twisted.internet.defer import inlineCallbacks
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.config import ConfigText, ConfigDict
from vumi.transports.httprpc import HttpRpcTransport


class AatUssdTransportConfig(HttpRpcTransport.CONFIG_CLASS):
    base_url = ConfigText(
        'The base url of the transport',
        required=True, static=True)
    provider_mappings = ConfigDict(
        'Mappings from the provider values received from aat to normalised '
        'provider values',
        static=True, default={})


class AatUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with AAT
    """
    transport_type = 'ussd'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'provider'])
    OPTIONAL_FIELDS = set(['request', 'ussdSessionId', 'to_addr'])

    # errors
    RESPONSE_FAILURE_ERROR = "Response to http request failed."
    NOT_REPLY_ERROR = "Outbound message is not a reply"
    NO_CONTENT_ERROR = "Outbound message has no content."

    CONFIG_CLASS = AatUssdTransportConfig

    @inlineCallbacks
    def setup_transport(self):
        yield super(AatUssdTransport, self).setup_transport()
        config = self.get_static_config()
        self.provider_mappings = config.provider_mappings

    def get_callback_url(self, to_addr):
        config = self.get_static_config()
        return "%s%s?to_addr=%s" % (
            config.base_url.rstrip("/"),
            config.web_path,
            quote(to_addr))

    def get_optional_field_values(self, request, optional_fields=frozenset()):
        values = {}
        for field in optional_fields:
            if field in request.args:
                raw_value = request.args.get(field)[0]
                values[field] = raw_value.decode(self.ENCODING)
            else:
                values[field] = None
        return values

    def normalise_provider(self, provider):
        if provider not in self.provider_mappings:
            self.log.warning(
                "No mapping exists for provider '%s', "
                "using '%s' as a fallback" % (provider, provider,))
            return provider
        else:
            return self.provider_mappings[provider]

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
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
            self.log.info('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        from_addr = values['msisdn']
        provider = self.normalise_provider(values['provider'])
        ussd_session_id = optional_values['ussdSessionId']

        if optional_values['to_addr'] is not None:
            session_event = TransportUserMessage.SESSION_RESUME
            to_addr = optional_values['to_addr']
            content = optional_values['request']
        else:
            session_event = TransportUserMessage.SESSION_NEW
            to_addr = optional_values['request']
            content = None

        self.log.info(
            'AatUssdTransport receiving inbound message (%s) from %s to %s.'
            % (message_id, from_addr, to_addr))

        yield self.publish_message(
            message_id=message_id,
            content=content,
            to_addr=to_addr,
            from_addr=from_addr,
            session_event=session_event,
            transport_type=self.transport_type,
            helper_metadata={
                'session_id': ussd_session_id,
            },
            transport_metadata={
                'aat_ussd': {
                    'provider': provider,
                    'ussd_session_id': ussd_session_id,
                }
            },
            provider=provider,
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
            self.get_callback_url(message['from_addr']),
            message['session_event']
        )
        self.log.info('AatUssdTransport outbound message (%s) with content: %r'
                      % (message_id, body,))

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
            # we don't yield on this publish because if a message store is
            # used, that causes the worker to wait for Riak before processing
            # the message and responding to USSD messages is time critical.
            self.publish_nack(message_id, self.RESPONSE_FAILURE_ERROR)
            return

        # we don't yield on this publish because if a message store is
        # used, that causes the worker to wait for Riak before processing
        # the message and responding to USSD messages is time critical.
        self.publish_ack(
            user_message_id=message_id, sent_message_id=message_id)
