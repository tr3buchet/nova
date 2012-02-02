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
Rackspace specific host manager.
"""

from nova.compute import task_states
from nova.compute import vm_states
from nova import flags
from nova import log as logging
from nova.scheduler import host_manager
from nova import utils

FLAGS = flags.FLAGS


class RackspaceHostState(host_manager.HostState):
    """Mutable and immutable information on hosts tracked
    by the ZoneManager. This is an attempt to remove the
    ad-hoc data structures previously used and lock down
    access."""

    def __init__(self, *args, **kwargs):
        super(RackspaceHostState, self).__init__(*args, **kwargs)
        self.vm_states = {}
        self.task_states = {}
        self.num_instances = 0
        self.num_instances_by_project = {}
        self.num_instances_by_os_type = {}
        #Valid vm types on this host: 'pv', 'hvm' or 'all'
        if 'allowed_vm_type' in self.capabilities:
            self.allowed_vm_type = self.capabilities['allowed_vm_type']
        else:
            self.allowed_vm_type = 'all'

    def consume_from_instance(self, instance):
        super(RackspaceHostState, self).consume_from_instance(instance)
        # Track number of instances on host
        self.num_instances += 1

        # Track number of instances by project_id
        project_id = instance.get('project_id')
        if project_id not in self.num_instances_by_project:
            self.num_instances_by_project[project_id] = 0
        self.num_instances_by_project[project_id] += 1

        # Track number of instances in certain vm_states
        vm_state = instance.get('vm_state', vm_states.BUILDING)
        if vm_state not in self.vm_states:
            self.vm_states[vm_state] = 0
        self.vm_states[vm_state] += 1

        # Track number of instances in certain task_states
        task_state = instance.get('task_state')
        if task_state not in self.task_states:
            self.task_states[task_state] = 0
        self.task_states[task_state] += 1

        # Track number of instances by host_type
        os_type = instance.get('os_type')
        if os_type not in self.num_instances_by_os_type:
            self.num_instances_by_os_type[os_type] = 0
        self.num_instances_by_os_type[os_type] += 1

    @property
    def num_io_ops(self):
        num_builds = self.vm_states.get(vm_states.BUILDING, 0)
        num_rebuilds = self.vm_states.get(vm_states.REBUILDING, 0)
        num_resizes = self.vm_states.get(vm_states.RESIZING, 0)
        num_migrations = self.vm_states.get(vm_states.MIGRATING, 0)
        num_snapshots = self.task_states.get(task_states.IMAGE_SNAPSHOT, 0)
        num_backups = self.task_states.get(task_states.IMAGE_BACKUP, 0)
        return (num_builds + num_rebuilds + num_resizes + num_migrations +
                num_snapshots + num_backups)

    def __repr__(self):
        return "%s ram:%s disk:%s io_ops:%s instances:%s vm_type:%s" % (
                self.host, self.free_ram_mb, self.free_disk_mb,
                self.num_io_ops, self.num_instances, self.allowed_vm_type)


class RackspaceHostManager(host_manager.HostManager):
    host_state_cls = RackspaceHostState
