# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011,2012 Nicira, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import inspect
from nova.network import api as network_api
from nova.network.quantum2 import api as quantum_api

from nova import flags
from nova import log as logging

from nova import test
from nova import utils

LOG = logging.getLogger(__name__)
FLAGS = flags.FLAGS


#class QuantumConnectionTestCase(test.TestCase):
#
#    def test_connection(self):
#        pass

class QuantumAPITests(test.TestCase):
    def setUp(self):
        self.quantum_api = quantum_api.QuantumAPI()

    def tearDown(self):
        pass

    def test_methods_are_equivalent_to_network_api(self):
        api_methods = inspect.getmembers(network_api.API,
                                         predicate=inspect.ismethod)
        qapi_methods = inspect.getmembers(quantum_api.QuantumAPI,
                                          predicate=inspect.ismethod)

        def get_like_method(name):
            for fname, fspec in qapi_methods:
                if fname == name:
                    return fspec

        for method in api_methods:
            if method[0] == '__init__':
                continue
            self.assertEqual(inspect.getargspec(method[1]),
                             inspect.getargspec(get_like_method(method[0])))

    def test_validate_networks(self):
        # can't think of a way to test other than just calling
        # function just passes...
        self.quantum_api.validate_networks('', '')

    def test_disassociate(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.disassociate,
                          '', '')

    def test_get_fixed_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_fixed_ip,
                          '', '')

    def test_get_fixed_ip_by_address(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_fixed_ip_by_address,
                          '', '')

    def test_get_floating_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_floating_ip,
                          '', '')

    def test_get_floating_ip_pools(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_floating_ip_pools,
                          '')

    def test_get_floating_ip_by_address(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_floating_ip_by_address,
                          '', '')

    def test_get_floating_ips_by_project(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_floating_ips_by_project,
                          '')

    def test_get_floating_ips_by_fixed_address(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_floating_ips_by_fixed_address,
                          '', '')

    def test_get_vifs_by_instance(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_vifs_by_instance,
                          '', '')

    def test_get_vif_by_mac_address(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_vif_by_mac_address,
                          '', '')

    def test_allocate_floating_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.allocate_floating_ip,
                          '', '')

    def test_release_floating_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.release_floating_ip,
                          '', '')

    def test_associate_floating_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.associate_floating_ip,
                          '', '', '')

    def test_disassociate_floating_ip(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.disassociate_floating_ip,
                          '', '')

    def test_add_fixed_ip_to_instance(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.add_fixed_ip_to_instance,
                          '', '', '', '')

    def test_remove_fixed_ip_from_instance(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.remove_fixed_ip_from_instance,
                          '', '', '')

    def test_add_network_to_project(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.add_network_to_project,
                          '', '')

    def test_get_instance_uuids_by_ip_filter(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_instance_uuids_by_ip_filter,
                          '', '')

    def test_get_dns_domains(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_dns_domains,
                          '')

    def test_add_dns_entry(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.add_dns_entry,
                          '', '', '', '', '')

    def test_modify_dns_entry(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.modify_dns_entry,
                          '', '', '', '')

    def test_delete_dns_entry(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.delete_dns_entry,
                          '', '', '')

    def test_delete_dns_domain(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.delete_dns_domain,
                          '', '')

    def test_get_dns_entries_by_address(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_dns_entries_by_address,
                          '', '', '')

    def test_get_dns_entries_by_name(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.get_dns_entries_by_name,
                          '', '', '')

    def test_create_private_dns_domain(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.create_private_dns_domain,
                          '', '', '')

    def test_create_public_dns_domain(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.create_public_dns_domain,
                          '', '')

    def test_setup_networks_on_host(self):
        self.assertRaises(NotImplementedError,
                          self.quantum_api.setup_networks_on_host,
                          '', '')
