# Copyright 2012 Rackspace Hosting
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
"""
Tests For Scheduler Host Filters.
"""

from nova.compute import task_states as compute_task_states
from nova.compute import vm_states as compute_vm_states
from nova import config
from nova import context
from nova.scheduler import filters
from nova import test
from nova.tests.scheduler import rackspace_fakes

CONF = config.CONF
CONF.import_opt('rackspace_max_instances_per_host',
                'nova.scheduler.filters.rackspace_filter')
CONF.import_opt('rackspace_max_ios_per_host',
                'nova.scheduler.filters.rackspace_filter')


class HostFiltersTestCase(test.TestCase):
    """Test case for host filters."""

    def setUp(self):
        super(HostFiltersTestCase, self).setUp()
        self.flags(rackspace_max_ios_per_host=10,
                rackspace_max_instances_per_host=50)
        self.context = context.RequestContext('fake', 'fake')
        classes = filters.get_filter_classes(
                ['nova.scheduler.filters.standard_filters'])
        self.class_map = {}
        self.filter_properties = dict(instance_type=dict(id=1,
                memory_mb=1024))
        for cls in classes:
            self.class_map[cls.__name__] = cls

    def test_rackspace_filter_num_iops_passes(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'num_io_ops': 6})
        self.assertTrue(filt_cls._io_ops_filter(host))

    def test_rackspace_filter_num_iops_fails(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'num_io_ops': 11})
        self.assertFalse(filt_cls._io_ops_filter(host))

    def test_rackspace_filter_num_instances_passes(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'num_instances': 49})
        self.assertTrue(filt_cls._num_instances_filter(host))

    def test_rackspace_filter_num_instances_fails(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'num_instances': 51})
        self.assertFalse(filt_cls._num_instances_filter(host))

    def test_rackspace_filter_instance_type_pv_fails(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'allowed_vm_type': 'hvm', 'free_ram_mb': 30 * 1024})
        self.assertFalse(filt_cls.host_passes(host, self.filter_properties))

    def test_rackspace_filter_instance_type_pv_passes(self):
        filt_cls = self.class_map['RackspaceFilter']()
        host_pv = rackspace_fakes.FakeHostState('host1', 'node1',
                {'allowed_vm_type': 'pv', 'free_ram_mb': 30 * 1024})
        host_all = rackspace_fakes.FakeHostState('host2', 'node1',
                {'allowed_vm_type': 'all', 'free_ram_mb': 30 * 1024})
        self.assertTrue(filt_cls.host_passes(host_pv, self.filter_properties))
        self.assertTrue(filt_cls.host_passes(host_all,
                                             self.filter_properties))

    def test_rackspace_filter_instance_type_hvm_fails(self):
        filt_cls = self.class_map['RackspaceFilter']()
        filter_properties = dict(instance_type=dict(id=101,
                memory_mb=1024))
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'allowed_vm_type': 'pv', 'free_ram_mb': 30 * 1024})
        self.assertFalse(filt_cls.host_passes(host, filter_properties))

    def test_rackspace_filter_instance_type_hvm_passes(self):
        filt_cls = self.class_map['RackspaceFilter']()
        filter_properties = dict(instance_type=dict(id=101,
                memory_mb=1024))
        host_hvm = rackspace_fakes.FakeHostState('host1', 'node1',
                {'allowed_vm_type': 'hvm', 'free_ram_mb': 30 * 1024})
        host_all = rackspace_fakes.FakeHostState('host2', 'node1',
                {'allowed_vm_type': 'all', 'free_ram_mb': 30 * 1024})
        self.assertTrue(filt_cls.host_passes(host_hvm, filter_properties))
        self.assertTrue(filt_cls.host_passes(host_all, filter_properties))

    def test_rackspace_filter_ram_check_fails(self):
        """Test that we need 1G of reserve for < 30G instance."""
        filt_props = {'instance_type': {'memory_mb': 1024}}
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'free_ram_mb': 1024})
        self.assertFalse(filt_cls._ram_check_filter(host, filt_props))

    def test_rackspace_filter_ram_check_passes_30g(self):
        filt_props = {'instance_type': {'memory_mb': 30 * 1024}}
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'free_ram_mb': 30 * 1024})
        self.assertTrue(filt_cls._ram_check_filter(host, filt_props))

    def test_rackspace_filter_ram_check_passes(self):
        filt_props = {'instance_type': {'memory_mb': 1024}}
        filt_cls = self.class_map['RackspaceFilter']()
        host = rackspace_fakes.FakeHostState('host1', 'node1',
                {'free_ram_mb': 2048})
        self.assertTrue(filt_cls._ram_check_filter(host, filt_props))
