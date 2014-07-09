import json

from twisted.internet.defer import inlineCallbacks
from twisted.python import log
from twisted.web import http

from vumi.transports.httprpc import HttpRpcTransport
from vumi.components.session import SessionManager
class AatUssdTransport(HttpRpcTransport):
    """
    HTTP transport for USSD with AAT
    """
    transport_type = 'ussd'
    ENCODING = 'utf-8'
    EXPECTED_FIELDS = set(['msisdn', 'request'])

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

    @inlineCallbacks
    def setup_transport(self):
        super(AatUssdTransport, self).setup_transport()

    @inlineCallbacks
    def teardown_transport(self):
        yield super(AatUssdTransport, self).teardown_transport()

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        errors = {}

        to_address, to_addr_errors = self.get_to_addr()
        errors.update(to_addr_errors)

        values, field_value_errors = self.get_field_values(
            request,
            self.EXPECTED_FIELDS
        )
        errors.update(field_value_errors)

        from_address = values['msisdn']
        response = values['request']

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
            from_address=from_address,
            provider='aat',
            transport_type=self.transport_type
        )
