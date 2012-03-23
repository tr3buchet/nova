# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import httplib
import json
import socket
import time
import urllib

from nova import flags
from nova import exception
from nova import log as logging
from nova.openstack.common import cfg


melange_opts = [
    cfg.StrOpt('melange_host',
               default='127.0.0.1',
               help='HOST for connecting to melange'),
    cfg.IntOpt('melange_port',
               default=9898,
               help='PORT for connecting to melange'),
    cfg.IntOpt('melange_num_retries',
               default=0,
               help='Number retries when contacting melange'),
    ]

FLAGS = flags.FLAGS
try:
    FLAGS.register_opts(melange_opts)
except cfg.DuplicateOptError:
    # NOTE(jkoelker) These options are verbatim in the legacy quantum
    #                manager. This is here to make sure they are
    #                registered in the tests.
    pass
LOG = logging.getLogger(__name__)

json_content_type = {'Content-type': 'application/json'}


class MelangeConnection(object):

    def __init__(self, host=None, port=None, use_ssl=False):
        if host is None:
            host = FLAGS.melange_host
        if port is None:
            port = FLAGS.melange_port
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.version = 'v1.0'

    def get(self, path, params=None, headers=None):
        return self.do_request('GET', path, params=params, headers=headers,
                               retries=FLAGS.melange_num_retries)

    def post(self, path, body=None, headers=None):
        return self.do_request('POST', path, body=body, headers=headers)

    def delete(self, path, headers=None):
        return self.do_request('DELETE', path, headers=headers)

    def put(self, path, body=None, headers=None):
        return self.do_request('PUT', path, body=body, headers=headers)

    def _get_connection(self):
        if self.use_ssl:
            return httplib.HTTPSConnection(self.host, self.port)
        else:
            return httplib.HTTPConnection(self.host, self.port)

    def do_request(self, method, path, body=None, headers=None, params=None,
                   content_type='.json', retries=0):
        headers = headers or {}
        params = params or {}

        url = '/%s/%s%s' % (self.version, path, content_type)
        if params:
            url += '?%s' % urllib.urlencode(params)

        if not headers.get('Content-type'):
            headers.update(json_content_type)

        if body is not None and not isinstance(body, basestring):
            body = json.dumps(body)

        LOG.debug(_('%(method)s\'ing %(url)s with %(body)s') %
                  {'method': method, 'url': url, 'body': body,
                   'headers': headers, 'params': params})
        for i in xrange(retries + 1):
            connection = self._get_connection()
            try:
                connection.request(method, url, body, headers)
                response = connection.getresponse()
                response_str = response.read()
                if response.status < 400:
                    return response_str
                raise Exception(_('Server returned error: %s' % response_str))
            except (socket.error, IOError):
                LOG.exception(_('Connection error contacting melange'
                                ' service, retrying'))

                time.sleep(1)

        raise exception.MelangeConnectionFailed(
                reason=_('Maximum attempts reached'))

    def allocate_for_instance_networks(self, tenant_id, instance_id,
                                       networks):
        """
        Create VIFS and allocate ips for each network.

        ``tenant_id``

            The tenant_id of the instance.

        ``instance_id``

            The identifier for the instance.
            Required.

        ``networks``

            An iterable of dicts of `{'tenant_id': network_tenant_id,
                                      'id': network_id}`.
            Required.
        """
        interfaces = [{'network': net,
                       'mac_address': None} for net in networks]
        instance = {'tenant_id': tenant_id,
                    'interfaces': interfaces}
        body = {'instance': instance}

        url = 'ipam/instances/%s/interfaces' % instance_id
        res = json.loads(self.put(url, body))
        return res['instance']['interfaces']

    def get_allocated_networks(self, instance_id):
        """
        Get VIFs and allocated ips for the instance

        ``instance_id``
            the instance identifier to retrieve data for
            Required.
        """
        url = 'ipam/instances/%s/interfaces' % instance_id
        res = json.loads(self.get(url))
        return res['instance']['interfaces']

    def get_networks_for_tenant(self, tenant_id):
        """
        Return a list of melange ip_blocks for the tenant

        ``tenant_id``
            The tenant_id to retrieve the networks for
            Required.
        """
        url = 'ipam/tenants/%s/ip_blocks' % tenant_id
        res = json.loads(self.get(url))
        return res['ip_blocks']

    def delete_ip_block(self, tenant_id, ip_block_id):
        """
        Delete an ip_block for the tenant

        ``tenant_id``
            The tenant_id to delete the block from
            Required.

        ``ip_block_id``
            The ip_block to delete
            Required.
        """
        url = 'ipam/tenants/%s/ip_blocks/%s' % (tenant_id, ip_block_id)
        self.delete(url)

    def create_ip_block(self, tenant_id, cidr, network_id, dns1=None,
                        dns2=None, gateway=None, policy_id=None):
        """
        Create a tenant's ip block

        ``tenant_id``
            The tenant_id to create the policy for
            Required.

        ``cidr``
            IPV4 or IPV6 cidr
            Required.

        ``network_id``
            The network_id associated with this network (Quantum id)
            Requred.

        ``dns1``
            A dns server for this block

        ``dns2``
            A dns server for this block

        ``gateway``
            A gateway to use

        ``policy_id``
            The existing policy_id to associate with the ip_block
        """
        url = 'ipam/tenants/%s/ip_blocks' % tenant_id
        ip_block = {'type': 'private',
                    'cidr': cidr,
                    'network_id': network_id}

        if dns1:
            ip_block['dns1'] = dns1

        if dns2:
            ip_block['dns2'] = dns2

        if gateway:
            ip_block['gateway'] = gateway

        if policy_id:
            ip_block['policy_id'] = policy_id

        body = {'ip_block': ip_block}
        res = json.loads(self.post(url, body=body))
        return res['ip_block']

    def create_ip_policy(self, tenant_id, name, description=None):
        """
        Create an IP policy for the network.

        ``tenant_id``
            The tenant_id to create the policy for
            Required.

        ``name``
            The name of the network policy
            Required.

        ``description``
            The description for the policy
        """
        url = 'ipam/tenants/%s/policies' % tenant_id
        policy = {'name': name}
        if description:
            policy['description'] = description
        body = {'policy': policy}
        res = json.loads(self.post(url, body=body))
        return res['policy']

    def create_unusable_octet_in_policy(self, tenant_id, policy_id, octet):
        """
        Create an Unusable Octet in the tenant's policy

        ``tenant_id``
            The tenant_id to create the octect policy for.
            Required.

        ``policy_id``
            The policy to create the octect policy in.
            Required.

        ``octet``
            The octet to exclude
            Required.
        """
        url = 'ipam/tenants/%s/policies/%s/unusable_ip_octets' % (tenant_id,
                                                                  policy_id)
        body = {'ip_octet': {'octet': octet}}
        res = json.loads(self.post(url, body=body))
        return res['ip_octet']

    def get_instance_ids_by_ip_address(self, context, address):
        url = ("ipam/allocated_ip_addresses")

        response = self.get(url, params={'address': address},
                            headers=json_content_type)

        ips = json.loads(response).get('ip_addresses', [])

        # TODO (aaron.lee) melange should be storing & returning instance_uuid!
        return [ip.get('used_by_device') for ip in ips]
