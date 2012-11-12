# Copyright (c) 2012 OpenStack, LLC
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

import datetime

from nova.api.openstack.compute.contrib import instance_usage_audit_log as ial
from nova.cells import api as cells_api
from nova import context
from nova import db
from nova.openstack.common import timeutils
from nova import test
from nova.tests.api.openstack import fakes
from nova import utils


TEST_COMPUTE_SERVICES = [dict(host='foo', topic='compute'),
                         dict(host='bar', topic='compute'),
                         dict(host='baz', topic='compute'),
                         dict(host='plonk', topic='compute'),
                         dict(host='wibble', topic='bogus'),
                         ]


begin1 = datetime.datetime(2012, 7, 4, 6, 0, 0)
begin2 = end1 = datetime.datetime(2012, 7, 5, 6, 0, 0)
begin3 = end2 = datetime.datetime(2012, 7, 6, 6, 0, 0)
end3 = datetime.datetime(2012, 7, 7, 6, 0, 0)


#test data


TEST_LOGS1 = [
    #all services done, no errors.
    dict(host="plonk", period_beginning=begin1, period_ending=end1,
         state="DONE", errors=0, task_items=23, message="test1"),
    dict(host="baz", period_beginning=begin1, period_ending=end1,
         state="DONE", errors=0, task_items=17, message="test2"),
    dict(host="bar", period_beginning=begin1, period_ending=end1,
         state="DONE", errors=0, task_items=10, message="test3"),
    dict(host="foo", period_beginning=begin1, period_ending=end1,
         state="DONE", errors=0, task_items=7, message="test4"),
    ]


TEST_LOGS2 = [
    #some still running...
    dict(host="plonk", period_beginning=begin2, period_ending=end2,
         state="DONE", errors=0, task_items=23, message="test5"),
    dict(host="baz", period_beginning=begin2, period_ending=end2,
         state="DONE", errors=0, task_items=17, message="test6"),
    dict(host="bar", period_beginning=begin2, period_ending=end2,
         state="RUNNING", errors=0, task_items=10, message="test7"),
    dict(host="foo", period_beginning=begin2, period_ending=end2,
         state="DONE", errors=0, task_items=7, message="test8"),
    ]


TEST_LOGS3 = [
    #some errors..
    dict(host="plonk", period_beginning=begin3, period_ending=end3,
         state="DONE", errors=0, task_items=23, message="test9"),
    dict(host="baz", period_beginning=begin3, period_ending=end3,
         state="DONE", errors=2, task_items=17, message="test10"),
    dict(host="bar", period_beginning=begin3, period_ending=end3,
         state="DONE", errors=0, task_items=10, message="test11"),
    dict(host="foo", period_beginning=begin3, period_ending=end3,
         state="DONE", errors=1, task_items=7, message="test12"),
    ]


def dont_call_me(*args, **kw):
    raise AssertionError("Called a method that should not be called")


def fake_service_get_all(context):
    return TEST_COMPUTE_SERVICES


def fake_task_log_get_all(context, task_name, begin, end):
    assert task_name == "instance_usage_audit"

    if begin == begin1 and end == end1:
        return TEST_LOGS1
    if begin == begin2 and end == end2:
        return TEST_LOGS2
    if begin == begin3 and end == end3:
        return TEST_LOGS3
    raise AssertionError("Invalid date %s to %s" % (begin, end))


def fake_last_completed_audit_period(unit=None, before=None):
    audit_periods = [(begin3, end3),
                     (begin2, end2),
                     (begin1, end1)]
    if before is not None:
        for begin, end in audit_periods:
            if before > end:
                return begin, end
        raise AssertionError("Invalid before date %s" % (before))
    return begin1, end1


def fake_cell_broadcast_call(context, direction, method, **kw):
    assert direction == 'down'
    assert method in ("task_logs", "list_services")

    if method == "task_logs":
        assert "task_name" in kw
        task_name = kw.get('task_name')
        begin = kw.get('begin')
        end = kw.get('end')
        tlogs = fake_task_log_get_all(context, task_name, begin, end)
        return (([l for l in tlogs if l['host'].startswith('b')],
                'toobee'),
                ([l for l in tlogs if not l['host'].startswith('b')],
                'nottoobee'))
    elif method == "list_services":
        svcs = fake_service_get_all(context)
        return (([s for s in svcs if s['host'].startswith('b')],
                'toobee'),
                ([s for s in svcs if not s['host'].startswith('b')],
                'nottoobee'))


class InstanceUsageAuditLogTest(test.TestCase):
    def setUp(self):
        super(InstanceUsageAuditLogTest, self).setUp()
        self.context = context.get_admin_context()
        timeutils.set_time_override(datetime.datetime(2012, 7, 5, 10, 0, 0))
        self.controller = ial.InstanceUsageAuditLogController()

        self.stubs.Set(utils, 'last_completed_audit_period',
                            fake_last_completed_audit_period)
        self.stubs.Set(db, 'service_get_all',
                            fake_service_get_all)
        self.stubs.Set(db, 'task_log_get_all',
                            fake_task_log_get_all)

    def tearDown(self):
        super(InstanceUsageAuditLogTest, self).tearDown()
        timeutils.clear_time_override()

    def test_index(self):
        req = fakes.HTTPRequest.blank('/v2/fake/os-instance_usage_audit_log')
        result = self.controller.index(req)
        self.assertIn('instance_usage_audit_logs', result)
        logs = result['instance_usage_audit_logs']
        self.assertEquals(57, logs['total_instances'])
        self.assertEquals(0, logs['total_errors'])
        self.assertEquals(4, len(logs['log']))
        self.assertEquals(4, logs['num_hosts'])
        self.assertEquals(4, logs['num_hosts_done'])
        self.assertEquals(0, logs['num_hosts_running'])
        self.assertEquals(0, logs['num_hosts_not_run'])
        self.assertEquals("ALL hosts done. 0 errors.", logs['overall_status'])

    def test_show(self):
        req = fakes.HTTPRequest.blank(
                    '/v2/fake/os-instance_usage_audit_log/show')
        result = self.controller.show(req, '2012-07-05 10:00:00')
        self.assertIn('instance_usage_audit_log', result)
        logs = result['instance_usage_audit_log']
        self.assertEquals(57, logs['total_instances'])
        self.assertEquals(0, logs['total_errors'])
        self.assertEquals(4, len(logs['log']))
        self.assertEquals(4, logs['num_hosts'])
        self.assertEquals(4, logs['num_hosts_done'])
        self.assertEquals(0, logs['num_hosts_running'])
        self.assertEquals(0, logs['num_hosts_not_run'])
        self.assertEquals("ALL hosts done. 0 errors.", logs['overall_status'])

    def test_show_with_running(self):
        req = fakes.HTTPRequest.blank(
                    '/v2/fake/os-instance_usage_audit_log/show')
        result = self.controller.show(req, '2012-07-06 10:00:00')
        self.assertIn('instance_usage_audit_log', result)
        logs = result['instance_usage_audit_log']
        self.assertEquals(57, logs['total_instances'])
        self.assertEquals(0, logs['total_errors'])
        self.assertEquals(4, len(logs['log']))
        self.assertEquals(4, logs['num_hosts'])
        self.assertEquals(3, logs['num_hosts_done'])
        self.assertEquals(1, logs['num_hosts_running'])
        self.assertEquals(0, logs['num_hosts_not_run'])
        self.assertEquals("3 of 4 hosts done. 0 errors.",
                          logs['overall_status'])

    def test_show_with_errors(self):
        req = fakes.HTTPRequest.blank(
                    '/v2/fake/os-instance_usage_audit_log/show')
        result = self.controller.show(req, '2012-07-07 10:00:00')
        self.assertIn('instance_usage_audit_log', result)
        logs = result['instance_usage_audit_log']
        self.assertEquals(57, logs['total_instances'])
        self.assertEquals(3, logs['total_errors'])
        self.assertEquals(4, len(logs['log']))
        self.assertEquals(4, logs['num_hosts'])
        self.assertEquals(4, logs['num_hosts_done'])
        self.assertEquals(0, logs['num_hosts_running'])
        self.assertEquals(0, logs['num_hosts_not_run'])
        self.assertEquals("ALL hosts done. 3 errors.",
                          logs['overall_status'])


class CellsInstanceUsageAuditLogTest(InstanceUsageAuditLogTest):

    def setUp(self):
        super(CellsInstanceUsageAuditLogTest, self).setUp()
        self.controller = ial.CellsInstanceUsageAuditLogController()
        self.stubs.Set(cells_api, 'cell_broadcast_call',
                            fake_cell_broadcast_call)
        self.stubs.Set(db, 'service_get_all',
                            dont_call_me)
        self.stubs.Set(db, 'task_log_get_all',
                            dont_call_me)
