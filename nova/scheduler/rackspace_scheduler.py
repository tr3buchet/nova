# Copyright (c) 2011 Rackspace Hosting
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
Rackspace specific scheduler.
"""

import eventlet
from eventlet import queue
import greenlet

from nova import flags
from nova.openstack.common import log as logging
from nova.scheduler import filter_scheduler


FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.scheduler.rackspace_scheduler')


class RackspaceScheduler(filter_scheduler.FilterScheduler):
    def __init__(self, *args, **kwargs):
        super(RackspaceScheduler, self).__init__(*args, **kwargs)
        # Only schedule 1 at a time
        self.run_instance_req_queue = queue.Queue(maxsize=1)
        self.run_instance_thread = eventlet.spawn(
                self._run_instance_queue_processor)

    def _run_instance_queue_processor(self):
        run_instance = super(RackspaceScheduler, self).schedule_run_instance
        try:
            while True:
                entry = self.run_instance_req_queue.get()
                entry['context'].update_store()
                try:
                    result = run_instance(
                            entry['context'],
                            entry['request_spec'],
                            *entry['args'],
                            **entry['kwargs'])
                except greenlet.GreenletExit:
                    raise
                except Exception, e:
                    LOG.exception(_("_schedule_run_instance failed"))
                    # We need to return this through the response queue
                    # so the caller can re-raise
                    result = e
                entry['resp_queue'].put(result)
                self.run_instance_req_queue.task_done()
        except greenlet.GreenletExit:
            return

    def populate_filter_properties(self, request_spec, filter_properties):
        super(RackspaceScheduler, self).populate_filter_properties(
                request_spec, filter_properties)
        project_id = request_spec['instance_properties']['project_id']
        os_type = request_spec['instance_properties']['os_type']
        filter_properties['project_id'] = project_id
        filter_properties['os_type'] = os_type

    def schedule_run_instance(self, context, request_spec, *args, **kwargs):
        """This method is called from nova.compute.api to provision
        an instance. However we need to look at the parameters being
        passed in to see if this is a request to:
        1. Create build plan (a list of WeightedHosts) and then provision, or
        2. Use the WeightedHost information in the request parameters
           to simply create the instance (either in this zone or
           a child zone).

        returns a list of the instances created.
        """

        resp_queue = queue.Queue()
        entry = dict(context=context, request_spec=request_spec,
                resp_queue=resp_queue,
                args=args, kwargs=kwargs)
        self.run_instance_req_queue.put(entry)
        result = resp_queue.get()
        if isinstance(result, BaseException):
            raise result
        return result
