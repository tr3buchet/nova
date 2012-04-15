# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Openstack LLC.
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

import functools
import re

import netaddr

from nova import db
from nova import exception
from nova import flags
from nova import manager
from nova.network import manager as network_manager
from nova.network import model
from nova.network.quantum import quantum_connection
from nova.network.quantum2 import melange_connection
from nova.openstack.common import cfg
from nova.openstack.common import excutils
from nova.openstack.common import importutils
from nova.openstack.common import log as logging
from nova import utils

LOG = logging.getLogger(__name__)

quantum_opts = [
    cfg.BoolOpt('quantum_use_port_security',
                default=False,
                help='Whether or not to enable port security'),
    cfg.BoolOpt('quantum_port_security_include_link_local',
                default=False,
                help='Add the link local address to the port security list'),
    ]

FLAGS = flags.FLAGS
try:
    FLAGS.register_opts(quantum_opts)
except cfg.DuplicateOptError:
    # NOTE(jkoelker) These options are verbatim in the legacy quantum
    #                manager. This is here to make sure they are
    #                registered in the tests.
    pass

quantum2_opts = [
    cfg.ListOpt('network_order',
                default=['public', 'private', '.*'],
                help='Ordered list of network labels, using regex syntax'),
    cfg.BoolOpt('quantum_cache_tenant_networks',
                default=True,
                help='Whether or not to cache quantum tenant networks'),
    ]

FLAGS.register_opts(quantum2_opts)


def uuidize(f):
    """
    Decorator to pass in instance_uuid as instance_id
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if 'instance_id' in kwargs and 'instance_uuid' in kwargs:
            kwargs['instance_id'] = kwargs['instance_uuid']
            del kwargs['instance_uuid']
        return f(*args, **kwargs)
    return wrapper


def _log_kwargs(msg='', **kwargs):
    """
    Utility to return a message with kwarg variables appended
    """
    kwarg_msg = ' '.join([('%s: |%s|' % (str(key), kwargs[key]))
                          for key in kwargs])
    return "%s %s" % (msg, kwarg_msg)


class QuantumManager(manager.SchedulerDependentManager):
    """NetworkManager class that communicates with a Quantum service
       via a web services API to provision VM network connectivity.

       currently also communicates with melange
    """
    def __init__(self, *args, **kwargs):
        """Initialize two key libraries, the connection to a
           Quantum service, and the library for implementing IPAM.

        """
        self.driver = importutils.import_module(FLAGS.network_driver)

        self.q_conn = quantum_connection.QuantumClientConnection()
        self.m_conn = melange_connection.MelangeConnection()
        self.net_info_cache = {}

        super(QuantumManager, self).__init__(service_name='network',
                                             *args, **kwargs)

    def init_host(self):
        pass

    #
    # NOTE(jkoelker) Here be the stub points and helper functions, matey
    #
    def _clean_up_melange(self, tenant_id, instance_id,
            raise_exception=True):
        try:
            self.m_conn.allocate_for_instance_networks(tenant_id,
                    instance_id, [])
        except Exception, e:
            LOG.exception(_("Error cleaning up melange: %(e)s"),
                    locals())
            if raise_exception:
                # TODO(jkoelker) Create a new exception
                raise exception.VirtualInterfaceCleanupException(
                        reason=str(e))

    def _deallocate_port(self, tenant_id, network_id, interface_id):
        port_id = self.q_conn.get_port_by_attachment(tenant_id,
                                                     network_id,
                                                     interface_id)
        if port_id:
            self.q_conn.detach_and_delete_port(tenant_id, network_id,
                                               port_id)
        else:
            LOG.error(_("Unable to find port with attachment: %s") %
                      interface_id)

    def _generate_address_pairs(self, vif, ips):
        return [{'mac_address': vif['mac_address'],
                 'ip_address': ip} for ip in ips]

    def _verify_vif_network_info(self, vif, network_tenant_ids, network_ids,
            exc_class=None):

        if exc_class is None:
            exc_class = exception.VirtualInterfaceIntegrityException

        if not network_tenant_ids:
            msg = _("No network tenants for VIF %s") % vif['id']
            raise exc_class(reason=msg)
        if not network_ids:
            msg = _("No networks for VIF %s") % vif['id']
            raise exc_class(reason=msg)

        if len(network_tenant_ids) > 1:
            msg = _("Too many network tenants for VIF %s") % vif['id']
            raise exc_class(reason=msg)

        if len(network_ids) > 1:
            msg = _("Too many networks for VIF %s") % vif['id']
            raise exc_class(reason=msg)

    def _get_ips_and_ids_from_vif(self, vif):
        ips = []
        network_tenant_ids = set()
        network_ids = set()

        for ip in vif['ip_addresses']:
            ips.append(ip['address'])
            network_tenant_ids.add(ip['ip_block']['tenant_id'])
            network_ids.add(ip['ip_block']['network_id'])

        return (ips, network_tenant_ids, network_ids)

    def _get_network(self, network_id, tenant_id):
        networks = self.m_conn.get_networks_for_tenant(tenant_id)
        networks = [net for net in networks
                    if net['network_id'] == network_id]

        if not networks:
            raise exception.NetworkNotFound(network_id=network_id)
        elif len(networks) > 1:
            raise exception.NetworkFoundMultipleTimes(network_id=network_id)

        return networks[0]

    # FIXME(jkoelker) Quantum client needs to be updated, until Folsom
    #                is open do this here.
    def _get_quantum_tenant_nets(self, tenant_id):
        if tenant_id in self.net_info_cache:
            return self.net_info_cache[tenant_id]

        client = self.q_conn.client
        # NOTE(jkoelker) Really?
        (format, tenant) = (client.format, client.tenant)
        client.format = 'json'
        client.tenant = tenant_id

        url = '/networks'
        res = client.do_request("GET", url)

        nets = res['networks']
        result = []
        for net in nets:
            net_id = net['id']
            url = '/networks/%s' % net_id
            res = client.do_request('GET', url)
            result.append({'id': net_id, 'name': res['network']['name']})

        # NOTE(jkoelker) Yes, really.
        client.format = format
        client.tenant = tenant
        # FIXME(comstud): Hack to cache this.  We'll need to make this
        # better, because it'll leak memory over time as-is.  Should
        # convert this to use nova/common/memorycache.py?
        if FLAGS.quantum_cache_tenant_networks:
            self.net_info_cache[tenant_id] = result
        return result

    def _normalize_network(self, network):
        # NOTE(jkoelker) We don't want to expose out a bunch of melange
        #                details, so we prune down here
        net = {'id': network['network_id'],
               'cidr': network['cidr']}
        try:
            label = self.q_conn.get_network_name(network['tenant_id'],
                                                 network['network_id'])
            net['label'] = label
        except Exception:
            net['label'] = 'UKNOWN'

        return net

    def _vif_from_network(self, m_vif, network_id, label):
        network = model.Network(id=network_id, label=label)
        for ip_address in m_vif['ip_addresses']:
            ip_block = ip_address['ip_block']

            gateway = None
            if ip_block.get('gateway'):
                gateway = model.IP(address=ip_block['gateway'],
                                   type='gateway')

            subnet = model.Subnet(cidr=ip_block['cidr'],
                                  gateway=gateway)

            for key in ('dns1', 'dns2'):
                if ip_block.get(key):
                    subnet.add_dns(model.IP(address=ip_block[key],
                                            type='dns'))

            for route in ip_block['ip_routes']:
                route_cidr = netaddr.IPNetwork('%s/%s' %
                                               (route['destination'],
                                                route['netmask'])).cidr
                gateway = model.IP(address=route['gateway'],
                                   type='gateway')
                subnet.add_route(model.Route(cidr=str(route_cidr),
                                             gateway=gateway))

            subnet.add_ip(model.FixedIP(address=ip_address['address']))
            network.add_subnet(subnet)
        return model.VIF(id=m_vif['id'], address=m_vif['mac_address'],
                         network=network)

    def _net_from_quantum(self, network_tenant_id, quantum_network_cache):
        q_nets = quantum_network_cache.get(network_tenant_id)
        if not q_nets:
            q_nets = self._get_quantum_tenant_nets(network_tenant_id)
            quantum_network_cache[network_tenant_id] = q_nets
        return q_nets

    def _vifs_to_model(self, melange_vifs, skip_broken_vifs=False):
        nw_info = model.NetworkInfo()
        # NOTE(jkoelker) This allows us to call quantum in the loop
        #                but only once per tenant_id. Keys are tenant_id
        #                value is list of networks
        quantum_network_cache = {}

        for m_vif in melange_vifs:
            (ips,
             network_tenant_ids,
             network_ids) = self._get_ips_and_ids_from_vif(m_vif)

            try:
                self._verify_vif_network_info(m_vif, network_tenant_ids,
                        network_ids)
            except exception.VirtualInterfaceIntegrityException:
                if skip_broken_vifs:
                    LOG.warn(_('Skipping missing/broken vif when building '
                            'model: %(m_vif)s'), locals())
                    continue
                raise

            network_tenant_id = network_tenant_ids.pop()
            network_id = network_ids.pop()

            q_nets = self._net_from_quantum(network_tenant_id,
                                            quantum_network_cache)
            labels = set([n['name'] for n in q_nets
                          if n['id'] == network_id])
            if not labels:
                labels = ['net-%s' % network_id]

            label = labels.pop()
            vif = self._vif_from_network(m_vif, network_id, label)

            nw_info.append(vif)

        return nw_info

    def _order_nw_info_by_label(self, nw_info):
        if nw_info is None:
            return nw_info

        def get_vif_label_key(vif):
            for i, pattern in enumerate(FLAGS.network_order):
                if re.match(pattern, vif['network']['label']):
                    return i
            else:
                return len(FLAGS.network_order)
        nw_info.sort(key=get_vif_label_key)
        return nw_info

    #
    # NOTE(jkoelker) Ahoy! Here be the API implementations, ya land louver
    #
    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    @uuidize
    def allocate_for_instance(self, context, instance_id, rxtx_factor,
                              project_id, host, requested_networks=None,
                              availability_zone=None, **kwargs):
        LOG.debug(_('network allocations for instance %s'), instance_id)
        tenant_id = project_id

        q_default_tenant_id = FLAGS.quantum_default_tenant_id
        networks = self.m_conn.get_networks_for_tenant(q_default_tenant_id)
        if requested_networks:
            networks.extend(self.m_conn.get_networks_for_tenant(tenant_id))
            requested_networks = [r_n[0] for r_n in requested_networks]
            networks = [n for n in networks
                        if n['network_id'] in requested_networks]

        # Make sure we only request one allocation per network
        networks = set([(net['network_id'],
                         net['tenant_id']) for net in networks])

        networks = [{'id': net[0],
                     'tenant_id': net[1]} for net in networks]
        vifs = []
        try:
            vifs = self.m_conn.allocate_for_instance_networks(tenant_id,
                                                              instance_id,
                                                              networks)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_('Melange allocation failed'))
                self._clean_up_melange(tenant_id, instance_id)

        exc_class = exception.VirtualInterfaceCreateException

        for vif in vifs:
            pairs = []

            try:
                (ips, network_tenant_ids, network_ids) = \
                        self._get_ips_and_ids_from_vif(vif)
                self._verify_vif_network_info(vif, network_tenant_ids,
                        network_ids, exc_class=exc_class)
            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.exception(_('Error extracting vif information'))
                    self._clean_up_melange(tenant_id, instance_id)

            network_tenant_id = network_tenant_ids.pop()
            network_id = network_ids.pop()

            if FLAGS.quantum_use_port_security:
                if FLAGS.quantum_port_security_include_link_local:
                    mac = netaddr.EUI(vif['mac_address'])
                    ips.append(str(mac.ipv6_link_local()))

                pairs = self._generate_address_pairs(vif, ips)

            self.q_conn.create_and_attach_port(network_tenant_id,
                                               network_id,
                                               vif['id'],
                                               vm_id=instance_id,
                                               rxtx_factor=rxtx_factor,
                                               nova_id=availability_zone,
                                               allowed_address_pairs=pairs)
        nw_info = self._vifs_to_model(vifs)
        return self._order_nw_info_by_label(nw_info)

    # NOTE(jkoelker) Only a single network is supported. Function is
    #                pluralized for da backwards compatability.
    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    def create_networks(self, context, label, cidr, gateway=None, **kwargs):
        # NOTE(jkoelker) For devstack compat we'll assume the defaul
        #                tenant if it is None in the context
        # NOTE(jkoelker) For the time being we only support 1 subnet
        #                you choose ipv6 or ipv4 but not both
        tenant_id = context.project_id or FLAGS.quantum_default_tenant_id
        nova_id = FLAGS.node_availability_zone

        network_id = self.q_conn.create_network(tenant_id, label,
                                                nova_id=nova_id)

        extra = {'network_id': network_id, 'tenant_id': tenant_id,
                 'label': label, 'nova_id': nova_id}
        msg = _('Network created in quantum')
        LOG.debug(_log_kwargs(msg, **extra), extra=extra)

        cidr = netaddr.IPNetwork(cidr)
        network_address = netaddr.IPNetwork(cidr).network
        octet = network_address.words[-1]

        policy = self.m_conn.create_ip_policy(tenant_id, network_id,
                                              ('Policy for network %s' %
                                               network_id))
        self.m_conn.create_unusable_octet_in_policy(tenant_id, policy['id'],
                                                    octet)
        ip_block = self.m_conn.create_ip_block(tenant_id, str(cidr),
                                               network_id,
                                               policy_id=policy['id'],
                                               gateway=gateway)
        # TODO(jkoelker) figure the return form
        return [self._normalize_network(ip_block)]

    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    @uuidize
    def deallocate_for_instance(self, context, instance_id, project_id,
                                **kwargs):
        tenant_id = project_id
        vifs = self.m_conn.get_allocated_networks(instance_id)
        self.m_conn.allocate_for_instance_networks(tenant_id, instance_id,
                                                   [])

        exc_class = exception.VirtualInterfaceCleanupException

        for vif in vifs:
            (_ips,
             network_tenant_ids,
             network_ids) = self._get_ips_and_ids_from_vif(vif)

            try:
                self._verify_vif_network_info(vif, network_tenant_ids,
                        network_ids, exc_class=exc_class)
            except Exception:
                # NOTE(comstud): Skip broken vifs.
                LOG.warn(_('Skipping deallocate for missing/broken vif: '
                        '%(vif)s'), locals())
                continue

            network_tenant_id = network_tenant_ids.pop()
            network_id = network_ids.pop()

            try:
                self._deallocate_port(network_tenant_id, network_id,
                                      vif['id'])
            except Exception:
                # except anything so the rest of deallocate can succeed
                extra = {'instance_id': instance_id,
                         'network_tenant_id': network_tenant_id,
                         'network_id': network_id,
                         'vif_id': vif['id']}
                msg = _('Port deallocation failed for instance.')
                LOG.critical(_log_kwargs(msg, **extra), extra=extra)

    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    def delete_network(self, context, uuid, **kwargs):
        tenant_id = context.project_id
        # NOTE(jkoelker) The param uuid is the network_id needs to be fixed
        #                in the api.
        network_id = uuid
        network = self._get_network(network_id, tenant_id)

        # Refuse to delete a network that has attached ports
        try:
            ports = self.q_conn.get_attached_ports(tenant_id, network_id)
            if len(ports) > 0:
                raise exception.NetworkBusy(network=network_id)
            self.q_conn.delete_network(tenant_id, network_id)
            LOG.debug(_('Deleting network %(network_id)s for tenant '
                        '%(tenant_id)s') % {'network_id': network_id,
                                            'tenant_id': tenant_id})
        except quantum_connection.quantum_client.QuantumNotFoundException:
            LOG.exception(_('Deleting quantum network %s failed') %
                          network_id)

        try:
            self.m_conn.delete_ip_block(tenant_id, network['id'])
        except Exception:
            LOG.exception(_("Melange block deletion failed"))
            raise

    def get_all_networks(self, context):
        tenant_id = context.project_id
        networks = self.m_conn.get_networks_for_tenant(tenant_id)
        return [self._normalize_network(network) for network in networks]

    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    @uuidize
    def get_instance_nw_info(self, context, instance_id, project_id,
                             **kwargs):

        try:
            vifs = self.m_conn.get_allocated_networks(instance_id)
            nw_info = self._vifs_to_model(vifs, skip_broken_vifs=True)
            nw_info = self._order_nw_info_by_label(nw_info)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                msg = _('Failed to get nw_info!!! for instance '
                        '|%(instance_id)s|, |%(e)s|') % locals()
                LOG.exception(msg)
        return nw_info

    def get_network(self, context, network_uuid):
        # NOTE(jkoelker) The param uuid is the network_id needs to be fixed
        #                in the api.
        network_id = network_uuid
        tenant_id = context.project_id

        network = self._get_network(network_id, tenant_id)
        return self._normalize_network(network)

    # NOTE(jkoelker) Stub function. setup_networks_on_host is for legacy
    #                dhcp and multi_host setups
    def setup_networks_on_host(self, *args, **kwargs):
        pass

    @network_manager.wrap_check_policy
    def get_instance_uuids_by_ip_filter(self, context, filters):
        # This is not returning the instance IDs like the method name would
        # make you think, its matching the return format of the method it's
        # overriding. Yahrrr
        address = filters.get('ip', None)
        instance_ids = self.m_conn.get_instance_ids_by_ip_address(
                                                            context, address)
        instances = [db.instance_get_by_uuid(context,
                                             id) for id in instance_ids]
        return [{'instance_uuid':instance.uuid} for instance in instances]

    # NOTE(jkoelker) Stub function. validate_networks is only called
    #                in the compute api prior to creating the instance
    #                passing here since it would perform the same checks
    #                as in allocate_for_instance. In the effort of not
    #                making extraneous calls, we're just letting the
    #                allocate_for_instance fail there.
    def validate_networks(self, context, networks):
        pass
