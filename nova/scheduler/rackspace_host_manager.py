# Copyright (c) 2011 Rackspace Hosting
# All Rights Reserved.

"""
Rackspace specific host manager.
"""

from nova.compute import task_states
from nova.compute import vm_states
from nova import flags
from nova.openstack.common import cfg
from nova.scheduler import host_manager

rax_host_manager_opts = [
    cfg.IntOpt('scheduler_spare_host_percentage',
            default=10,
            help='Percentage of hosts that should be reserved as spares')
    ]

FLAGS = flags.FLAGS
FLAGS.register_opts(rax_host_manager_opts)


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
        num_migrations = self.task_states.get(task_states.RESIZE_MIGRATING, 0)
        num_rebuilds = self.task_states.get(task_states.REBUILDING, 0)
        num_resizes = self.task_states.get(task_states.RESIZE_PREP, 0)
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

    def filter_hosts(self, hosts, filter_properties, filters=None):
        # 'hosts' is an iterator.. so we can only consume it once, but we
        # need to get the total number of hosts below.  Turn it back into
        # a list..
        hosts = [host for host in hosts]
        filtered_hosts = super(RackspaceHostManager, self).filter_hosts(
                iter(hosts), filter_properties, filters=filters)
        # Do some fudging for reserving some empty hosts for 30G instances
        empty_hosts = [x for x in filtered_hosts if not x.num_instances]
        num_empty_hosts = len(empty_hosts)
        if len(filtered_hosts) == num_empty_hosts:
            # Only empty_hosts match the filter, so return them
            return filtered_hosts
        # Otherwise, make sure we're reserving a certain amount of hosts
        pct_spare = FLAGS.scheduler_spare_host_percentage
        if not pct_spare:
            # Feature disabled
            return filtered_hosts
        total_hosts = len(hosts)
        target_spares = total_hosts / pct_spare
        if target_spares > num_empty_hosts:
            target_spares = num_empty_hosts
        for i in xrange(target_spares):
            filtered_hosts.remove(empty_hosts[i])
        return filtered_hosts
