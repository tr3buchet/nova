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


import datetime

import webob.exc

from nova.api.openstack import extensions
from nova.cells import rpcapi as cells_rpcapi
from nova import config
from nova import db
from nova import utils

CONF = config.CONF


authorize = extensions.extension_authorizer('compute',
                           'instance_usage_audit_log')


class InstanceUsageAuditLogController(object):

    def index(self, req):
        context = req.environ['nova.context']
        authorize(context)
        task_log = self._get_audit_task_logs(context)
        return {'instance_usage_audit_logs': task_log}

    def show(self, req, id):
        context = req.environ['nova.context']
        authorize(context)
        try:
            if '.' in id:
                before_date = datetime.datetime.strptime(str(id),
                                                "%Y-%m-%d %H:%M:%S.%f")
            else:
                before_date = datetime.datetime.strptime(str(id),
                                                "%Y-%m-%d %H:%M:%S")
        except ValueError:
            msg = _("Invalid timestamp for date %s") % id
            raise webob.exc.HTTPBadRequest(explanation=msg)
        task_log = self._get_audit_task_logs(context,
                                                     before=before_date)
        return {'instance_usage_audit_log': task_log}

    def _get_task_logs(self, context, begin, end):
        return db.task_log_get_all(context, "instance_usage_audit",
                                        begin, end)

    def _get_hosts(self, context):
        # We do this this way to include disabled compute services,
        # which can have instances on them. (mdragon)
        services = [svc for svc in db.service_get_all(context)
                if svc['topic'] == CONF.compute_topic]
        return set(serv['host'] for serv in services)

    def _get_audit_task_logs(self, context, begin=None, end=None,
                             before=None):
        """Returns a full log for all instance usage audit tasks on all
           computes.

        :param begin: datetime beginning of audit period to get logs for,
            Defaults to the beginning of the most recently completed
            audit period prior to the 'before' date.
        :param end: datetime ending of audit period to get logs for,
            Defaults to the ending of the most recently completed
            audit period prior to the 'before' date.
        :param before: By default we look for the audit period most recently
            completed before this datetime. Has no effect if both begin and end
            are specified.
        """
        defbegin, defend = utils.last_completed_audit_period(before=before)
        if begin is None:
            begin = defbegin
        if end is None:
            end = defend
        task_logs = self._get_task_logs(context, begin, end)
        hosts = self._get_hosts(context)
        seen_hosts = set()
        done_hosts = set()
        running_hosts = set()
        total_errors = 0
        total_items = 0
        for tlog in task_logs:
            seen_hosts.add(tlog['host'])
            if tlog['state'] == "DONE":
                done_hosts.add(tlog['host'])
            if tlog['state'] == "RUNNING":
                running_hosts.add(tlog['host'])
            total_errors += tlog['errors']
            total_items += tlog['task_items']
        log = dict((tl['host'], dict(state=tl['state'],
                                  instances=tl['task_items'],
                                  errors=tl['errors'],
                                  message=tl['message']))
                  for tl in task_logs)
        missing_hosts = hosts - seen_hosts
        overall_status = "%s hosts done. %s errors." % (
                    'ALL' if len(done_hosts) == len(hosts)
                    else "%s of %s" % (len(done_hosts), len(hosts)),
                    total_errors)
        return dict(period_beginning=str(begin),
                    period_ending=str(end),
                    num_hosts=len(hosts),
                    num_hosts_done=len(done_hosts),
                    num_hosts_running=len(running_hosts),
                    num_hosts_not_run=len(missing_hosts),
                    hosts_not_run=list(missing_hosts),
                    total_instances=total_items,
                    total_errors=total_errors,
                    overall_status=overall_status,
                    log=log)


class CellsInstanceUsageAuditLogController(InstanceUsageAuditLogController):

    def __init__(self):
        super(CellsInstanceUsageAuditLogController, self).__init__()
        self.cells_rpcapi = cells_rpcapi.CellsAPI()

    def _get_hosts(self, context):
        hosts = set()
        responses = self.cells_rpcapi.cell_broadcast_call(context,
                                                  "down",
                                                  "list_services")
        for (response, cell_name) in responses:
            for service in response:
                # We do this this way to include disabled compute services,
                # which can have instances on them. (mdragon)
                if service['topic'] == CONF.compute_topic:
                    #host.add("%s %s" % (cell_name, service['host']))
                    hosts.add(service['host'])
        return hosts

    def _get_task_logs(self, context, begin, end):
        logs = []
        responses = self.cells_rpcapi.cell_broadcast_call(context,
                                      "down",
                                      "task_logs",
                                      task_name="instance_usage_audit",
                                      begin=begin,
                                      end=end)
        for (response, cell_name) in responses:
            for tlog in response:
                #tlog['host'] = "%s %s" % (cell_name, tlog['host'])
                logs.append(tlog)
        return logs


class Instance_usage_audit_log(extensions.ExtensionDescriptor):
    """Admin-only Task Log Monitoring"""
    name = "OSInstanceUsageAuditLog"
    alias = "os-instance_usage_audit_log"
    namespace = "http://docs.openstack.org/ext/services/api/v1.1"
    updated = "2012-07-06T01:00:00+00:00"

    def get_resources(self):
        if CONF.enable_cells:
            controller = CellsInstanceUsageAuditLogController()
        else:
            controller = InstanceUsageAuditLogController()

        ext = extensions.ResourceExtension('os-instance_usage_audit_log',
                                           controller)
        return [ext]
