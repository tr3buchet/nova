# Copyright 2012 Rackspace Hosting
# All Rights Reserved.

"""
Tests For Rackspace weighting functions.
"""
from nova.compute import vm_states
from nova import config
from nova.openstack.common import cfg
from nova.scheduler import host_manager
from nova.scheduler import least_cost
from nova.scheduler import rackspace_weights as weights
from nova import test


CONF = config.CONF
CONF.import_opt('reserved_host_disk_mb', 'nova.compute.resource_tracker')


class RackspaceWeightsTestCase(test.TestCase):
    def setUp(self):
        super(RackspaceWeightsTestCase, self).setUp()
        self.flags(reserved_host_disk_mb=0, reserved_host_memory_mb=0)
        self.flags(rax_scheduler_num_hosts_to_fuzz=0)
        # Set up 100 empty hosts
        self.hosts = []
        for i in xrange(100):
            hs = host_manager.HostState('host-%03i' % (i + 1), 'node1')
            hs.free_ram_mb = 32 * 1024
            hs.free_disk_mb = 1 * 1024 * 1024 * 1024
            self.hosts.append(hs)
        fn_names = ['compute_empty_host_fn',
                'compute_num_instances_fn',
                'compute_num_instances_for_project_fn',
                'compute_num_instances_for_os_type_fn']
        self.weight_fns = []
        for fn_name in fn_names:
            fn = getattr(weights, fn_name)
            weight = getattr(cfg.CONF, '%s_weight' % fn_name)
            self.weight_fns.append((weight, fn))

    def _fake_instance(self, memory_mb, disk_gb=None, vm_state=None,
            task_state=None, os_type=None, project_id='1'):
        if disk_gb is None:
            disk_gb = 10
        if vm_state is None:
            vm_state = vm_states.ACTIVE
        if os_type is None:
            os_type = 'linux'
        return dict(ephemeral_gb=0, root_gb=disk_gb,
                memory_mb=memory_mb, vm_state=vm_state,
                task_state=task_state, os_type=os_type,
                vcpus=1, project_id=project_id)

    def _weighing_properties(self, instance):
        weighing_properties = dict(project_id=instance['project_id'],
                os_type=instance['os_type'])
        return weighing_properties

    def _get_sorted_hosts(self, weighing_properties):
        hosts = least_cost._get_weighted_hosts(self.weight_fns,
                self.hosts, weighing_properties)[0]
        return sorted(hosts, key=lambda x: x.weight)

    def test_single_instance(self):
        instance = self._fake_instance(512)
        weighing_properties = self._weighing_properties(instance)
        weighted_hosts = self._get_sorted_hosts(weighing_properties)
        host = weighted_hosts[0].host_state
        self.assertTrue(host is not None)
        # Should be the first host
        self.assertEqual(host.host, 'host-001')

    def test_one_instance_already_on_first_host(self):
        instance = self._fake_instance(512)
        weighing_properties = self._weighing_properties(instance)
        # Put an instance on first host
        self.hosts[0].consume_from_instance(instance)
        weighted_hosts = self._get_sorted_hosts(weighing_properties)
        host = weighted_hosts[0].host_state
        self.assertTrue(host is not None)
        self.assertEqual(host.host, 'host-002')

    def test_two_instances_already_on_first_host(self):
        instance = self._fake_instance(512)
        weighing_properties = self._weighing_properties(instance)
        # Put 2 instances on first host
        self.hosts[0].consume_from_instance(instance)
        self.hosts[0].consume_from_instance(instance)
        weighted_hosts = self._get_sorted_hosts(weighing_properties)
        host = weighted_hosts[0].host_state
        self.assertTrue(host is not None)
        # Should be the second host
        self.assertEqual(host.host, 'host-002')

    def test_one_instance_on_first_two_hosts(self):
        instance = self._fake_instance(512)
        weighing_properties = self._weighing_properties(instance)
        # Put 2 instances on first host
        self.hosts[0].consume_from_instance(instance)
        self.hosts[1].consume_from_instance(instance)
        weighted_hosts = self._get_sorted_hosts(weighing_properties)
        host = weighted_hosts[0].host_state
        self.assertTrue(host is not None)
        # Should be the second host
        self.assertEqual(host.host, 'host-003')

    def test_one_instance_on_first_two_hosts_diff_project(self):
        instance_proj1 = self._fake_instance(512)
        instance_proj2 = self._fake_instance(512, project_id='2')
        weighing_properties = self._weighing_properties(instance_proj2)
        # Put 2 instances on first host
        self.hosts[0].consume_from_instance(instance_proj1)
        self.hosts[1].consume_from_instance(instance_proj1)
        weighted_hosts = self._get_sorted_hosts(weighing_properties)
        host = weighted_hosts[0].host_state
        self.assertTrue(host is not None)
        # Should go to the first host
        self.assertEqual(host.host, 'host-001')
