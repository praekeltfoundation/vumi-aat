import json

from twisted.internet.defer import inlineCallbacks
from twisted.python import log
from twisted.web import http

from vumi.message import TransportUserMessage
from vumi.transports.httprpc import HttpRpcTransport


from xml.etree.ElementTree import Element, SubElement, tostring

class AatUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with AAT
    """
    transport_type = 'ussd'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'provider'])
    IGNORE_FIELDS = set(['request'])

    def get_to_addr(self, request):
        """
        Extracts the request url path's suffix and uses it to obtain the tag
        associated with the suffix. Returns a tuple consisting of the tag and
        a dict of errors encountered.
        """
        errors = {}

        [suffix] = request.postpath
        tag = self.suffix_to_addrs.get(suffix, None)
        if tag is None:
            errors['unknown_suffix'] = suffix

        return tag, errors

    def validate_config(self):
        super(AatUssdTransport, self).validate_config()

        # Mappings between url suffixes and the tags used as the to_addr for
        # inbound messages (e.g. shortcodes or longcodes). This is necessary
        # since the requests from AAT do not provided us with this.
        self.suffix_to_addrs = self.config['suffix_to_addrs']

        # Base URL

    def get_callback_url(self, request):
        [suffix] = request.postpath
        return "%s%s%s" % (
            self.config['base_url'],
            self.config['web_path'],
            suffix)

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        errors = {}

        to_address, to_addr_errors = self.get_to_addr(request)
        errors.update(to_addr_errors)

        values, field_value_errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS,
            self.IGNORE_FIELDS
        )
        errors.update(field_value_errors)

        from_address = values['msisdn']
        provider = values['provider']

        if 'request' in request.args:
            response = request.args.get('request')[0].decode(self.ENCODING)
            session_event = TransportUserMessage.SESSION_RESUME
        else:
            response = ""
            session_event = TransportUserMessage.SESSION_NEW

        if errors:
            log.msg('Unhappy incoming message: %s ' % (errors,))
            yield self.finish_request(
                message_id, json.dumps(errors), code=http.BAD_REQUEST
            )
            return

        log.msg('AatUssdTransport receiving inbound message from %s to %s.' %
                (from_address, to_address))

        yield self.publish_message(
            message_id=message_id,
            content=response,
            to_addr=to_address,
            from_addr=from_address,
            session_event=session_event,
            provider='aat',
            transport_type=self.transport_type,
            transport_metadata={
                'aat_ussd': {
                    'provider': provider,
                    'url': self.get_callback_url(request)
                }
            }
        )

    def generate_body(self, reply, callback):
        request = Element('request')
        headertext = SubElement(request, 'headertext')
        headertext.text = reply
        options = SubElement(request, 'options')
        option = SubElement(
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
            message['transport_metadata']['aat_ussd']['url']
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
