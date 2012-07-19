# Copyright (c) 2012 OpenStack, LLC.
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
Weight cells by memory needed in a way that spreads instances.
"""

from nova.cells import weights
from nova import flags
from nova.openstack.common import cfg

flag_opts = [
        cfg.FloatOpt('cells_spread_by_ram_weighter_multiplier',
                default=10.0,
                help="How much to weight this weighter")
]

FLAGS = flags.FLAGS
FLAGS.register_opts(flag_opts)


class CellSpreadByRamWeighter(weights.BaseCellWeighter):
    """Weight cell by ram needed."""

    def fn_weight(self):
        return FLAGS.cells_spread_by_ram_weighter_multiplier

    def cell_weight(self, cell, weight_properties):
        """Override in a subclass to weigh a cell."""
        request_spec = weight_properties['request_spec']
        instance_type = request_spec['instance_type']
        memory_mb = instance_type['memory_mb']

        ram_free = cell.capacities.get('ram_free', {})
        units_by_mb = ram_free.get('units_by_mb', {})

        # Lower weights win, so we need to invert the values in
        # order to spread.
        return 1000 - units_by_mb.get(str(memory_mb), 0)
