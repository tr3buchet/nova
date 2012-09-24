# Copyright 2012 Rackspace Hosting
# All Rights Reserved.

"""
Tests For Rackspace Host Manager.
"""

from nova.compute import task_states
from nova.compute import vm_states
from nova.scheduler import host_manager
from nova.scheduler import rackspace_host_manager as rax_host_manager
from nova import test


class RackspaceHostManagerTestCase(test.TestCase):
    """Test case for Rackspace Host Manager."""

    def setUp(self):
        super(RackspaceHostManagerTestCase, self).setUp()
        self.flags(scheduler_spare_host_percentage=10)
        self.host_manager = rax_host_manager.RackspaceHostManager()

        def _choose_host_filters(*args):
            return []

        self.stubs.Set(self.host_manager, '_choose_host_filters',
                _choose_host_filters)

    def _create_hosts(self, num_instances_mod, num_hosts=50):
        hosts = []
        num_empty = 0
        for i in xrange(num_hosts):
            host = host_manager.HostState('host-%03i' % (i + 1), 'node1')
            host.num_instances = ((i % num_instances_mod)
                    if num_instances_mod else 0)
            if not host.num_instances:
                num_empty += 1
            hosts.append(host)
        return hosts, num_empty

    def test_spares_disabled(self):
        self.flags(scheduler_spare_host_percentage=0)
        hosts, _num_empty = self._create_hosts(10)
        filtered_hosts = self.host_manager.filter_hosts(iter(hosts), {})
        self.assertEqual(len(hosts), len(filtered_hosts))

    def test_all_hosts_empty(self):
        hosts, _num_empty = self._create_hosts(0)
        filtered_hosts = self.host_manager.filter_hosts(iter(hosts), {})
        self.assertEqual(len(hosts), len(filtered_hosts))

    def test_no_empty_hosts(self):
        hosts, _num_empty = self._create_hosts(100)
        # Fudge the first host to contain instances
        hosts[0].num_instances = 1
        filtered_hosts = self.host_manager.filter_hosts(iter(hosts), {})
        self.assertEqual(len(hosts), len(filtered_hosts))

    def test_more_empty_hosts_than_spares_percentage(self):
        hosts, num_empty = self._create_hosts(2)
        filtered_hosts = self.host_manager.filter_hosts(iter(hosts), {})
        self.assertTrue(num_empty > 5)
        # Reserve 10% of 50 hosts... or 5 hosts.
        self.assertEqual(len(filtered_hosts), len(hosts) - 5)
        empties = [x for x in filtered_hosts if not x.num_instances]
        # Mod of 2 means we had 25 full and 25 empty before filtering
        self.assertEqual(len(empties), 20)

    def test_less_empty_hosts_than_spares_percentage(self):
        hosts, num_empty = self._create_hosts(25)
        filtered_hosts = self.host_manager.filter_hosts(iter(hosts), {})
        self.assertEqual(len(filtered_hosts), len(hosts) - num_empty)
        empties = [x for x in filtered_hosts if not x.num_instances]
        self.assertEqual(len(empties), 0)
