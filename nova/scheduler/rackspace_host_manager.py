# Copyright (c) 2011 Rackspace Hosting
# All Rights Reserved.

"""
Rackspace specific host manager.
"""

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


class RackspaceHostManager(host_manager.HostManager):

    def filter_hosts(self, hosts, filter_properties, filters=None):
        # 'hosts' is an iterator.. so we can only consume it once, but we
        # need to get the total number of hosts below.  Turn it back into
        # a list..
        hosts = [host for host in hosts]
        # Allow scheduler hint of '0z0ne_target_host' to force a build
        # to a specific host without any other checks.
        scheduler_hints = filter_properties.get('scheduler_hints') or {}
        target_host = scheduler_hints.get('0z0ne_target_host', None)
        if target_host:
            filtered_hosts = [host for host in hosts
                    if host.host == target_host]
            return filtered_hosts
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
