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

from nova import flags
from nova.openstack.common import cfg
from nova.openstack.common import log as logging
from nova.scheduler import filters

flag_opts = [
    cfg.IntOpt('rackspace_max_ios_per_host',
            default=8,
            help=("Ignore hosts that have too many builds/resizes/snaps/"
                    "migrations")),
    cfg.IntOpt('rackspace_max_instances_per_host',
            default=50,
            help="Ignore hosts that have too many instances"),
    ]

FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts)

LOG = logging.getLogger(__name__)


class RackspaceFilter(filters.BaseHostFilter):
    """Rackspace hard rule filtering"""

    def _io_ops_filter(self, host_state):
        """Only return hosts that don't have too many IO Ops."""
        num_io_ops = host_state.num_io_ops
        max_ios = FLAGS.rackspace_max_ios_per_host
        passes = num_io_ops < max_ios
        if not passes:
            LOG.debug(_("%(host_state)s fails IOOps check: "
                    "Max IOs per host is set to %(max_ios)s"), locals())
        return passes

    def _num_instances_filter(self, host_state):
        """Only return hosts that don't have too many instances."""
        num_instances = host_state.num_instances
        max_instances = FLAGS.rackspace_max_instances_per_host
        passes = num_instances < max_instances
        if not passes:
            LOG.debug(_("%(host_state)s fails num_instances check: "
                    "Max instances per host is set to %(max_instances)s"),
                    locals())
        return passes

    def _ram_check_filter(self, host_state, filter_properties):
        """Somewhat duplicates RamFilter, but provides extra 1G reserve
        for instances < 30G.
        """
        instance_type = filter_properties.get('instance_type')
        requested_ram = instance_type['memory_mb']
        free_ram_mb = host_state.free_ram_mb
        if requested_ram < (30 * 1024):
            extra_reserve = 1024
        else:
            extra_reserve = 0
        passes = free_ram_mb >= (requested_ram + extra_reserve)
        if not passes:
            LOG.debug(_("%(host_state)s fails RAM check: "
                    "Need %(requested_ram)sMB + %(extra_reserve)sMB reserve"),
                    locals())
        return passes

    def _instance_type_filter(self, host_state, filter_properties):
        instance_type = filter_properties['instance_type']
        # This is a hack until we have better properties on flavors
        # see B-12428 for details (mdragon)
        if instance_type['id'] < 100:
            vm_type = 'pv'
        else:
            vm_type = 'hvm'
        if host_state.allowed_vm_type == 'all':
            return True
        return host_state.allowed_vm_type == vm_type

    def host_passes(self, host_state, filter_properties):
        """Rackspace server best match hard rules."""
        if not self._ram_check_filter(host_state, filter_properties):
            return False
        if not self._io_ops_filter(host_state):
            return False
        if not self._num_instances_filter(host_state):
            return False
        if not self._instance_type_filter(host_state, filter_properties):
            return False
        return True
