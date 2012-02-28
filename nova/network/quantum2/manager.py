# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Trey god damned Morris
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

import time

import netaddr

from nova import context
from nova import db
from nova import exception
from nova import flags
from nova import log as logging
from nova.network import manager
from nova.network.quantum import melange_ipam_lib
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
FLAGS.register_opts(quantum_opts)


class QuantumManager(manager.SchedulerDependentManager):
    """NetworkManager class that communicates with a Quantum service
       via a web services API to provision VM network connectivity.

       currently also communicates with melange
    """
    def __init__(self, *args, **kwargs):
        """Initialize two key libraries, the connection to a
           Quantum service, and the library for implementing IPAM.

        """
        if not network_driver:
            network_driver = FLAGS.network_driver
        self.driver = utils.import_object(network_driver)

        self.network_api = network_api.API()
        self.compute_api = compute_api.API()

        self.q_conn = quantum_connection.QuantumClientConnection()
        self.m_conn = melange_connection.MelangeConnection()

        super(QuantumManager, self).__init__(service_name='network',
                                             *args, **kwargs)

    def init_host(self):
        pass

    def create_networks(self, context, label, cidr, multi_host, num_networks,
                        network_size, cidr_v6, gateway, gateway_v6, bridge,
                        bridge_interface, dns1=None, dns2=None, uuid=None,
                        **kwargs):
        """Unlike other NetworkManagers, with QuantumManager, each
           create_networks calls should create only a single network.

           Two scenarios exist:
                - no 'uuid' is specified, in which case we contact
                  Quantum and create a new network.
                - an existing 'uuid' is specified, corresponding to
                  a Quantum network created out of band.

           In both cases, we initialize a subnet using the IPAM lib.
        """
        # Enforce Configuration sanity.
        #
        # These flags are passed in from bin/nova-manage. The script
        # collects the arguments and then passes them in through this
        # function call. Note that in some cases, the script pre-processes
        # the arguments, and sets them to a default value if a parameter's
        # value was not specified on the command line. For pre-processed
        # parameters, the most effective check to see if the user passed it
        # in is to see if is different from the default value. (This
        # does miss the use case where the user passes in the default value
        # on the command line -- but it is unavoidable.)
        if gateway is not None and len(gateway) > 0:
            if gateway.split('.')[3] != '1':
                raise Exception(_("QuantumManager requires a valid (.1)"
                              " gateway address."))

        q_tenant_id = kwargs["project_id"] or FLAGS.quantum_default_tenant_id
        quantum_net_id = uuid
        # If a uuid was specified with the network it should have already been
        # created in Quantum, so make sure.
        if quantum_net_id:
            if not self.q_conn.network_exists(q_tenant_id, quantum_net_id):
                    raise Exception(_("Unable to find existing quantum "
                                      "network for tenant '%(q_tenant_id)s' "
                                      "with net-id '%(quantum_net_id)s'" %
                                      locals()))
        else:
            nova_id = self._get_nova_id()
            quantum_net_id = self.q_conn.create_network(q_tenant_id, label,
                                                        nova_id=nova_id)

        ipam_tenant_id = kwargs.get("project_id", None)
        priority = kwargs.get("priority", 0)
        # NOTE(tr3buchet): this call creates a nova network in the nova db
        self.ipam.create_subnet(context, label, ipam_tenant_id, quantum_net_id,
            priority, cidr, gateway, gateway_v6,
            cidr_v6, dns1, dns2)

        # Initialize forwarding
        self.l3driver.initialize_network(cidr)

        return [{'uuid': quantum_net_id}]

    def delete_network(self, context, fixed_range, uuid):
        """Lookup network by uuid, delete both the IPAM
           subnet and the corresponding Quantum network.

           The fixed_range parameter is kept here for interface compatibility
           but is not used.
        """
        net_ref = db.network_get_by_uuid(context.elevated(), uuid)
        project_id = net_ref['project_id']
        q_tenant_id = project_id or FLAGS.quantum_default_tenant_id
        net_uuid = net_ref['uuid']

        # Check for any attached ports on the network and fail the deletion if
        # there is anything but the gateway port attached.  If it is only the
        # gateway port, unattach and delete it.
        ports = self.q_conn.get_attached_ports(q_tenant_id, net_uuid)
        num_ports = len(ports)
        gw_interface_id = self.driver.get_dev(net_ref)
        gw_port_uuid = None
        if gw_interface_id is not None:
            gw_port_uuid = self.q_conn.get_port_by_attachment(q_tenant_id,
                                        net_uuid, gw_interface_id)

        if gw_port_uuid:
            num_ports -= 1

        if num_ports > 0:
            raise Exception(_("Network %s has active ports, cannot delete"
                                                            % (net_uuid)))

        # only delete gw ports if we are going to finish deleting network
        if gw_port_uuid:
            self.q_conn.detach_and_delete_port(q_tenant_id,
                                                   net_uuid,
                                                   gw_port_uuid)

        # Now we can delete the network
        self.q_conn.delete_network(q_tenant_id, net_uuid)
        LOG.debug("Deleting network %s for tenant: %s" % \
                                    (net_uuid, q_tenant_id))
        self.ipam.delete_subnets_by_net_id(context, net_uuid, project_id)
        # Get rid of dnsmasq
        if FLAGS.quantum_use_dhcp:
            dev = self.driver.get_dev(net_ref)
            if self.driver._device_exists(dev):
                self.driver.kill_dhcp(dev)

    def allocate_for_instance(self, context, instance_id, **kwargs):
        """Called by compute when it is creating a new VM.

           There are three key tasks:
                - Determine the number and order of vNICs to create
                - Allocate IP addresses
                - Create ports on a Quantum network and attach vNICs.

           We support two approaches to determining vNICs:
                - By default, a VM gets a vNIC for any network belonging
                  to the VM's project, and a vNIC for any "global" network
                  that has a NULL project_id.  vNIC order is determined
                  by the network's 'priority' field.
                - If the 'os-create-server-ext' was used to create the VM,
                  only the networks in 'requested_networks' are used to
                  create vNICs, and the vNIC order is determiend by the
                  order in the requested_networks array.

           For each vNIC, use the FlatManager to create the entries
           in the virtual_interfaces table, contact Quantum to
           create a port and attachment the vNIC, and use the IPAM
           lib to allocate IP addresses.
        """
        instance_id = kwargs['instance_uuid']
        rxtx_factor = kwargs['rxtx_factor']
        host = kwargs['host']
        project_id = kwargs['project_id']
        LOG.debug(_("network allocations for instance %s"), project_id)
        requested_networks = kwargs.get('requested_networks')
        instance = db.instance_get(context, instance_id)

        net_proj_pairs = self.ipam.get_project_and_global_net_ids(context,
                                                                project_id)
        if requested_networks:
            # need to figure out if a requested network is owned
            # by the tenant, or by the provider
            # Note: these are the only possible options, as the compute
            # API already validated networks using validate_network()
            proj_net_ids = set([p[0] for p in net_proj_pairs if p[1]])
            net_proj_pairs = []
            for net_id, _i in requested_networks:
                if net_id in proj_net_ids:
                    net_proj_pairs.append((net_id, project_id))
                else:
                    net_proj_pairs.append((net_id, None))

        # Create a port via quantum and attach the vif
        for proj_pair in net_proj_pairs:
            network = self.get_network(context, proj_pair)

            # TODO(tr3buchet): broken. Virtual interfaces require an integer
            #                  network ID and it is not nullable
            vif_rec = self.add_virtual_interface(context,
                                                 instance_id,
                                                 network['id'],
                                                 project_id)

            # talk to Quantum API to create and attach port.
            nova_id = self._get_nova_id(instance)
            # Tell the ipam library to allocate an IP
            ips = self.ipam.allocate_fixed_ips(context, project_id,
                    network['quantum_net_id'], network['net_tenant_id'],
                    vif_rec)
            pairs = []
            # Set up port security if enabled
            if FLAGS.quantum_use_port_security:
                if FLAGS.quantum_port_security_include_link_local:
                    mac = netaddr.EUI(vif_rec['address'])
                    ips.append(str(mac.ipv6_link_local()))

                pairs = [{'mac_address': vif_rec['address'],
                          'ip_address': ip} for ip in ips]

            self.q_conn.create_and_attach_port(network['net_tenant_id'],
                                               network['quantum_net_id'],
                                               vif_rec['uuid'],
                                               vm_id=instance['uuid'],
                                               rxtx_factor=rxtx_factor,
                                               nova_id=nova_id,
                                               allowed_address_pairs=pairs)
            # Set up/start the dhcp server for this network if necessary
            if FLAGS.quantum_use_dhcp:
                self.enable_dhcp(context, network['quantum_net_id'], network,
                    vif_rec, network['net_tenant_id'])
        return self.get_instance_nw_info(context, instance_id,
                                         instance['uuid'],
                                         rxtx_factor, host,
                                         project_id=project_id)

    def get_all_networks(self, context):
        networks = []
        networks.extend(self.ipam.get_global_networks(context))
        networks.extend(self.ipam.get_project_networks(context))
        return networks

    def get_network(self, context, proj_pair):
        (quantum_net_id, net_tenant_id) = proj_pair

        net_tenant_id = net_tenant_id or FLAGS.quantum_default_tenant_id
        # FIXME(danwent): We'd like to have the manager be
        # completely decoupled from the nova networks table.
        # However, other parts of nova sometimes go behind our
        # back and access network data directly from the DB.  So
        # for now, the quantum manager knows that there is a nova
        # networks DB table and accesses it here.  updating the
        # virtual_interfaces table to use UUIDs would be one
        # solution, but this would require significant work
        # elsewhere.
        admin_context = context.elevated()

        # We may not be able to get a network_ref here if this network
        # isn't in the database (i.e. it came from Quantum).
        network_ref = db.network_get_by_uuid(admin_context,
                                             quantum_net_id)

        if network_ref is None:
            network_ref = {}
            network_ref = {"uuid": quantum_net_id,
                           "project_id": net_tenant_id,
            # NOTE(bgh): We need to document this somewhere but since
            # we don't know the priority of any networks we get from
            # quantum we just give them a priority of 0.  If its
            # necessary to specify the order of the vifs and what
            # network they map to then the user will have to use the
            # OSCreateServer extension and specify them explicitly.
            #
            # In the future users will be able to tag quantum networks
            # with a priority .. and at that point we can update the
            # code here to reflect that.
                           "priority": 0,
                           "id": 'NULL',
                           "label": "quantum-net-%s" % quantum_net_id}
        network_ref['net_tenant_id'] = net_tenant_id
        network_ref['quantum_net_id'] = quantum_net_id
        return network_ref

    def add_virtual_interface(self, context, instance_id, network_id,
                              net_tenant_id):
        # If we're not using melange, use the default means...
        if FLAGS.use_melange_mac_generation:
            return self._add_virtual_interface(context, instance_id,
                                               network_id, net_tenant_id)

        return super(QuantumManager, self).add_virtual_interface(context,
                                                                 instance_id,
                                                                 network_id)

    def _add_virtual_interface(self, context, instance_id, network_id,
                               net_tenant_id):
        vif = {'instance_id': instance_id,
               'network_id': network_id,
               'uuid': str(utils.gen_uuid())}

        # TODO(Vek): Ideally, we would have a VirtualInterface class
        #            that would take care of delegating to whoever it
        #            needs to get information from.  We'll look at
        #            this after Trey's refactorings...
        m_ipam = melange_ipam_lib.get_ipam_lib(self)
        vif['address'] = m_ipam.create_vif(vif['uuid'],
                                           vif['instance_id'],
                                           net_tenant_id)

        return self.db.virtual_interface_create(context, vif)

    def get_instance_nw_info(self, context, instance_id, instance_uuid,
                                            rxtx_factor, host, **kwargs):
        """This method is used by compute to fetch all network data
           that should be used when creating the VM.

           The method simply loops through all virtual interfaces
           stored in the nova DB and queries the IPAM lib to get
           the associated IP data.

           The format of returned data is 'defined' by the initial
           set of NetworkManagers found in nova/network/manager.py .
           Ideally this 'interface' will be more formally defined
           in the future.
        """
        project_id = kwargs['project_id']
        vifs = db.virtual_interface_get_by_instance(context, instance_id)

        net_tenant_dict = dict((net_id, tenant_id)
                               for (net_id, tenant_id)
                               in self.ipam.get_project_and_global_net_ids(
                                                          context, project_id))
        networks = {}
        for vif in vifs:
            if vif.get('network_id') is not None:
                network = db.network_get(context.elevated(), vif['network_id'])
                net_tenant_id = net_tenant_dict[network['uuid']]
                if net_tenant_id is None:
                    net_tenant_id = FLAGS.quantum_default_tenant_id
                network = {'id': network['id'],
                           'uuid': network['uuid'],
                           'bridge': '',  # Quantum ignores this field
                           'label': network['label'],
                           'injected': FLAGS.flat_injected,
                           'project_id': net_tenant_id}
                networks[vif['uuid']] = network

        # update instance network cache and return network_info
        nw_info = self.build_network_info_model(context, vifs, networks,
                                                rxtx_factor, host)
        db.instance_info_cache_update(context, instance_uuid,
                                      {'network_info': nw_info.as_cache()})

        return nw_info

    def deallocate_for_instance(self, context, **kwargs):
        """Called when a VM is terminated.  Loop through each virtual
           interface in the Nova DB and remove the Quantum port and
           clear the IP allocation using the IPAM.  Finally, remove
           the virtual interfaces from the Nova DB.
        """
        instance_id = kwargs.get('instance_id')
        project_id = kwargs.pop('project_id', None)

        admin_context = context.elevated()
        vifs = db.virtual_interface_get_by_instance(admin_context,
                                                    instance_id)

        for vif in vifs:
            network = db.network_get(admin_context, vif['network_id'])

            self.deallocate_port(vif['uuid'], network['uuid'], project_id,
                                 instance_id)

            ipam_tenant_id = self.deallocate_ip_address(context,
                                network['uuid'], project_id, vif, instance_id)

            if FLAGS.quantum_use_dhcp:
                self.update_dhcp(context, ipam_tenant_id, network,
                                 vif, project_id)

            db.virtual_interface_delete(admin_context, vif['id'])

    def deallocate_port(self, interface_id, net_id, q_tenant_id, instance_id):
        try:
            port_id = self.q_conn.get_port_by_attachment(q_tenant_id,
                                                         net_id, interface_id)
            if not port_id:
                q_tenant_id = FLAGS.quantum_default_tenant_id
                port_id = self.q_conn.get_port_by_attachment(
                    q_tenant_id, net_id, interface_id)

            if not port_id:
                LOG.error("Unable to find port with attachment: %s" %
                          (interface_id))
            else:
                self.q_conn.detach_and_delete_port(q_tenant_id,
                                                   net_id, port_id)
        except Exception:
            # except anything so the rest of deallocate can succeed

            msg = _('port deallocation failed for instance: '
                    '|%(instance_id)s|, port_id: |%(port_id)s|')
            LOG.critical(msg % locals())

    def deallocate_ip_address(self, context, net_id,
                              project_id, vif_ref, instance_id):
        ipam_tenant_id = None
        try:
            ipam_tenant_id = self.ipam.get_tenant_id_by_net_id(context,
                                                               net_id,
                                                               vif_ref['uuid'],
                                                               project_id)

            self.ipam.deallocate_ips_by_vif(context, ipam_tenant_id,
                                            net_id, vif_ref)

        except Exception:
            # except anything so the rest of deallocate can succeed
            vif_uuid = vif_ref['uuid']
            msg = _('ipam deallocation failed for instance: '
                    '|%(instance_id)s|, vif_uuid: |%(vif_uuid)s|')
            LOG.critical(msg % locals())
        return ipam_tenant_id
