# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012, Red Hat, Inc.
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
Unit Tests for nova.compute.rpcapi
"""

from nova.compute import rpcapi as compute_rpcapi
from nova import context
from nova import flags
from nova.openstack.common import rpc
from nova import test


FLAGS = flags.FLAGS


class ComputeRpcAPITestCase(test.TestCase):

    def setUp(self):
        self.fake_instance = {
            'uuid': 'fake_uuid',
            'host': 'fake_host',
            'name': 'fake_name',
            'id': 'fake_id',
        }
        super(ComputeRpcAPITestCase, self).setUp()

    def tearDown(self):
        super(ComputeRpcAPITestCase, self).tearDown()

    def _test_compute_api(self, method, rpc_method, **kwargs):
        ctxt = context.RequestContext('fake_user', 'fake_project')
        if 'rpcapi_class' in kwargs:
            rpcapi_class = kwargs['rpcapi_class']
            del kwargs['rpcapi_class']
        else:
            rpcapi_class = compute_rpcapi.ComputeAPI
        rpcapi = rpcapi_class()
        expected_retval = 'foo' if method == 'call' else None

        expected_version = kwargs.pop('version', rpcapi.BASE_RPC_API_VERSION)
        expected_msg = rpcapi.make_msg(method, **kwargs)
        if 'host_param' in expected_msg['args']:
            host_param = expected_msg['args']['host_param']
            del expected_msg['args']['host_param']
            expected_msg['args']['host'] = host_param
        elif 'host' in expected_msg['args']:
            del expected_msg['args']['host']
        if 'destination' in expected_msg['args']:
            del expected_msg['args']['destination']
        if 'instance' in expected_msg['args']:
            instance = expected_msg['args']['instance']
            del expected_msg['args']['instance']
            if method in ['rollback_live_migration_at_destination',
                          'pre_live_migration', 'remove_volume_connection',
                          'post_live_migration_at_destination',
                          'check_can_live_migrate_destination',
                          'check_can_live_migrate_source']:
                expected_msg['args']['instance_id'] = instance['id']
            elif method == 'get_instance_disk_info':
                expected_msg['args']['instance_name'] = instance['name']
            else:
                expected_msg['args']['instance_uuid'] = instance['uuid']
        expected_msg['version'] = expected_version

        cast_and_call = ['confirm_resize', 'stop_instance']
        if rpc_method == 'call' and method in cast_and_call:
            kwargs['cast'] = False
        if 'host' in kwargs:
            host = kwargs['host']
        elif 'destination' in kwargs:
            host = kwargs['destination']
        else:
            host = kwargs['instance']['host']
        expected_topic = '%s.%s' % (FLAGS.compute_topic, host)

        self.fake_args = None
        self.fake_kwargs = None

        def _fake_rpc_method(*args, **kwargs):
            self.fake_args = args
            self.fake_kwargs = kwargs
            if expected_retval:
                return expected_retval

        self.stubs.Set(rpc, rpc_method, _fake_rpc_method)

        retval = getattr(rpcapi, method)(ctxt, **kwargs)

        self.assertEqual(retval, expected_retval)
        expected_args = [ctxt, expected_topic, expected_msg]
        for arg, expected_arg in zip(self.fake_args, expected_args):
            self.assertEqual(arg, expected_arg)

    def test_add_aggregate_host(self):
        self._test_compute_api('add_aggregate_host', 'cast', aggregate_id='id',
                host_param='host', host='host')

    def test_add_fixed_ip_to_instance(self):
        self._test_compute_api('add_fixed_ip_to_instance', 'cast',
                instance=self.fake_instance, network_id='id')

    def test_attach_volume(self):
        self._test_compute_api('attach_volume', 'cast',
                instance=self.fake_instance, volume_id='id', mountpoint='mp')

    def test_check_can_live_migrate_destination(self):
        self._test_compute_api('check_can_live_migrate_destination', 'call',
                version='1.2', instance=self.fake_instance, destination='dest',
                block_migration=True, disk_over_commit=True)

    def test_check_can_live_migrate_source(self):
        self._test_compute_api('check_can_live_migrate_source', 'call',
                version='1.2', instance=self.fake_instance,
                dest_check_data={"test": "data"})

    def test_confirm_resize_cast(self):
        self._test_compute_api('confirm_resize', 'cast',
                instance=self.fake_instance, migration_id='id', host='host')

    def test_confirm_resize_call(self):
        self._test_compute_api('confirm_resize', 'call',
                instance=self.fake_instance, migration_id='id', host='host')

    def test_detach_volume(self):
        self._test_compute_api('detach_volume', 'cast',
                instance=self.fake_instance, volume_id='id')

    def test_finish_resize(self):
        self._test_compute_api('finish_resize', 'cast',
                instance=self.fake_instance, migration_id='id',
                image='image', disk_info='disk_info', host='host')

    def test_finish_revert_resize(self):
        self._test_compute_api('finish_revert_resize', 'cast',
                instance=self.fake_instance, migration_id='id', host='host')

    def test_get_console_output(self):
        self._test_compute_api('get_console_output', 'call',
                instance=self.fake_instance, tail_length='tl')

    def test_get_console_pool_info(self):
        self._test_compute_api('get_console_pool_info', 'call',
                console_type='type', host='host')

    def test_get_console_topic(self):
        self._test_compute_api('get_console_topic', 'call', host='host')

    def test_get_diagnostics(self):
        self._test_compute_api('get_diagnostics', 'call',
                instance=self.fake_instance)

    def test_get_instance_disk_info(self):
        self._test_compute_api('get_instance_disk_info', 'call',
                instance=self.fake_instance)

    def test_get_vnc_console(self):
        self._test_compute_api('get_vnc_console', 'call',
                instance=self.fake_instance, console_type='type')

    def test_host_maintenance_mode(self):
        self._test_compute_api('host_maintenance_mode', 'call',
                host_param='param', mode='mode', host='host')

    def test_host_power_action(self):
        self._test_compute_api('host_power_action', 'call', action='action',
                host='host')

    def test_inject_file(self):
        self._test_compute_api('inject_file', 'cast',
                instance=self.fake_instance, path='path', file_contents='fc')

    def test_inject_network_info(self):
        self._test_compute_api('inject_network_info', 'cast',
                instance=self.fake_instance)

    def test_lock_instance(self):
        self._test_compute_api('lock_instance', 'cast',
                instance=self.fake_instance)

    def test_post_live_migration_at_destination(self):
        self._test_compute_api('post_live_migration_at_destination', 'call',
                instance=self.fake_instance, block_migration='block_migration',
                host='host')

    def test_pause_instance(self):
        self._test_compute_api('pause_instance', 'cast',
                instance=self.fake_instance)

    def test_power_off_instance(self):
        self._test_compute_api('power_off_instance', 'cast',
                instance=self.fake_instance)

    def test_power_on_instance(self):
        self._test_compute_api('power_on_instance', 'cast',
                instance=self.fake_instance)

    def test_pre_live_migration(self):
        self._test_compute_api('pre_live_migration', 'call',
                instance=self.fake_instance, block_migration='block_migration',
                disk='disk', host='host')

    def test_reboot_instance(self):
        self._test_compute_api('reboot_instance', 'cast',
                instance=self.fake_instance, reboot_type='type')

    def test_rebuild_instance(self):
        self._test_compute_api('rebuild_instance', 'cast',
                instance=self.fake_instance, new_pass='pass',
                injected_files='files', image_ref='ref',
                orig_image_ref='orig_ref',
                orig_sys_metadata='orig_sys_metadata')

    def refresh_provider_fw_rules(self):
        self._test_compute_api('refresh_provider_fw_rules', 'cast',
                host='host')

    def test_refresh_security_group_rules(self):
        self._test_compute_api('refresh_security_group_rules', 'cast',
                rpcapi_class=compute_rpcapi.SecurityGroupAPI,
                security_group_id='id', host='host')

    def test_refresh_security_group_members(self):
        self._test_compute_api('refresh_security_group_members', 'cast',
                rpcapi_class=compute_rpcapi.SecurityGroupAPI,
                security_group_id='id', host='host')

    def test_remove_aggregate_host(self):
        self._test_compute_api('remove_aggregate_host', 'cast',
                aggregate_id='id', host_param='host', host='host')

    def test_remove_fixed_ip_from_instance(self):
        self._test_compute_api('remove_fixed_ip_from_instance', 'cast',
                instance=self.fake_instance, address='addr')

    def test_remove_volume_connection(self):
        self._test_compute_api('remove_volume_connection', 'call',
                instance=self.fake_instance, volume_id='id', host='host')

    def test_rescue_instance(self):
        self._test_compute_api('rescue_instance', 'cast',
                instance=self.fake_instance, rescue_password='pw')

    def test_reset_network(self):
        self._test_compute_api('reset_network', 'cast',
                instance=self.fake_instance)

    def test_resize_instance(self):
        self._test_compute_api('resize_instance', 'cast',
                instance=self.fake_instance, migration_id='id', image='image')

    def test_resume_instance(self):
        self._test_compute_api('resume_instance', 'cast',
                instance=self.fake_instance)

    def test_revert_resize(self):
        self._test_compute_api('revert_resize', 'cast',
                instance=self.fake_instance, migration_id='id', host='host')

    def test_rollback_live_migration_at_destination(self):
        self._test_compute_api('rollback_live_migration_at_destination',
                'cast', instance=self.fake_instance, host='host')

    def test_set_admin_password(self):
        self._test_compute_api('set_admin_password', 'cast',
                instance=self.fake_instance, new_pass='pw')

    def test_set_host_enabled(self):
        self._test_compute_api('set_host_enabled', 'call',
                enabled='enabled', host='host')

    def test_get_host_uptime(self):
        self._test_compute_api('get_host_uptime', 'call', host='host',
                version='1.1')

    def test_snapshot_instance(self):
        self._test_compute_api('snapshot_instance', 'cast',
                instance=self.fake_instance, image_id='id', image_type='type',
                backup_type='type', rotation='rotation')

    def test_start_instance(self):
        self._test_compute_api('start_instance', 'cast',
                instance=self.fake_instance)

    def test_stop_instance_cast(self):
        self._test_compute_api('stop_instance', 'cast',
                instance=self.fake_instance)

    def test_stop_instance_call(self):
        self._test_compute_api('stop_instance', 'call',
                instance=self.fake_instance)

    def test_suspend_instance(self):
        self._test_compute_api('suspend_instance', 'cast',
                instance=self.fake_instance)

    def test_terminate_instance(self):
        self._test_compute_api('terminate_instance', 'cast',
                instance=self.fake_instance)

    def test_unlock_instance(self):
        self._test_compute_api('unlock_instance', 'cast',
                instance=self.fake_instance)

    def test_unpause_instance(self):
        self._test_compute_api('unpause_instance', 'cast',
                instance=self.fake_instance)

    def test_unrescue_instance(self):
        self._test_compute_api('unrescue_instance', 'cast',
                instance=self.fake_instance)
