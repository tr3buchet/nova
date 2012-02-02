# Copyright 2011 Rackspace Hosting
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
Tests For Rackspace Scheduler.
"""

import eventlet

import nova.context
from nova.scheduler import filter_scheduler
from nova.scheduler import rackspace_scheduler
from nova import test

eventlet.monkey_patch()


class RackspaceSchedulerTestCase(test.TestCase):
    """Test case for Rackspace Scheduler."""

    def setUp(self):
        super(RackspaceSchedulerTestCase, self).setUp()
        host_manager_name = ('nova.scheduler.rackspace_host_manager.' +
                'RackspaceHostManager')
        self.flags(scheduler_host_manager=host_manager_name,
                scheduler_default_filters=['InstanceTypeFilter',
                    'RackspaceFilter'])

    def test_schedule_one_at_a_time(self):
        """Make sure we only schedule one at a time"""

        info_dict = dict(num_schedules=0)

        def fake_schedule_run_instance(_self, context, request_spec,
                *args, **kwargs):
            self.assertEqual(info_dict['num_schedules'], 0)
            info_dict['num_schedules'] += 1
            eventlet.sleep(2)
            info_dict['num_schedules'] -= 1
            self.assertEqual(info_dict['num_schedules'], 0)
            return request_spec['num']

        # RackspaceScheduler will super to this call..
        self.stubs.Set(filter_scheduler.FilterScheduler,
                'schedule_run_instance', fake_schedule_run_instance)

        sched = rackspace_scheduler.RackspaceScheduler()

        ctx = nova.context.RequestContext('user', 'project')

        thr1 = eventlet.spawn(sched.schedule_run_instance, ctx, dict(num=1))
        thr2 = eventlet.spawn(sched.schedule_run_instance, ctx, dict(num=2))
        res2 = thr2.wait()
        res1 = thr1.wait()
        self.assertEqual(res2, 2)
        self.assertEqual(res1, 1)
