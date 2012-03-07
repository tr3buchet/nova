# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC.
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

import contextlib
import mock

from nova import context
from nova import db
from nova import exception
from nova import flags
from nova.network.quantum import quantum_connection
from nova.network.quantum2 import manager
from nova.network.quantum2 import melange_connection
from nova import test
import nova.utils

FLAGS = flags.FLAGS


def dummy(*args, **kwargs):
    pass


def dummy_list(*args, **kwargs):
    return []


def dummy_raise(*args, **kwargs):
    raise test.TestingException('Boom!')


def _ip_addresses_helper(ips_per_vif):
    return ['10.0.0.1%02d' % i for i in xrange(ips_per_vif)]


def _vif_helper(tenant_id, network_uuid, mac_offset=0, vif_id=None):
    return {'id': vif_id or str(nova.utils.gen_uuid()),
            'mac_address': '00:00:00:00:00:%02d' % mac_offset,
            'ip_addresses': [
                    {'address': _ip_addresses_helper(1)[0],
                    'ip_block': {'tenant_id': tenant_id,
                                 'network_id': network_uuid,
                                 'gateway': '10.0.0.1',
                                 'cidr': '10.0.0.0/8',
                                 'dns1': '8.8.8.8',
                                 'dns2': '8.8.4.4',
                                 'ip_routes': [
                                   {'destination': '1.1.1.1',
                                    'gateway': '2.2.2.2',
                                    'netmask': '255.0.0.0'}]}}]}


def _ips_from_vif_stub(ips_per_vif, tenants, networks):
    def ips(vif):
        ip_addresses = _ip_addresses_helper(ips_per_vif)
        nets = [n['network_id'] for n in networks]
        return ip_addresses, tenants, nets
    return ips


def _fake_networks(network_count, tenant_id):
    """id is the id from melange
    network_id is the id from quantum. Dumb"""
    return [{'id': str(nova.utils.gen_uuid()),
             'name': 'net%d' % i,
             'network_id': str(nova.utils.gen_uuid()),
             'tenant_id': tenant_id} for i in xrange(network_count)]


def _get_allocated_networks_stub(vifs, bare_uuids=True):

    def allocated_nets(self, instance_id):
        if bare_uuids:
            return [{'id': vif} for vif in vifs]
        return vifs

    return allocated_nets


def _allocate_for_instance_networks_stub(networks):
    vif_ids = [str(nova.utils.gen_uuid()) for i in xrange(len(networks))]

    def allocate(self, tenant_id, instance_id, nets):
        # explicitly ignoring including IPs, as we're going to
        # stub out the helper method that iterates over VIFs looking
        # for them.
        return [_vif_helper(tenant_id, networks[i],
                            mac_offset=i, vif_id=vif_ids[i])
                    for i in xrange(len(networks))]
    return vif_ids, allocate


def _create_network_stub(network_uuid):

    def net_create(self, tenant_id, label, nova_id=None):
        return network_uuid
    return net_create


def _get_networks_for_tenant_stub(networks):

    def nets_for_tenant(self, tenant_id):
        return networks
    return nets_for_tenant


def _get_attached_ports_stub(ports):

    def get_ports(self, tenant_id, network_id):
        return ports
    return get_ports


def _create_ip_policy_stub():
    def policy(self, tenant_id, network_id, label):
        return dict(id=1)

    return policy


def _get_port_by_attachment_stub(port):
    def get_port(self, tenant_id, instance_id, interface_id):
        return port
    return get_port


def _quantum_client_stub(networks_dict):
    class Client(object):
        def __init__(self, *args, **kwargs):
            self.tenant = None
            self.format = None

        def do_request(self, method, url):
            return networks_dict
    return Client


def _get_quantum_tenant_nets_stub(networks):
    def get_nets(tenant):
        return networks[tenant]
    return get_nets


def _net_from_quantum_stub(network):
    def get_net(net_tenant, quantum_cache):
        return network
    return get_net


def _normalize_network_stub(label):
    def normalize(net):
        net['label'] = label
        return net
    return normalize


def _create_ip_block_stub(block):
    def ip(*args, **kwargs):
        return block
    return ip


class QuantumPrimeManagerInterfaceTests(test.TestCase):
    """This test suite merely checks that the methods are callable"""
    def setUp(self):
        super(QuantumPrimeManagerInterfaceTests, self).setUp()

        self.stubs.Set(manager.QuantumManager, '_clean_up_melange', dummy)
        self.context = context.RequestContext(user_id=1, project_id=1)
        self.net_manager = manager.QuantumManager()

    def test_allocate_for_instance(self):
        self.stubs.Set(quantum_connection.QuantumClientConnection,
                  'create_and_attach_port', dummy)
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks', dummy_list)
        self.stubs.Set(melange_connection.MelangeConnection,
                  'get_networks_for_tenant', dummy_list)
        self.net_manager.allocate_for_instance(self.context, instance_id=1,
                                               rxtx_factor=1,
                                               project_id='project1',
                                               host='host')

    def test_deallocate_for_instance(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                  'get_allocated_networks', dummy_list)
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks', dummy_list)
        self.net_manager.deallocate_for_instance(self.context,
                                                 instance_id=1,
                                                 project_id='project1')

    def test_get_all_networks(self):
        self.stubs.Set(melange_connection.MelangeConnection,
              'get_networks_for_tenant', dummy_list)
        self.net_manager.get_all_networks(self.context)

    def test_init_host(self):
        self.net_manager.init_host()


class Quantum2ManagerTestsAllocateForInstance(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerTestsAllocateForInstance, self).setUp()
        self.tenant_id = 'project1'
        self.context = context.RequestContext(user_id=1,
                                              project_id=self.tenant_id)
        self.net_manager = manager.QuantumManager()
        self.q_client = ('nova.network.quantum.quantum_connection.'
                         'QuantumClientConnection')
        self.m_client = ('nova.network.quantum2.melange_connection.'
                         'MelangeConnection')

    def test_allocate_for_instance_with_vifs(self):
        with contextlib.nested(
            mock.patch.object(self.net_manager, '_vifs_to_model'),
            mock.patch(self.q_client + '.create_and_attach_port'),
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            ) as (vifs_to_model,
                  create_and_attach,
                  get_networks_for_tenant,
                  allocate_for_instance_networks):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.net_manager.allocate_for_instance(self.context,
                                                   instance_id=1,
                                                   rxtx_factor=1,
                                                   project_id='project1',
                                                   host='host')
            self.assertTrue(create_and_attach.called)

    def test_allocate_for_instance_with_requested_nets(self):
        with contextlib.nested(
            mock.patch.object(self.net_manager, '_vifs_to_model'),
            mock.patch(self.q_client + '.create_and_attach_port'),
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            ) as (vifs_to_model,
                  create_and_attach,
                  get_networks_for_tenant,
                  allocate_for_instance_networks):

            # only take the first network for the test
            networks = _fake_networks(2, self.tenant_id)
            expected_networks = networks[:1]
            requested_networks = [n['network_id']
                                  for n in expected_networks]

            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in expected_networks]
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            instance_id = 1
            kwargs = dict(instance_id=instance_id,
                          rxtx_factor=1,
                          project_id='project1',
                          requested_networks=[requested_networks],
                          host='host')

            self.net_manager.allocate_for_instance(self.context, **kwargs)

            args = (self.tenant_id,
                    instance_id,
                    [{'id': n['network_id'], 'tenant_id': n['tenant_id']}
                      for n in expected_networks])
            allocate_for_instance_networks.assert_called_once_with(*args)
            self.assertTrue(create_and_attach.called)

    def test_allocate_for_instance_no_vifs_raises(self):
        with contextlib.nested(
            mock.patch(self.q_client + '.create_and_attach_port'),
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            ) as (create_and_attach,
                  get_networks_for_tenant,
                  allocate_for_instance_networks):

            networks = _fake_networks(1, self.tenant_id)
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = []

            self.net_manager.allocate_for_instance(self.context,
                                                   instance_id=1,
                                                   rxtx_factor=1,
                                                   project_id='project1',
                                                   host='host')
            self.assertEqual(create_and_attach.called, False)

    def test_allocate_for_instance_melange_allocation_fails(self):
        with contextlib.nested(
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            ) as (get_networks_for_tenant,
                  allocate_for_instance_networks):

            get_networks_for_tenant.return_value = []

            def side_effect(*args):
                def clean_up_call(*args, **kwargs):
                    return
                allocate_for_instance_networks.side_effect = clean_up_call
                raise test.TestingException()

            allocate_for_instance_networks.side_effect = side_effect

            self.assertRaises(test.TestingException,
                              self.net_manager.allocate_for_instance,
                              self.context, instance_id=1,
                              rxtx_factor=1,
                              project_id='project1',
                              host='host')

    def test_allocate_for_instance_too_many_net_tenant_ids_fails(self):
        with contextlib.nested(
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            mock.patch.object(self.net_manager, '_get_ips_and_ids_from_vif'),
            ) as (get_networks_for_tenant,
                  allocate_for_instance_networks,
                  _get_ips_and_ids_from_vif):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            ips = _ips_from_vif_stub(ips_per_vif=2,
                                     tenants=['project1', 'project2'],
                                     networks=networks)

            _get_ips_and_ids_from_vif.side_effect = ips
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.assertRaises(exception.VirtualInterfaceCreateException,
                              self.net_manager.allocate_for_instance,
                              self.context,
                              instance_id=1,
                              rxtx_factor=1,
                              project_id='project1',
                              host='host')

    def test_allocate_for_instance_too_many_net_ids_fails(self):
        with contextlib.nested(
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            mock.patch.object(self.net_manager, '_get_ips_and_ids_from_vif'),
            ) as (get_networks_for_tenant,
                  allocate_for_instance_networks,
                  _get_ips_and_ids_from_vif):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            ips = _ips_from_vif_stub(ips_per_vif=2,
                                     tenants=['project1'],
                                     networks=networks * 2)

            _get_ips_and_ids_from_vif.side_effect = ips
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.assertRaises(exception.VirtualInterfaceCreateException,
                              self.net_manager.allocate_for_instance,
                              self.context,
                              instance_id=1,
                              rxtx_factor=1,
                              project_id='project1',
                              host='host')

    def test_allocate_for_instance_no_net_tenant_ids_fails(self):
        with contextlib.nested(
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            mock.patch.object(self.net_manager, '_get_ips_and_ids_from_vif'),
            ) as (get_networks_for_tenant,
                  allocate_for_instance_networks,
                  _get_ips_and_ids_from_vif):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            ips = _ips_from_vif_stub(ips_per_vif=2,
                                     tenants=[],
                                     networks=networks)

            _get_ips_and_ids_from_vif.side_effect = ips
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.assertRaises(exception.VirtualInterfaceCreateException,
                              self.net_manager.allocate_for_instance,
                              self.context,
                              instance_id=1,
                              rxtx_factor=1,
                              project_id='project1',
                              host='host')

    def test_allocate_for_instance_no_net_ids_fails(self):
        with contextlib.nested(
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            mock.patch.object(self.net_manager, '_get_ips_and_ids_from_vif'),
            ) as (get_networks_for_tenant,
                  allocate_for_instance_networks,
                  _get_ips_and_ids_from_vif):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            ips = _ips_from_vif_stub(ips_per_vif=2,
                                     tenants=['project1'],
                                     networks=[])

            _get_ips_and_ids_from_vif.side_effect = ips
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.assertRaises(exception.VirtualInterfaceCreateException,
                              self.net_manager.allocate_for_instance,
                              self.context,
                              instance_id=1,
                              rxtx_factor=1,
                              project_id='project1',
                              host='host')

    def test_allocate_for_instance_with_port_security(self):
        FLAGS.quantum_use_port_security = True
        with contextlib.nested(
            mock.patch.object(self.net_manager, '_vifs_to_model'),
            mock.patch.object(self.net_manager, '_generate_address_pairs'),
            mock.patch(self.q_client + '.create_and_attach_port'),
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            ) as (vifs_to_model,
                  gen_pairs,
                  create_and_attach,
                  get_networks_for_tenant,
                  allocate_for_instance_networks):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.net_manager.allocate_for_instance(self.context,
                                                   instance_id=1,
                                                   rxtx_factor=1,
                                                   project_id='project1',
                                                   host='host')
            create_and_attach.assert_called()
            gen_pairs.assert_called()

    def test_allocate_for_instance_with_port_security_link_local(self):
        FLAGS.quantum_use_port_security = True
        with contextlib.nested(
            mock.patch.object(self.net_manager, '_vifs_to_model'),
            mock.patch.object(self.net_manager, '_generate_address_pairs'),
            mock.patch(self.q_client + '.create_and_attach_port'),
            mock.patch(self.m_client + '.get_networks_for_tenant'),
            mock.patch(self.m_client + '.allocate_for_instance_networks'),
            mock.patch('netaddr.EUI'),
            ) as (vifs_to_model,
                  gen_pairs,
                  create_and_attach,
                  get_networks_for_tenant,
                  allocate_for_instance_networks,
                  eui):

            networks = _fake_networks(1, self.tenant_id)
            vifs = [_vif_helper(self.tenant_id, n['network_id'])
                    for n in networks]
            get_networks_for_tenant.return_value = networks
            allocate_for_instance_networks.return_value = vifs

            self.net_manager.allocate_for_instance(self.context,
                                                   instance_id=1,
                                                   rxtx_factor=1,
                                                   project_id='project1',
                                                   host='host')
            create_and_attach.assert_called()
            gen_pairs.assert_called()


class Quantum2ManagerDeallocateForInstance(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerDeallocateForInstance, self).setUp()
        self.tenant_id = 'project1'
        self.context = context.RequestContext(user_id=1,
                                              project_id=self.tenant_id)
        self.net_manager = manager.QuantumManager()

        stub = self.stubs.Set
        networks = _fake_networks(1, self.tenant_id)

        self.vifs, allocate_stub = _allocate_for_instance_networks_stub(
                                                networks=networks)
        stub(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks',
                  allocate_stub)

        stub(self.net_manager, '_get_ips_and_ids_from_vif',
                       _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=['project1'],
                                          networks=networks))

        self.stubs.Set(melange_connection.MelangeConnection,
                       'get_allocated_networks',
                       _get_allocated_networks_stub(self.vifs))

    def test_deallocate_instance_no_vifs(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                       'get_allocated_networks',
                       _get_allocated_networks_stub([]))
        with mock.patch('nova.network.quantum2.manager.QuantumManager'
                        '._deallocate_port') as patch:
            self.net_manager.deallocate_for_instance(context=self.context,
                                                     instance_id=1,
                                                     project_id=self.tenant_id)
            self.assertEqual(patch.called, False)

    def test_deallocate_for_instance(self):
        with mock.patch('nova.network.quantum2.manager.QuantumManager'
                        '._deallocate_port') as patch:
            self.net_manager.deallocate_for_instance(context=self.context,
                                                     instance_id=1,
                                                     project_id=self.tenant_id)
            self.assertEqual(patch.called, True)

    def test_deallocate_instance_deallocate_port_fails(self):
        """
        It's hard to assert this test is proving anything. We have to assume
        the raise just happens
        """
        with mock.patch('nova.network.quantum2.manager.QuantumManager'
                       '._deallocate_port',
                       mock.MagicMock(side_effect=Exception('Boom'))):
            self.net_manager.deallocate_for_instance(self.context, 1,
                                                     self.tenant_id)


class Quantum2ManagerCreateNetworks(test.TestCase):
    def test_create_networks(self):
        net_manager = manager.QuantumManager()
        network_uuid = nova.utils.gen_uuid()
        stub = self.stubs.Set
        ctxt = context.RequestContext(user_id=1,
                                              project_id='project1')
        stub(net_manager, '_normalize_network',
             _normalize_network_stub('label'))
        stub(melange_connection.MelangeConnection,
             'create_unusable_octet_in_policy', dummy)
        stub(quantum_connection.QuantumClientConnection,
             'create_network', _create_network_stub(network_uuid))
        stub(melange_connection.MelangeConnection,
             'create_ip_policy', _create_ip_policy_stub())
        stub(melange_connection.MelangeConnection,
             'create_unusable_octet_in_policy', dummy)
        stub(melange_connection.MelangeConnection,
                       'create_ip_block', _create_ip_block_stub({}))

        ret = net_manager.create_networks(ctxt, label='label',
                                          cidr='10.0.0.0/24')
        self.assertEqual(ret, [{'label': 'label'}])


class Quantum2ManagerDeleteNetwork(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerDeleteNetwork, self).setUp()
        self.tenant_id = 'project1'
        self.context = context.RequestContext(user_id=1,
                                              project_id=self.tenant_id)
        self.net_manager = manager.QuantumManager()
        self.networks = _fake_networks(network_count=2,
                                       tenant_id=self.tenant_id)
        stub = self.stubs.Set

        stub(melange_connection.MelangeConnection, 'get_networks_for_tenant',
             _get_networks_for_tenant_stub(self.networks))
        stub(quantum_connection.QuantumClientConnection, 'get_attached_ports',
             _get_attached_ports_stub([]))
        stub(quantum_connection.QuantumClientConnection, 'delete_network',
             dummy)

    def test_delete_network_no_networks_raises(self):
        self.assertRaises(exception.NetworkNotFound,
                          self.net_manager.delete_network,
                          context=self.context,
                          uuid='wharrgarbl')

    def test_delete_network_too_many_networks_raises(self):
        network_uuid = self.networks[0]['network_id']
        # Make the ids the same, so we find two of the same net
        self.networks[1]['network_id'] = network_uuid
        self.assertRaises(exception.NetworkFoundMultipleTimes,
                          self.net_manager.delete_network,
                          context=self.context,
                          uuid=network_uuid)

    def test_delete_network_active_ports_raises(self):
        self.stubs.Set(quantum_connection.QuantumClientConnection,
                       'get_attached_ports',
                       _get_attached_ports_stub(['port']))
        network_uuid = self.networks[0]['network_id']
        self.assertRaises(exception.NetworkBusy,
                          self.net_manager.delete_network,
                          context=self.context,
                          uuid=network_uuid)

    def test_delete_networK(self):
        network_uuid = self.networks[0]['network_id']
        with mock.patch('nova.network.quantum2.melange_connection.'
                        'MelangeConnection.delete_ip_block') as patch:
            self.net_manager.delete_network(context=self.context,
                                            uuid=network_uuid)
            patch.assert_called()


class Quantum2ManagerGetAllNetworks(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerGetAllNetworks, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        stub = self.stubs.Set
        self.context = context.RequestContext(user_id=1,
                                      project_id='project1')
        self.networks = _fake_networks(network_count=2,
                                       tenant_id=self.tenant_id)
        stub(melange_connection.MelangeConnection, 'get_networks_for_tenant',
             _get_networks_for_tenant_stub(self.networks))
        stub(self.net_manager, '_normalize_network',
             _normalize_network_stub('label'))

    def test_get_all_networks_no_tenant(self):
        nets = self.net_manager.get_all_networks(self.context)
        self.assertEqual(nets, self.networks)


class Quantum2ManagerGetInstanceNwInfo(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerGetInstanceNwInfo, self).setUp()
        self.q_client = ('nova.network.quantum.quantum_connection.'
                         'QuantumClientConnection')
        self.m_client = ('nova.network.quantum2.melange_connection.'
                         'MelangeConnection')

    def test_get_instance_nw_info(self):
        tenant_id = 'project1'
        net_manager = manager.QuantumManager()
        ctx = context.RequestContext(user_id=1, project_id=tenant_id)

        with contextlib.nested(
            mock.patch(self.m_client + '.get_allocated_networks'),
            mock.patch.object(net_manager, '_net_from_quantum'),
            ) as (get_allocated_networks,
                  net_from_quantum):

            networks = _fake_networks(2, tenant_id)
            vifs = [_vif_helper(tenant_id, n['network_id'])
                    for n in networks]

            get_allocated_networks.return_value = vifs
            net_from_quantum.return_value = networks

            get_nw_info = net_manager.get_instance_nw_info
            res = get_nw_info(ctx, instance_id=1, project_id=tenant_id)
            self.assertEqual(len(res), len(vifs))

    def test_get_instance_nw_info_correct_order(self):
        tenant_id = 'project1'
        net_manager = manager.QuantumManager()
        ctx = context.RequestContext(user_id=1, project_id=tenant_id)

        networks = _fake_networks(2, tenant_id)
        vifs = [_vif_helper(tenant_id, networks[-1]['network_id']),
                _vif_helper(tenant_id, networks[0]['network_id'])]

        tenant_networks = {tenant_id: [{'id': n['network_id'],
                                        'name': n['name']}
                                       for n in networks]}

        tenant_net_func = _get_quantum_tenant_nets_stub(tenant_networks)
        network_order = ['net%d' % i for i in xrange(len(networks))]
        self.flags(network_order=network_order)

        with contextlib.nested(
            mock.patch(self.m_client + '.get_allocated_networks'),
            mock.patch.object(net_manager, '_get_quantum_tenant_nets'),
            ) as (get_allocated_networks,
                  get_quantum_tenant_nets):

            get_allocated_networks.return_value = vifs
            get_quantum_tenant_nets.side_effect = tenant_net_func

            get_nw_info = net_manager.get_instance_nw_info
            res = get_nw_info(ctx, instance_id=1, project_id=tenant_id)

            self.assertEqual(len(res), len(vifs))
            self.assertEqual(res[0]['network']['label'],
                             networks[0]['name'])
            self.assertEqual(res[1]['network']['label'],
                             networks[-1]['name'])


class Quantum2ManagerGetIpsAndIdsFromVifs(test.TestCase):
    def test_get_ips_and_ids_from_vifs(self):
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        network_uuid = str(nova.utils.gen_uuid())
        self.context = context.RequestContext(user_id=1,
                                      project_id='project1')

        vif = _vif_helper(self.tenant_id, network_uuid)

        res = self.net_manager._get_ips_and_ids_from_vif(vif)
        addresses, tenants, network_uuids = res
        self.assertEquals(addresses, ['10.0.0.100'])
        self.assertEquals(tenants, set(['project1']))
        self.assertEquals(network_uuids, set([network_uuid]))


class Quantum2ManagerCleanUpMelange(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerCleanUpMelange, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        self.context = context.RequestContext(user_id=1,
                                      project_id=self.tenant_id)
        self.networks = _fake_networks(network_count=1,
                                       tenant_id=self.tenant_id)
        self.vifs, allocate_stub = _allocate_for_instance_networks_stub(
                                                                self.networks)
        self.allocate_stub = allocate_stub

    def test_clean_up_melange(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks',
                  self.allocate_stub)

        self.net_manager._clean_up_melange(self.tenant_id, instance_id=1,
                                           raise_exception=False)

    def test_clean_up_melange_no_exception_doesnt_raise(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks',
                  self.allocate_stub)

        self.net_manager._clean_up_melange(self.tenant_id, instance_id=1,
                                           raise_exception=True)

    def test_clean_up_melange_exception_raise_exception_true_raises(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks',
                  dummy_raise)
        self.assertRaises(exception.VirtualInterfaceCleanupException,
                          self.net_manager._clean_up_melange,
                          self.tenant_id, instance_id=1, raise_exception=True)

    def test_clean_up_melange_exception_raise_exception_false(self):
        self.stubs.Set(melange_connection.MelangeConnection,
                  'allocate_for_instance_networks',
                  dummy_raise)
        self.net_manager._clean_up_melange(self.tenant_id, instance_id=1,
                                           raise_exception=False)


class Quantum2ManagerGenerateAddressPairs(test.TestCase):
    def test_generate_address_pairs(self):
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        network_uuid = str(nova.utils.gen_uuid())
        self.context = context.RequestContext(user_id=1,
                                      project_id=self.tenant_id)
        vif = _vif_helper(self.tenant_id, network_uuid)
        ips = _ip_addresses_helper(1)
        res = self.net_manager._generate_address_pairs(vif, ips)

        self.assertEquals(res[0]['ip_address'], '10.0.0.100')
        self.assertEquals(res[0]['mac_address'], '00:00:00:00:00:00')


class Quantum2ManagerDeallocatePort(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerDeallocatePort, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        self.network_uuid = str(nova.utils.gen_uuid())
        self.context = context.RequestContext(user_id=1,
                                      project_id=self.tenant_id)
        self.stubs.Set(quantum_connection.QuantumClientConnection,
             'get_port_by_attachment', _get_port_by_attachment_stub('port'))

    def test_deallocate_port_no_port(self):
        self.stubs.Set(quantum_connection.QuantumClientConnection,
             'get_port_by_attachment', _get_port_by_attachment_stub(None))
        with mock.patch('nova.network.quantum.quantum_connection.'
                        'QuantumClientConnection.detach_and_delete_port') \
                    as patch:
            self.net_manager._deallocate_port(self.tenant_id,
                                              self.network_uuid,
                                              interface_id=1)
            self.assertEqual(patch.called, False)

    def test_deallocate_port(self):
        with mock.patch('nova.network.quantum.quantum_connection.'
                        'QuantumClientConnection.detach_and_delete_port') \
                    as patch:
            self.net_manager._deallocate_port(self.tenant_id,
                                              self.network_uuid,
                                              interface_id=1)
            self.assertEqual(patch.called, True)


class Quantum2ManagerQuantumTenantNets(test.TestCase):
    def test_get_quantum_tenant_nets(self):
        networks = {'networks': []}
        self.stubs.Set(nova.network.quantum.client, 'Client',
                       _quantum_client_stub(networks))
        net_manager = manager.QuantumManager()
        res = net_manager._get_quantum_tenant_nets('project1')
        self.assertEquals(res, networks['networks'])


class Quantum2ManagerNetFromQuantum(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerNetFromQuantum, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        self.networks = {self.tenant_id: 'from_quantum'}
        self.stubs.Set(self.net_manager, '_get_quantum_tenant_nets',
                       _get_quantum_tenant_nets_stub(self.networks))

    def test_nets_from_quantum(self):
        net = {self.tenant_id: 'from_cache'}
        res = self.net_manager._net_from_quantum(self.tenant_id, net)
        self.assertEquals('from_cache', res)

    def test_nets_from_quantum_no_cache(self):
        res = self.net_manager._net_from_quantum(self.tenant_id, {})
        self.assertEquals('from_quantum', res)


class Quantum2ManagerVifFromNetwork(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerVifFromNetwork, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        self.label = 'public'
        self.network_uuid = str(nova.utils.gen_uuid())
        self.context = context.RequestContext(user_id=1,
                                      project_id=self.tenant_id)

    def test_vif_from_network(self):
        vifs = _vif_helper(self.tenant_id, self.network_uuid)
        res = self.net_manager._vif_from_network(vifs, self.network_uuid,
                                                 self.label)
        self.assertEquals(res['network']['subnets'][0]['ips'][0]['address'],
                          '10.0.0.100')

    def test_vif_from_network_no_gateway(self):
        vifs = _vif_helper(self.tenant_id, self.network_uuid)
        vifs['ip_addresses'][0]['ip_block'].pop('gateway')
        res = self.net_manager._vif_from_network(vifs, self.network_uuid,
                                                 self.label)
        self.assertEqual(res['network']['subnets'][0].get('gateway'), None)

    def test_vif_from_network_no_dns(self):
        vifs = _vif_helper(self.tenant_id, self.network_uuid)
        vifs['ip_addresses'][0]['ip_block'].pop('dns1')
        vifs['ip_addresses'][0]['ip_block'].pop('dns2')
        res = self.net_manager._vif_from_network(vifs, self.network_uuid,
                                                 self.label)
        self.assertEqual(res['network']['subnets'][0].get('dns'), [])


class Quantum2ManagerVifsToModel(test.TestCase):
    def setUp(self):
        super(Quantum2ManagerVifsToModel, self).setUp()
        self.tenant_id = 'project1'
        self.net_manager = manager.QuantumManager()
        stub = self.stubs.Set
        self.context = context.RequestContext(user_id=1,
                                      project_id=self.tenant_id)
        self.networks = _fake_networks(network_count=1,
                                       tenant_id=self.tenant_id)

        stub(self.net_manager, '_get_ips_and_ids_from_vif',
                                _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=['project1'],
                                          networks=self.networks))
        stub(self.net_manager, '_net_from_quantum',
             _net_from_quantum_stub([self.networks[0]]))
        self.vif = _vif_helper(self.tenant_id, self.networks[0]['id'])

    def test_vifs_to_model_no_network_ids_fails(self):
        self.stubs.Set(self.net_manager, '_get_ips_and_ids_from_vif',
                                _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=['project1'],
                                          networks=[]))
        self.assertRaises(exception.VirtualInterfaceIntegrityException,
                self.net_manager._vifs_to_model, [self.vif])

    def test_vifs_to_model_no_tenant_ids_fails(self):
        self.stubs.Set(self.net_manager, '_get_ips_and_ids_from_vif',
                                _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=[],
                                          networks=self.networks))
        self.assertRaises(exception.VirtualInterfaceIntegrityException,
                self.net_manager._vifs_to_model, [self.vif])

    def test_vifs_to_model_too_many_networks_fails(self):
        networks = _fake_networks(network_count=4,
                tenant_id=self.tenant_id)
        self.stubs.Set(self.net_manager, '_get_ips_and_ids_from_vif',
                                _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=['project1'],
                                          networks=networks))
        self.assertRaises(exception.VirtualInterfaceIntegrityException,
                self.net_manager._vifs_to_model, [self.vif])

    def test_vifs_to_model_too_many_tenants_fails(self):
        networks = _fake_networks(network_count=4,
                tenant_id=self.tenant_id)
        self.stubs.Set(self.net_manager, '_get_ips_and_ids_from_vif',
                                _ips_from_vif_stub(ips_per_vif=2,
                                          tenants=['project1'] * 4,
                                          networks=networks))
        self.assertRaises(exception.VirtualInterfaceIntegrityException,
                self.net_manager._vifs_to_model, [self.vif])

    def test_vifs_to_model_no_labels(self):
        self.networks[0].pop('name')
        self.stubs.Set(self.net_manager, '_net_from_quantum',
             _net_from_quantum_stub([self.networks[0]]))
        res = self.net_manager._vifs_to_model([self.vif])
        net_id = self.networks[0]['network_id']
        self.assertEquals(res[0]['network']['label'], 'net-%s' % net_id)

    def test_vifs_to_model(self):
        res = self.net_manager._vifs_to_model([self.vif])
        self.assertEquals(res[0]['network']['subnets'][0]['ips'][0]['address'],
                          '10.0.0.100')


class Quantum2GetInstanceUUIDS(test.TestCase):
    def setUp(self):
        super(Quantum2GetInstanceUUIDS, self).setUp()
        self.net_manager = manager.QuantumManager()

        self.context = context.RequestContext(user_id=1,
                                              project_id=1)

    def test_get_instance_uuids_by_ip_filter(self):
        filters = {'ip': 'ip_address'}

        self.stubs.Set(melange_connection.MelangeConnection,
                       'get_instance_ids_by_ip_address',
                       lambda a, b, c: ["instance_id"])

        instance = self.mox.CreateMockAnything()
        instance.uuid = 'instance_uuid'

        self.mox.StubOutWithMock(db, 'instance_get_by_uuid')
        db.instance_get_by_uuid(self.context,
                                'instance_id').AndReturn(instance)

        self.mox.ReplayAll()

        uuids = self.net_manager.get_instance_uuids_by_ip_filter(self.context,
                                                                filters)
        self.assertEquals(uuids, [{'instance_uuid':'instance_uuid'}])
