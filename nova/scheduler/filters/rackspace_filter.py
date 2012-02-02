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

from nova.openstack.common import cfg
from nova import flags
from nova import log as logging
import abstract_filter

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

LOG = logging.getLogger('nova.scheduler.filter.rackspace_filter')


class RackspaceFilter(abstract_filter.AbstractHostFilter):
    """Rackspace hard rule filtering"""

    def _io_ops_filter(self, host_state):
        """Only return hosts that don't have too many IO Ops."""
        num_io_ops = host_state.num_io_ops
        return num_io_ops < FLAGS.rackspace_max_ios_per_host

    def _num_instances_filter(self, host_state):
        """Only return hosts that don't have too many instances."""
        num_instances = host_state.num_instances
        return num_instances < FLAGS.rackspace_max_instances_per_host

    def compute_host_passes(self, host_state, filter_properties):
        """Rackspace server best match hard rules."""
        if not self._io_ops_filter(host_state):
            return False
        if not self._num_instances_filter(host_state):
            return False
        return True
