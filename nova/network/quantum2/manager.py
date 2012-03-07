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

import netaddr

from nova import exception
from nova import flags
from nova import log as logging
from nova import manager
from nova.network import model
from nova.network.quantum2 import melange_connection
from nova.network.quantum import quantum_connection
from nova.openstack.common import cfg
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
        self.driver = utils.import_object(FLAGS.network_driver)

        self.q_conn = quantum_connection.QuantumClientConnection()
        self.m_conn = melange_connection.MelangeConnection()

        super(QuantumManager, self).__init__(service_name='network',
                                             *args, **kwargs)

    def init_host(self):
        pass

    #
    # NOTE(jkoelker) Here be the stub points and helper functions, matey
    #
    def _clean_up_melange(self, tenant_id, instance_id,
                          raise_exception=True):
        self.m_conn.allocate_for_instance_networks(tenant_id, instance_id,
                                                   [])
        if raise_exception:
            # TODO(jkoelker) Create a new exception
            raise exception.VirtualInterfaceCleanupException()

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

    def _get_ips_and_ids_from_vif(self, vif):
        ips = []
        network_tenant_ids = set()
        network_ids = set()

        for ip in vif['ip_addresses']:
            ips.append(ip['address'])
            network_tenant_ids.add(ip['ip_block']['tenant_id'])
            network_ids.add(ip['ip_block']['network_id'])

        return (ips, network_tenant_ids, network_ids)

    def _get_all_networks(self, tenant_id):
        q_default_tenant_id = FLAGS.quantum_default_tenant_id
        networks = self.m_conn.get_networks_for_tenant(q_default_tenant_id)
        networks.extend(self.m_conn.get_networks_for_tenant(tenant_id))
        return networks

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
        client = self.q_conn.client
        # NOTE(jkoelker) Really?
        (format, tenant) = (client.format, client.tenant)
        client.format = 'json'
        client.tenant = tenant_id

        url = '/networks/detail'
        res = client.do_request("GET", url)

        # NOTE(jkoelker) Yes, really.
        client.format = format
        client.tenant = tenant
        return res['networks']

    def _normalize_network(self, network):
        # NOTE(jkoelker) We don't want to expose out a bunch of melange
        #                details, so we prune down here
        net = {'id': network['network_id'],
               'cidr': network['cidr']}
        net['label'] = self.q_conn.get_network_name(network['tenant_id'],
                                                    network['network_id'])
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

    def _vifs_to_model(self, melange_vifs):
        nw_info = model.NetworkInfo()
        # NOTE(jkoelker) This allows us to call quantum in the loop
        #                but only once per tenant_id. Keys are tenant_id
        #                value is list of networks
        quantum_network_cache = {}

        for m_vif in melange_vifs:
            (ips,
             network_tenant_ids,
             network_ids) = self._get_ips_and_ids_from_vif(m_vif)

            if not network_tenant_ids:
                msg = _("No network tenants for VIF %s") % m_vif['id']
                raise exception.VirtualInterfaceCreateException(msg)
            if not network_ids:
                msg = _("No networks for VIF %s") % m_vif['id']
                raise exception.VirtualInterfaceCreateException(msg)

            if len(network_tenant_ids) > 1:
                msg = _("Too many network tenants for VIF %s") % m_vif['id']
                raise exception.VirtualInterfaceCreateException(msg)

            if len(network_ids) > 1:
                msg = _("Too many networks for VIF %s") % m_vif['id']
                raise exception.VirtualInterfaceCreateException(msg)

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

        networks = self._get_all_networks(tenant_id)

        if requested_networks:
            networks = [n for n in networks
                        if n['network_id'] in requested_networks]

        networks = [{'id': net['network_id'],
                     'tenant_id': net['tenant_id']} for net in networks]
        vifs = []
        try:
            vifs = self.m_conn.allocate_for_instance_networks(tenant_id,
                                                              instance_id,
                                                              networks)
        except Exception:
            LOG.exception(_('Melange allocation failed'))
            self._clean_up_melange(tenant_id, instance_id, False)

        for vif in vifs:
            pairs = []

            (ips, network_tenant_ids, network_ids) = \
                        self._get_ips_and_ids_from_vif(vif)

            if (not network_tenant_ids or not network_ids or
                len(network_tenant_ids) > 1 or len(network_ids) > 1):

                # NOTE(jkoelker) Something is screwy, cleanup and error
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
        return self._vifs_to_model(vifs)

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

        for vif in vifs:
            (_ips,
             network_tenant_ids,
             network_ids) = self._get_ips_and_ids_from_vif(vif)

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

        with utils.logging_error(_("Melange block deletetion failed")):
            self.m_conn.delete_ip_block(tenant_id, network['id'])

    def get_all_networks(self, context):
        tenant_id = context.project_id
        networks = self.m_conn.get_networks_for_tenant(tenant_id)
        return [self._normalize_network(network) for network in networks]

    # NOTE(jkoelker) Accept **kwargs, for the bass ackwards compat. Dont use
    #                them.
    @uuidize
    def get_instance_nw_info(self, context, instance_id, **kwargs):
        vifs = self.m_conn.get_allocated_networks(instance_id)
        return self._vifs_to_model(vifs)

    def get_network(self, context, network_uuid):
        # NOTE(jkoelker) The param uuid is the network_id needs to be fixed
        #                in the api.
        network_id = network_uuid
        tenant_id = context.project_id

        network = self._get_network(network_id, tenant_id)
        return self._normalize_network(network)

    # NOTE(jkoelker) Stub function. validate_networks is only called
    #                in the compute api prior to creating the instance
    #                passing here since it would perform the same checks
    #                as in allocate_for_instance. In the effort of not
    #                making extraneous calls, we're just letting the
    #                allocate_for_instance fail there.
    def validate_networks(self, context, networks):
        pass
